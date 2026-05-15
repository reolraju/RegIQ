# All-in-one HuggingFace Spaces image.
# Spaces gives you a single container exposing one port. We run ingestion
# (one-shot) -> FastAPI backend -> Streamlit frontend on 7860 inside the
# same container, supervised by a small shell script.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/data/huggingface/sentence-transformers \
    CHROMA_DIR=/data/chroma \
    DOCS_DIR=/app/sample_docs \
    PORT=7860

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY huggingface/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY ingestion/main.py /app/ingest_main.py
COPY ingestion/sample_docs/ /app/sample_docs/
COPY huggingface/start.sh /app/start.sh
RUN chmod +x /app/start.sh

# HuggingFace Spaces requires the listening port to be 7860.
EXPOSE 7860

# /data is HF Spaces' persistent volume; chroma + model cache live there so
# re-runs on the same Space don't re-ingest or re-download the cross-encoder.
RUN mkdir -p /data/chroma /data/huggingface

CMD ["/app/start.sh"]
