import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from google.api_core.exceptions import ResourceExhausted
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHROMA_DIR = os.getenv("CHROMA_DIR", "/chroma_data")
COLLECTION = os.getenv("CHROMA_COLLECTION", "regiq")
TOP_K = int(os.getenv("TOP_K", "5"))
FUSION_K = int(os.getenv("FUSION_K", "20"))
CROSS_ENCODER_MODEL = os.getenv("CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

RAG_PROMPT = ChatPromptTemplate.from_template(
    """You are a regulatory compliance expert specializing in Indian financial regulations issued by RBI and SEBI.

Answer the question using ONLY the context provided below. If the answer is not in the context, say "I could not find specific information about this in the available regulatory documents."

For every claim you make, cite the source document (use the "source" field from the context metadata).

Context:
{context}

Question: {question}

Answer (with citations):"""
)

vectorstore: Optional[Chroma] = None
all_chunks: list[Document] = []
bm25_index: Optional[BM25Okapi] = None
cross_encoder_model: Optional[CrossEncoder] = None


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def _bm25_retrieve(
    query: str,
    k: int,
    regulator: Optional[str],
    year_from: Optional[int],
    year_to: Optional[int],
) -> list[Document]:
    if bm25_index is None or not all_chunks:
        return []
    scores = bm25_index.get_scores(_tokenize(query))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    results: list[Document] = []
    for idx in ranked:
        if len(results) >= k:
            break
        meta = all_chunks[idx].metadata
        if regulator and meta.get("regulator", "").upper() != regulator.upper():
            continue
        if year_from and meta.get("circular_year", 0) < year_from:
            continue
        if year_to and meta.get("circular_year", 9999) > year_to:
            continue
        results.append(all_chunks[idx])
    return results


def _rrf_fuse(ranked_lists: list[list[Document]], rrf_k: int = 60) -> list[Document]:
    scores: dict[str, float] = {}
    doc_map: dict[str, Document] = {}
    for results in ranked_lists:
        for rank, doc in enumerate(results):
            doc_id = doc.page_content[:100]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)
            doc_map[doc_id] = doc
    return [doc_map[did] for did in sorted(scores, key=lambda x: scores[x], reverse=True)]


def _rerank(query: str, docs: list[Document], top_k: int) -> list[Document]:
    if not docs or cross_encoder_model is None:
        return docs[:top_k]
    pairs = [(query, doc.page_content) for doc in docs]
    scores = cross_encoder_model.predict(pairs)
    ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in ranked[:top_k]]


def _build_chroma_filter(
    regulator: Optional[str],
    year_from: Optional[int],
    year_to: Optional[int],
) -> Optional[dict]:
    conditions = []
    if regulator:
        conditions.append({"regulator": {"$eq": regulator.upper()}})
    if year_from:
        conditions.append({"circular_year": {"$gte": year_from}})
    if year_to:
        conditions.append({"circular_year": {"$lte": year_to}})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _format_docs(docs: list[Document]) -> str:
    parts = []
    for doc in docs:
        src = doc.metadata.get("source", "unknown")
        reg = doc.metadata.get("regulator", "")
        header = f"[Source: {src}" + (f" | Regulator: {reg}]" if reg else "]")
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global vectorstore, all_chunks, bm25_index, cross_encoder_model

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

    raw = vectorstore.get(include=["documents", "metadatas"])
    all_chunks = [
        Document(page_content=text, metadata=meta)
        for text, meta in zip(raw["documents"], raw["metadatas"])
    ]
    log.info("Loaded %d chunks for BM25", len(all_chunks))
    bm25_index = BM25Okapi([_tokenize(c.page_content) for c in all_chunks])
    log.info("BM25 index built")

    try:
        cross_encoder_model = CrossEncoder(CROSS_ENCODER_MODEL)
        log.info("Cross-encoder '%s' loaded", CROSS_ENCODER_MODEL)
    except Exception as exc:
        log.warning("Cross-encoder load failed (%s) — reranking disabled", exc)
        cross_encoder_model = None

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
    regulator: Optional[str] = None   # "RBI" | "SEBI" | None
    year_from: Optional[int] = None
    year_to: Optional[int] = None


class SourceChunk(BaseModel):
    content: str
    source: str
    regulator: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    chroma_filter = _build_chroma_filter(req.regulator, req.year_from, req.year_to)

    # Dense retrieval
    dense_kwargs: dict = {"k": FUSION_K}
    if chroma_filter:
        dense_kwargs["filter"] = chroma_filter
    dense_docs = vectorstore.similarity_search(req.question, **dense_kwargs)

    # BM25 retrieval
    bm25_docs = _bm25_retrieve(req.question, FUSION_K, req.regulator, req.year_from, req.year_to)

    # RRF fusion → cross-encoder rerank → top-k
    fused = _rrf_fuse([dense_docs, bm25_docs])
    final_docs = _rerank(req.question, fused[:FUSION_K], TOP_K)

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.1,
    )
    chain = RAG_PROMPT | llm | StrOutputParser()

    try:
        answer = chain.invoke({"context": _format_docs(final_docs), "question": req.question})
    except ResourceExhausted as exc:
        log.warning("Gemini quota exhausted: %s", exc)
        raise HTTPException(status_code=503, detail="AI quota exhausted. Please try again later.")

    sources = [
        SourceChunk(
            content=doc.page_content[:500],
            source=doc.metadata.get("source", "unknown"),
            regulator=doc.metadata.get("regulator", "Unknown"),
        )
        for doc in final_docs
    ]

    return QueryResponse(answer=answer, sources=sources)
