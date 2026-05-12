# RegIQ — Regulatory Intelligence System

Ask plain-English questions about RBI and SEBI regulations and get accurate, sourced answers traced back to specific circulars.

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
  "intent": "simple_lookup",
  "product_type": null,
  "grounded": true,
  "guard_notes": "all claims supported",
  "sources": [
    {
      "content": "...excerpt from circular...",
      "source": "rbi_sample.txt",
      "regulator": "RBI"
    }
  ]
}
```

`intent` is one of `simple_lookup`, `comparison`, or `checklist` — the
LangGraph agent picks the right path based on the question.

## Roadmap

---

### Phase 1 — MVP ✅
**Goal:** A working app you can type a question into and get a sourced answer.

| Step | What it does |
|---|---|
| Ingestion service | Reads sample `.txt` documents, splits them into overlapping chunks of ~1000 characters, embeds each chunk using Gemini `gemini-embedding-001`, and stores the vectors in ChromaDB. Runs once on startup then exits. |
| Backend service | FastAPI server with a single `POST /query` route. Takes a plain-English question, retrieves the top 5 most semantically similar chunks from ChromaDB, passes them as context to Gemini 2.5 Flash, and returns the answer with source citations. |
| Frontend service | Streamlit web UI with a text box, example question buttons, a regulator filter (RBI / SEBI / both), and collapsible source document expanders below each answer. |
| Docker Compose | Wires all 3 services together. Ingestion runs first and writes to a shared `chroma_data` volume. Backend and frontend start after and stay up. One command: `docker compose up --build`. |

---

### Phase 2 — Real Docs + Better Retrieval
**Goal:** Good enough to demo with real regulatory questions.

| Step | What it does |
|---|---|
| Real RBI/SEBI PDFs | Replace sample text files with actual regulatory PDFs. Use PyMuPDF to parse them — handles multi-column layouts, headers, footers, and tables better than plain text readers. |
| BM25 keyword search | Add a sparse retrieval layer alongside the dense vector search. BM25 is a classic keyword-matching algorithm — it catches exact terms like circular numbers, section references, and specific thresholds that semantic search can miss. |
| RRF fusion | Combine the results from dense search and BM25 using Reciprocal Rank Fusion (RRF). This merges two ranked lists into one by rewarding chunks that rank highly in both, giving more complete retrieval than either alone. |
| Cross-encoder reranking | Take the top 20 fused results and rerank them using `cross-encoder/ms-marco-MiniLM`. A cross-encoder reads the question and each chunk together (unlike embeddings which encode them separately), so it scores relevance much more accurately. Final top 5 are passed to the LLM. |
| Metadata filtering | Add date range and regulator filters to the UI and pass them as ChromaDB metadata filters so retrieval is scoped to the right documents. |

---

### Phase 3 — LangGraph Agent ✅
**Goal:** Multi-step reasoning that a simple RAG chain can't do.

| Step | What it does |
|---|---|
| LangGraph agent graph | Replace the single-step RAG chain with a stateful graph where each node is a reasoning step. LangGraph manages the flow between nodes, handles retries, and maintains state across steps. |
| Intent classifier node | First node in the graph. Classifies the question into categories — simple factual lookup, cross-regulator comparison, compliance checklist, etc. — and routes it to the right path in the graph. |
| Cross-regulator comparison node | Queries RBI and SEBI document stores separately, retrieves relevant chunks from each, then prompts the LLM to compare and contrast the two regulators' positions on the same topic. |
| Hallucination guard node | After the LLM generates an answer, this node checks every factual claim against the retrieved chunks. Flags or rewrites any claim that isn't grounded in the source documents. |
| Compliance checklist generator | Given a product type (e.g. "digital lending app" or "AIF"), this node runs multiple targeted retrievals and produces a structured, sourced checklist of all applicable regulatory requirements. |

---

### Phase 4 — Evaluation + Production Polish
**Goal:** A live URL and measurable proof the system works.

| Step | What it does |
|---|---|
| Golden dataset + RAGAs | Build 20 hand-crafted question/answer pairs covering both regulators. Run the RAGAs evaluation framework which scores the system on faithfulness, answer relevancy, context precision, and context recall. |
| Cost + latency dashboard | Add a metrics panel to the Streamlit UI showing per-query latency, estimated token cost, and retrieval time. Helps identify slow or expensive queries. |
| GitHub Actions weekly pipeline | A scheduled workflow that runs every week, checks for new circulars on the RBI and SEBI websites, downloads them, runs the ingestion pipeline, and updates the ChromaDB index automatically. |
| HuggingFace Spaces deploy | Package the app for HuggingFace Spaces (which supports Docker). Push the repo, configure secrets, and get a public URL. Record a demo video and write the final README. |
