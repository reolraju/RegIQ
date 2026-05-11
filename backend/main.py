import os
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
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from google.api_core.exceptions import ResourceExhausted
from sentence_transformers import CrossEncoder

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

RAG_PROMPT = ChatPromptTemplate.from_template("""You are a regulatory compliance expert specializing in Indian financial regulations issued by RBI and SEBI.

Answer the question using ONLY the context provided below. If the answer is not in the context, say "I could not find specific information about this in the available regulatory documents."

For every claim you make, cite the source document (use the "source" field from the context metadata).

Context:
{context}

Question: {question}

Answer (with citations):""")


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
        dense_hits = self._dense_search(query, flt, CANDIDATE_K)
        sparse_hits = self._bm25_search(query, flt, CANDIDATE_K)
        fused = self._rrf_fuse([dense_hits, sparse_hits], CANDIDATE_K)
        log.info("Retrieved %d dense, %d bm25, %d fused", len(dense_hits), len(sparse_hits), len(fused))
        return self._rerank(query, fused, TOP_K)


vectorstore: Optional[Chroma] = None
engine: Optional[RetrievalEngine] = None
llm: Optional[ChatGoogleGenerativeAI] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global vectorstore, engine, llm

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
    log.info("RAG pipeline ready")

    yield

    log.info("Shutting down")


app = FastAPI(title="RegIQ API", version="2.0.0", lifespan=lifespan)

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


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]


def _build_filter(req: QueryRequest) -> dict:
    clauses: list[dict] = []
    if req.regulator:
        clauses.append({"regulator": req.regulator.upper()})
    if req.date_from:
        clauses.append({"date": {"$gte": req.date_from}})
    if req.date_to:
        clauses.append({"date": {"$lte": req.date_to}})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _format_context(docs: list[Document]) -> str:
    parts = []
    for doc in docs:
        src = doc.metadata.get("source", "unknown")
        reg = doc.metadata.get("regulator", "")
        date = doc.metadata.get("date", "")
        ref = doc.metadata.get("reference", "")
        header_bits = [f"Source: {src}"]
        if reg:
            header_bits.append(f"Regulator: {reg}")
        if date:
            header_bits.append(f"Date: {date}")
        if ref:
            header_bits.append(f"Ref: {ref}")
        header = "[" + " | ".join(header_bits) + "]"
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if engine is None or llm is None:
        raise HTTPException(status_code=503, detail="Service still warming up")

    flt = _build_filter(req)
    source_docs = engine.retrieve(req.question, flt)

    if not source_docs:
        return QueryResponse(
            answer="I could not find specific information about this in the available regulatory documents.",
            sources=[],
        )

    chain = RAG_PROMPT | llm | StrOutputParser()
    try:
        answer = chain.invoke({
            "context": _format_context(source_docs),
            "question": req.question,
        })
    except ResourceExhausted as e:
        log.warning("Gemini quota exhausted: %s", e)
        raise HTTPException(status_code=503, detail="AI quota exhausted. Please try again later.")

    sources = [
        SourceChunk(
            content=doc.page_content[:500],
            source=doc.metadata.get("source", "unknown"),
            regulator=doc.metadata.get("regulator", "Unknown"),
            date=doc.metadata.get("date"),
            reference=doc.metadata.get("reference"),
        )
        for doc in source_docs
    ]

    return QueryResponse(answer=answer, sources=sources)
