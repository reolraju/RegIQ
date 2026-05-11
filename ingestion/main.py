import os
import logging
from pathlib import Path
from dotenv import load_dotenv

from langchain_community.document_loaders import TextLoader, PyMuPDFLoader
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


def load_documents(docs_dir: Path) -> list:
    docs = []
    for pattern, loader_cls in [("**/*.txt", TextLoader), ("**/*.pdf", PyMuPDFLoader)]:
        for path in docs_dir.glob(pattern):
            log.info("Loading %s", path)
            loader = loader_cls(str(path))
            loaded = loader.load()
            # Attach source metadata
            for doc in loaded:
                doc.metadata.setdefault("source", path.name)
                # Infer regulator from filename / content
                name_lower = path.name.lower()
                if "rbi" in name_lower:
                    doc.metadata["regulator"] = "RBI"
                elif "sebi" in name_lower:
                    doc.metadata["regulator"] = "SEBI"
                else:
                    doc.metadata["regulator"] = "Unknown"
            docs.extend(loaded)
    return docs


def split_documents(docs: list) -> list:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    log.info("Split into %d chunks", len(chunks))
    return chunks


def build_vectorstore(chunks: list) -> Chroma:
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=GEMINI_API_KEY,
    )
    log.info("Building ChromaDB collection '%s' at %s", COLLECTION, CHROMA_DIR)
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=COLLECTION,
        persist_directory=str(CHROMA_DIR),
    )
    log.info("Ingestion complete — %d chunks indexed", len(chunks))
    return vectorstore


def main():
    if not GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY is not set")

    docs = load_documents(DOCS_DIR)
    if not docs:
        raise FileNotFoundError(f"No documents found in {DOCS_DIR}")

    chunks = split_documents(docs)
    build_vectorstore(chunks)


if __name__ == "__main__":
    main()
