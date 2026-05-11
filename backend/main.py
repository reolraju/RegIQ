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
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from google.api_core.exceptions import ResourceExhausted

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHROMA_DIR = os.getenv("CHROMA_DIR", "/chroma_data")
COLLECTION = os.getenv("CHROMA_COLLECTION", "regiq")
TOP_K = int(os.getenv("TOP_K", "5"))

RAG_PROMPT = ChatPromptTemplate.from_template("""You are a regulatory compliance expert specializing in Indian financial regulations issued by RBI and SEBI.

Answer the question using ONLY the context provided below. If the answer is not in the context, say "I could not find specific information about this in the available regulatory documents."

For every claim you make, cite the source document (use the "source" field from the context metadata).

Context:
{context}

Question: {question}

Answer (with citations):""")

vectorstore: Optional[Chroma] = None
rag_chain = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global vectorstore, rag_chain

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

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.1,
    )

    retriever = vectorstore.as_retriever(search_kwargs={"k": TOP_K})

    def format_docs(docs):
        parts = []
        for doc in docs:
            src = doc.metadata.get("source", "unknown")
            reg = doc.metadata.get("regulator", "")
            header = f"[Source: {src}" + (f" | Regulator: {reg}]" if reg else "]")
            parts.append(f"{header}\n{doc.page_content}")
        return "\n\n---\n\n".join(parts)

    rag_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | RAG_PROMPT
        | llm
        | StrOutputParser()
    )
    log.info("RAG chain ready")

    yield

    log.info("Shutting down")


app = FastAPI(title="RegIQ API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str
    regulator: Optional[str] = None  # "RBI", "SEBI", or None for both


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

    # Build retriever with optional metadata filter
    search_kwargs: dict = {"k": TOP_K}
    if req.regulator:
        search_kwargs["filter"] = {"regulator": req.regulator.upper()}

    retriever = vectorstore.as_retriever(search_kwargs=search_kwargs)
    source_docs = retriever.invoke(req.question)

    def format_docs(docs):
        parts = []
        for doc in docs:
            src = doc.metadata.get("source", "unknown")
            reg = doc.metadata.get("regulator", "")
            header = f"[Source: {src}" + (f" | Regulator: {reg}]" if reg else "]")
            parts.append(f"{header}\n{doc.page_content}")
        return "\n\n---\n\n".join(parts)

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.1,
    )

    chain = (
        RAG_PROMPT
        | llm
        | StrOutputParser()
    )

    context_str = format_docs(source_docs)
    try:
        answer = chain.invoke({"context": context_str, "question": req.question})
    except ResourceExhausted as e:
        log.warning("Gemini quota exhausted: %s", e)
        raise HTTPException(status_code=503, detail="AI quota exhausted. Please try again later.")

    sources = [
        SourceChunk(
            content=doc.page_content[:500],
            source=doc.metadata.get("source", "unknown"),
            regulator=doc.metadata.get("regulator", "Unknown"),
        )
        for doc in source_docs
    ]

    return QueryResponse(answer=answer, sources=sources)
