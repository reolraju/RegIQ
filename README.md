# RegIQ — Regulatory Intelligence System

Ask plain-English questions about RBI and SEBI regulations and get accurate, sourced answers traced back to specific circulars.

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Gemini 2.0 Flash |
| Embeddings | text-embedding-004 |
| Vector DB | ChromaDB |
| Sparse search | BM25 (Phase 2) |
| Reranker | cross-encoder/ms-marco-MiniLM (Phase 2) |
| Framework | LangChain + LangGraph (Phase 3) |
| Backend | FastAPI |
| Frontend | Streamlit |
| PDF parsing | PyMuPDF (Phase 2) |
| Evaluation | RAGAs (Phase 4) |
| Tracing | LangSmith (Phase 4) |
| Containers | Docker + Docker Compose |

## Quick Start

### Prerequisites
- Docker + Docker Compose
- A [Gemini API key](https://aistudio.google.com/app/apikey) (free tier works)

### Run

```bash
# 1. Clone the repo
git clone https://github.com/reolraju/RegIQ.git
cd RegIQ

# 2. Set your API key
cp .env.example .env
# Edit .env and paste your GEMINI_API_KEY

# 3. Launch all services
docker compose up --build
```

Open **http://localhost:8501** in your browser.

The ingestion service runs once on startup, indexes the sample docs into ChromaDB, then exits. The backend and frontend stay up.

## Project Structure

```
RegIQ/
├── ingestion/          # One-shot doc loader → chunker → embedder → ChromaDB
│   ├── main.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── sample_docs/    # Sample RBI + SEBI circulars (text)
├── backend/            # FastAPI — POST /query → RAG → Gemini → answer + sources
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/           # Streamlit UI
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
└── docker-compose.yml
```

## API

### `POST /query`

```json
{
  "question": "What are the KYC requirements for digital lending?",
  "regulator": "RBI"   // optional: "RBI" | "SEBI" | omit for both
}
```

Response:
```json
{
  "answer": "According to RBI Circular RBI/2022-23/111...",
  "sources": [
    {
      "content": "...excerpt from circular...",
      "source": "rbi_sample.txt",
      "regulator": "RBI"
    }
  ]
}
```

## Roadmap

- **Phase 1** ✅ MVP — sample docs, ChromaDB, FastAPI, Streamlit, Docker Compose
- **Phase 2** Real RBI/SEBI PDFs + hybrid BM25 + dense retrieval + cross-encoder reranking
- **Phase 3** LangGraph agent — intent router, cross-regulator comparison, hallucination guard, compliance checklist
- **Phase 4** RAGAs evaluation, cost/latency dashboard, GitHub Actions weekly ingestion, HuggingFace Spaces deploy
