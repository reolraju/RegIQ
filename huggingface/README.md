---
title: RegIQ
emoji: 📋
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: RBI & SEBI regulatory Q&A with sourced answers
---

# RegIQ on HuggingFace Spaces

This is the Spaces deployment of [RegIQ](https://github.com/reolraju/RegIQ), a
regulatory-intelligence app for the Indian financial regulators (RBI and
SEBI). Ask plain-English questions; every answer is grounded in source
circulars.

The container runs:
- a one-shot ingestion pass that indexes the sample circulars into a
  persistent ChromaDB on `/data/chroma`,
- a FastAPI backend on `:8000` (internal),
- a Streamlit UI on `:7860` (the public Space port).

## Setup

1. Duplicate this Space (or create a new Docker Space pointing at this repo).
2. Under **Settings → Repository secrets**, add `GEMINI_API_KEY` (get one at
   [aistudio.google.com](https://aistudio.google.com/app/apikey)).
3. (Optional) Add `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY`, and
   `LANGCHAIN_PROJECT` to enable LangSmith tracing.
4. Build & launch — first boot takes ~3 minutes (ingestion + cross-encoder
   model download). Subsequent restarts reuse the cache on `/data`.

## Architecture

See the [main README](https://github.com/reolraju/RegIQ#readme) for a deeper
breakdown of the LangGraph agent, hybrid retrieval, and evaluation
methodology.
