import os
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from google.api_core.exceptions import ResourceExhausted
from sentence_transformers import CrossEncoder

from agent import build_agent
from metrics import MetricsTracker, current_metrics

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHROMA_DIR = os.getenv("CHROMA_DIR", "/chroma_data")
COLLECTION = os.getenv("CHROMA_COLLECTION", "regiq")
TOP_K = int(os.getenv("TOP_K", "5"))
CANDIDATE_K = int(os.getenv("CANDIDATE_K", "20"))
RRF_K = int(os.getenv("RRF_K", "60"))
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")


class RetrievalEngine:
    """Hybrid retriever: dense (Chroma) + sparse (BM25) → RRF → cross-encoder rerank."""

    def __init__(self, vectorstore: Chroma, reranker: CrossEncoder):
        self.vectorstore = vectorstore
        self.reranker = reranker
        self._all_docs: list[Document] = self._load_all_docs()
        log.info("Loaded %d chunks from Chroma for BM25 corpus", len(self._all_docs))

    def _load_all_docs(self) -> list[Document]:
        # Pull every chunk from Chroma so we can build an in-memory BM25 index.
        raw = self.vectorstore.get(include=["documents", "metadatas"])
        docs: list[Document] = []
        for content, meta in zip(raw.get("documents", []), raw.get("metadatas", [])):
            docs.append(Document(page_content=content or "", metadata=meta or {}))
        return docs

    @staticmethod
    def _doc_matches_filter(doc: Document, flt: dict) -> bool:
        for key, cond in flt.items():
            if key == "$and":
                if not all(RetrievalEngine._doc_matches_filter(doc, sub) for sub in cond):
                    return False
                continue
            value = doc.metadata.get(key)
            if isinstance(cond, dict):
                for op, target in cond.items():
                    if op == "$gte" and (value is None or value < target):
                        return False
                    if op == "$lte" and (value is None or value > target):
                        return False
                    if op == "$eq" and value != target:
                        return False
            else:
                if value != cond:
                    return False
        return True

    def _bm25_search(self, query: str, flt: dict, k: int) -> list[Document]:
        pool = [d for d in self._all_docs if self._doc_matches_filter(d, flt)] if flt else self._all_docs
        if not pool:
            return []
        retriever = BM25Retriever.from_documents(pool)
        retriever.k = k
        return retriever.invoke(query)

    def _dense_search(self, query: str, flt: dict, k: int) -> list[Document]:
        if flt:
            return self.vectorstore.similarity_search(query, k=k, filter=flt)
        return self.vectorstore.similarity_search(query, k=k)

    @staticmethod
    def _doc_key(doc: Document) -> tuple:
        return (doc.metadata.get("source", ""), doc.page_content)

    def _rrf_fuse(self, ranked_lists: list[list[Document]], k: int) -> list[Document]:
        """Reciprocal Rank Fusion: score = sum(1 / (RRF_K + rank))."""
        scores: dict[tuple, float] = {}
        keep: dict[tuple, Document] = {}
        for docs in ranked_lists:
            for rank, doc in enumerate(docs):
                key = self._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
                keep.setdefault(key, doc)
        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [keep[key] for key, _ in ordered[:k]]

    def _rerank(self, query: str, docs: list[Document], k: int) -> list[Document]:
        if not docs:
            return []
        pairs = [(query, d.page_content) for d in docs]
        scores = self.reranker.predict(pairs)
        scored = sorted(zip(scores, docs), key=lambda x: float(x[0]), reverse=True)
        return [d for _, d in scored[:k]]

    def retrieve(self, query: str, flt: Optional[dict] = None) -> list[Document]:
        flt = flt or {}
        t0 = time.perf_counter()
        dense_hits = self._dense_search(query, flt, CANDIDATE_K)
        sparse_hits = self._bm25_search(query, flt, CANDIDATE_K)
        fused = self._rrf_fuse([dense_hits, sparse_hits], CANDIDATE_K)
        result = self._rerank(query, fused, TOP_K)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        tracker = current_metrics.get()
        if tracker is not None:
            tracker.add_retrieval(elapsed_ms)
        log.info(
            "Retrieved %d dense, %d bm25, %d fused (%.1fms)",
            len(dense_hits), len(sparse_hits), len(fused), elapsed_ms,
        )
        return result


vectorstore: Optional[Chroma] = None
engine: Optional[RetrievalEngine] = None
llm: Optional[ChatGoogleGenerativeAI] = None
agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global vectorstore, engine, llm, agent

    if not GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY is not set")

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=GEMINI_API_KEY,
    )
    vectorstore = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
    )
    log.info("Connected to ChromaDB collection '%s'", COLLECTION)

    log.info("Loading cross-encoder: %s", RERANK_MODEL)
    reranker = CrossEncoder(RERANK_MODEL)

    engine = RetrievalEngine(vectorstore, reranker)

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.1,
    )

    agent = build_agent(engine, llm)
    log.info("LangGraph agent compiled — RAG pipeline ready")

    yield

    log.info("Shutting down")


app = FastAPI(title="RegIQ API", version="4.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str
    regulator: Optional[str] = None  # "RBI", "SEBI", or None for both
    date_from: Optional[str] = None  # ISO YYYY-MM-DD
    date_to: Optional[str] = None    # ISO YYYY-MM-DD


class SourceChunk(BaseModel):
    content: str
    source: str
    regulator: str
    date: Optional[str] = None
    reference: Optional[str] = None


class QueryMetrics(BaseModel):
    total_ms: float
    retrieval_ms: float
    llm_ms: float
    retrieval_calls: int
    llm_calls: int
    tokens_input: int
    tokens_output: int
    cost_usd: float


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]
    intent: str
    product_type: Optional[str] = None
    grounded: bool = True
    guard_notes: Optional[str] = None
    metrics: Optional[QueryMetrics] = None


def _to_source_chunks(docs: list[Document]) -> list[SourceChunk]:
    seen: set[tuple] = set()
    out: list[SourceChunk] = []
    for doc in docs:
        meta = doc.metadata or {}
        key = (meta.get("source", ""), doc.page_content[:200])
        if key in seen:
            continue
        seen.add(key)
        out.append(SourceChunk(
            content=doc.page_content[:500],
            source=meta.get("source", "unknown"),
            regulator=meta.get("regulator", "Unknown"),
            date=meta.get("date"),
            reference=meta.get("reference"),
        ))
    return out


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if agent is None:
        raise HTTPException(status_code=503, detail="Service still warming up")

    initial_state = {
        "question": req.question.strip(),
        "regulator": req.regulator,
        "date_from": req.date_from,
        "date_to": req.date_to,
    }

    tracker = MetricsTracker()
    token = current_metrics.set(tracker)
    try:
        final_state = agent.invoke(
            initial_state,
            config={"callbacks": [tracker]},
        )
    except ResourceExhausted as e:
        log.warning("Gemini quota exhausted: %s", e)
        raise HTTPException(status_code=503, detail="AI quota exhausted. Please try again later.")
    finally:
        current_metrics.reset(token)

    answer = final_state.get("final_answer") or final_state.get("draft_answer") or \
        "I could not find specific information about this in the available regulatory documents."
    sources = _to_source_chunks(final_state.get("docs", []))
    snapshot = tracker.snapshot()
    log.info(
        "Query done: total=%sms retrieval=%sms llm=%sms tokens=%d/%d cost=$%.6f",
        snapshot["total_ms"], snapshot["retrieval_ms"], snapshot["llm_ms"],
        snapshot["tokens_input"], snapshot["tokens_output"], snapshot["cost_usd"],
    )

    return QueryResponse(
        answer=answer,
        sources=sources,
        intent=final_state.get("intent", "simple_lookup"),
        product_type=final_state.get("product_type"),
        grounded=bool(final_state.get("grounded", True)),
        guard_notes=final_state.get("guard_notes") or None,
        metrics=QueryMetrics(**snapshot),
    )
