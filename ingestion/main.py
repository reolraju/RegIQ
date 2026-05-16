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


def _already_indexed_sources(vectorstore: Chroma) -> set[str]:
    """Return the set of source filenames already present in ChromaDB."""
    try:
        result = vectorstore.get(include=["metadatas"])
        return {m.get("source", "") for m in result.get("metadatas", []) if m}
    except Exception:
        return set()


def build_vectorstore_incremental(chunks: list[Document], embeddings) -> Chroma:
    """Add only chunks whose source file is not already in the collection.

    On a fresh deploy (empty /chroma_data) this behaves identically to a full
    build.  On subsequent runs it skips files that are already indexed, so we
    only pay embedding API cost for genuinely new circulars.
    """
    vectorstore = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )

    already_indexed = _already_indexed_sources(vectorstore)
    if already_indexed:
        log.info("%d source file(s) already indexed — skipping them", len(already_indexed))

    new_chunks = [c for c in chunks if c.metadata.get("source", "") not in already_indexed]

    if not new_chunks:
        log.info("Nothing new to index — ChromaDB is already up to date")
        return vectorstore

    log.info("Indexing %d new chunk(s) from %d chunk(s) total",
             len(new_chunks), len(chunks))
    vectorstore.add_documents(new_chunks)
    log.info("Incremental ingestion complete — added %d chunks", len(new_chunks))
    return vectorstore


def main():
    if not GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY is not set")

    docs = load_documents(DOCS_DIR)
    if not docs:
        raise FileNotFoundError(f"No documents found in {DOCS_DIR}")

    chunks = split_documents(docs)

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=GEMINI_API_KEY,
    )
    log.info("Connected to ChromaDB collection '%s' at %s", COLLECTION, CHROMA_DIR)
    build_vectorstore_incremental(chunks, embeddings)


if __name__ == "__main__":
    main()
