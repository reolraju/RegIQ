import os
import re
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from langchain_core.documents import Document
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DOCS_DIR = Path(os.getenv("DOCS_DIR", "/app/sample_docs"))
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "/chroma_data"))
COLLECTION = os.getenv("CHROMA_COLLECTION", "regiq")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

DATE_PATTERN = re.compile(
    r"Date:\s*([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
REFERENCE_PATTERN = re.compile(r"Reference:\s*([A-Za-z0-9/\-\.]+)")
CIRCULAR_SEPARATOR = re.compile(r"\n-{3,}\n")


def _parse_date(text: str) -> str | None:
    m = DATE_PATTERN.search(text)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_reference(text: str) -> str | None:
    m = REFERENCE_PATTERN.search(text)
    return m.group(1) if m else None


def _infer_regulator(name: str, text: str) -> str:
    name_lower = name.lower()
    text_head = text[:500].lower()
    if "rbi" in name_lower or "reserve bank" in text_head:
        return "RBI"
    if "sebi" in name_lower or "securities and exchange board" in text_head:
        return "SEBI"
    return "Unknown"


def _build_metadata(source: str, text: str) -> dict:
    meta = {"source": source, "regulator": _infer_regulator(source, text)}
    date_iso = _parse_date(text)
    if date_iso:
        meta["date"] = date_iso
    ref = _parse_reference(text)
    if ref:
        meta["reference"] = ref
    return meta


def _load_text_file(path: Path) -> list[Document]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    segments = [s.strip() for s in CIRCULAR_SEPARATOR.split(raw) if s.strip()]
    docs = []
    for seg in segments:
        docs.append(Document(page_content=seg, metadata=_build_metadata(path.name, seg)))
    return docs


def _load_pdf_file(path: Path) -> list[Document]:
    loaded = PyMuPDFLoader(str(path)).load()
    full_text = "\n".join(p.page_content for p in loaded)
    base_meta = _build_metadata(path.name, full_text)
    for doc in loaded:
        doc.metadata.update(base_meta)
    return loaded


def load_documents(docs_dir: Path) -> list[Document]:
    docs: list[Document] = []
    for path in sorted(docs_dir.glob("**/*.txt")):
        log.info("Loading text: %s", path)
        docs.extend(_load_text_file(path))
    for path in sorted(docs_dir.glob("**/*.pdf")):
        log.info("Loading PDF: %s", path)
        docs.extend(_load_pdf_file(path))
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    log.info("Split into %d chunks", len(chunks))
    return chunks


def _sync_chromadb(vectorstore: Chroma, chunks: list[Document], current_sources: set[str]) -> None:
    """Keep ChromaDB in sync with the files currently on disk.

    1. Remove chunks whose source file has been pruned/deleted.
    2. Add chunks for source files not yet indexed.
    """
    result = vectorstore.get(include=["metadatas"])
    ids: list[str] = result.get("ids", [])
    metadatas: list[dict] = result.get("metadatas", []) or []

    # --- Step 1: remove stale chunks ---
    stale_ids = [
        doc_id for doc_id, meta in zip(ids, metadatas)
        if meta and meta.get("source", "") not in current_sources
    ]
    if stale_ids:
        vectorstore.delete(ids=stale_ids)
        log.info("Removed %d stale chunk(s) from ChromaDB (pruned files)", len(stale_ids))

    # --- Step 2: add new chunks ---
    indexed_sources = {
        meta.get("source", "") for meta in metadatas
        if meta and meta.get("source", "") not in
        {m.get("source", "") for doc_id, m in zip(ids, metadatas) if doc_id in stale_ids}
    }
    # Re-query after deletion to get the clean indexed set
    after = vectorstore.get(include=["metadatas"])
    indexed_sources = {m.get("source", "") for m in (after.get("metadatas") or []) if m}

    new_chunks = [c for c in chunks if c.metadata.get("source", "") not in indexed_sources]
    if new_chunks:
        vectorstore.add_documents(new_chunks)
        log.info("Added %d new chunk(s) to ChromaDB", len(new_chunks))
    else:
        log.info("ChromaDB already up to date — nothing to add")


def main():
    if not GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY is not set")

    docs = load_documents(DOCS_DIR)
    if not docs:
        raise FileNotFoundError(f"No documents found in {DOCS_DIR}")

    chunks = split_documents(docs)
    current_sources = {c.metadata.get("source", "") for c in chunks}

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=GEMINI_API_KEY,
    )

    vectorstore = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )
    log.info("Connected to ChromaDB collection '%s' at %s", COLLECTION, CHROMA_DIR)

    _sync_chromadb(vectorstore, chunks, current_sources)


if __name__ == "__main__":
    main()
