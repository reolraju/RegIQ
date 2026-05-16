#!/bin/sh
# HuggingFace Spaces entrypoint:
#   1) one-shot ingestion (skipped if Chroma already populated)
#   2) FastAPI backend on :8000 (internal)
#   3) Streamlit frontend on :7860 (the public Spaces port)
set -eu

: "${GEMINI_API_KEY:?GEMINI_API_KEY must be set in the Space secrets}"

CHROMA_FLAG="${CHROMA_DIR}/.regiq_ingested"

if [ ! -f "$CHROMA_FLAG" ]; then
  echo "[start] running ingestion into $CHROMA_DIR ..."
  python /app/ingest_main.py
  touch "$CHROMA_FLAG"
else
  echo "[start] ingestion already done, skipping"
fi

echo "[start] launching backend on :8000 ..."
cd /app/backend
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "[start] waiting for backend health ..."
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/health > /dev/null; then
    echo "[start] backend healthy"
    break
  fi
  sleep 2
done

trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM

echo "[start] launching Streamlit on :${PORT} ..."
cd /app/frontend
exec streamlit run app.py \
  --server.port "${PORT}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
