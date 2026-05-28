# RegIQ — Regulatory Intelligence System

Ask plain-English questions about RBI and SEBI regulations and get accurate, sourced answers traced back to specific circulars.

**Live demo:** https://huggingface.co/spaces/charles43/regiq

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Gemini 2.5 Flash |
| Embeddings | gemini-embedding-001 |
| Vector DB | ChromaDB |
| Sparse search | BM25 (Phase 2) |
| Reranker | cross-encoder/ms-marco-MiniLM (Phase 2) |
| Framework | LangChain + LangGraph |
| Backend | FastAPI |
| Frontend | Static React/HTML SPA served by FastAPI |
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
│   └── sample_docs/    # Sample RBI + SEBI circulars (text + PDFs)
├── backend/            # FastAPI — POST /query → LangGraph agent → answer + sources
│   ├── main.py
│   ├── agent.py
│   ├── metrics.py      # per-request token / cost / latency tracker
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/           # FastAPI server + React/HTML SPA (RegIQ design system)
│   ├── app.py          # serves static files + proxies POST /api/query → backend
│   ├── static/         # index.html, JSX components, CSS tokens, brand assets
│   ├── requirements.txt
│   └── Dockerfile
├── evaluation/         # Golden dataset + RAGAs scoring (Phase 4)
│   ├── golden_dataset.json
│   ├── evaluate.py
│   ├── Dockerfile
│   └── README.md
├── huggingface/        # Single-container HF Spaces deployment (Phase 4)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── start.sh
│   └── README.md
├── scripts/            # Weekly circular sync (Phase 4)
│   ├── fetch_circulars.py
│   └── requirements.txt
├── .github/workflows/  # Weekly sync, RAGAs CI, HF Spaces deploy
└── docker-compose.yml
```

## API

### `POST /query`

```json
{
  "question": "What are the KYC requirements for digital lending?",
  "regulator": "RBI",        // optional: "RBI" | "SEBI" | omit for both
  "date_from": "2020-01-01", // optional
  "date_to":   "2024-12-31"  // optional
}
```

Response:
```json
{
  "answer": "According to RBI Circular RBI/2022-23/111...",
  "intent": "simple_lookup",
  "product_type": null,
  "grounded": true,
  "guard_notes": "all claims supported",
  "sources": [
    {
      "content": "...excerpt from circular...",
      "source": "rbi_sample.txt",
      "regulator": "RBI",
      "date": "2022-09-02",
      "reference": "RBI/2022-23/111"
    }
  ],
  "metrics": {
    "total_ms": 4231.5,
    "retrieval_ms": 612.3,
    "llm_ms": 3504.8,
    "retrieval_calls": 1,
    "llm_calls": 3,
    "tokens_input": 4821,
    "tokens_output": 312,
    "cost_usd": 0.002227
  }
}
```

`intent` is one of `simple_lookup`, `comparison`, or `checklist` — the
LangGraph agent picks the right path based on the question. `metrics` is
returned on every response and powers the Streamlit cost & latency dashboard.

## Evaluation

```bash
# from the repo root, with `docker compose up -d` running
cd evaluation
pip install -r requirements.txt
GEMINI_API_KEY=... BACKEND_URL=http://localhost:8000 python evaluate.py
```

Outputs land in `evaluation/results/`:
- `eval_summary.json` — aggregate RAGAs scores.
- `eval_per_question.csv` — per-question scores plus the backend's own
  latency / cost numbers, so you can see at a glance which questions are
  slow, expensive, or weakly grounded.

See [`evaluation/README.md`](evaluation/README.md) for details.

## Deploying to HuggingFace Spaces

1. Create a new **Docker** Space at huggingface.co.
2. Set `GEMINI_API_KEY` under **Settings → Repository secrets**.
3. Either:
   - point the Space at this repo, copying `huggingface/Dockerfile` and `huggingface/README.md` to the root, or
   - configure `HF_TOKEN` + `HF_SPACE` secrets on this GitHub repo and let the [`deploy_huggingface`](.github/workflows/deploy_huggingface.yml) workflow stage and push for you on every `main` push.

The container persists ChromaDB and the cross-encoder cache to `/data` so warm restarts skip ingestion and the model download.
