# RegIQ Roadmap

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

### Phase 4 — Evaluation + Production Polish ✅
**Goal:** A live URL and measurable proof the system works.

| Step | What it does |
|---|---|
| Golden dataset + RAGAs | 20 hand-crafted Q&A pairs in [`evaluation/golden_dataset.json`](../evaluation/golden_dataset.json) covering both regulators and all three agent intents. [`evaluation/evaluate.py`](../evaluation/evaluate.py) hits the live backend and scores answers on faithfulness, answer relevancy, context precision, and context recall using RAGAs with Gemini 2.5 Flash as the judge. |
| Cost + latency dashboard | Every `/query` response now carries a `metrics` object (per-stage timings, token counts, USD cost estimate). The Streamlit UI surfaces these as a Performance panel per query plus a Session-totals widget in the sidebar. |
| GitHub Actions weekly pipeline | [`.github/workflows/weekly_ingestion.yml`](../.github/workflows/weekly_ingestion.yml) runs every Monday, calls [`scripts/fetch_circulars.py`](../scripts/fetch_circulars.py) to discover new RBI/SEBI PDFs, stages them under `ingestion/sample_docs/`, and opens a PR. Merging the PR triggers re-ingestion on the next deploy. |
| HuggingFace Spaces deploy | [`huggingface/`](../huggingface) packages the three services into a single Spaces-compatible container (ingestion → backend → Streamlit on `:7860`). [`.github/workflows/deploy_huggingface.yml`](../.github/workflows/deploy_huggingface.yml) pushes the staged tree to a Space on every `main` push, given `HF_TOKEN` and `HF_SPACE` secrets. |
| LangSmith tracing | Setting `LANGCHAIN_TRACING_V2=true` plus an API key (see `.env.example`) captures every LangGraph node, retrieval, and LLM call in LangSmith for debugging. |
| RAGAs CI | [`.github/workflows/evaluation.yml`](../.github/workflows/evaluation.yml) re-runs the golden dataset on every PR that touches the backend or evaluation code and posts the aggregate scores as a PR comment. |
