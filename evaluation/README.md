# RegIQ Evaluation

Golden dataset + RAGAs scoring for the RegIQ retrieval-and-answer pipeline.

## What's in here

| File | Purpose |
|---|---|
| `golden_dataset.json` | 20 hand-crafted question / ground-truth pairs covering both regulators and all three agent intents (simple lookup, comparison, checklist). |
| `evaluate.py` | Hits the live backend for each question, then scores the responses with RAGAs on **faithfulness**, **answer relevancy**, **context precision**, and **context recall**. |
| `Dockerfile` | Self-contained container if you'd rather not install RAGAs locally. |
| `results/` | Output directory — `eval_summary.json` (aggregate scores) and `eval_per_question.csv` (drill-down). Gitignored. |

## Run it

The backend must be up first (the script queries it as a black box).

```bash
# from the repo root
docker compose up --build -d

# then, with the backend healthy on :8000
cd evaluation
pip install -r requirements.txt
GEMINI_API_KEY=... BACKEND_URL=http://localhost:8000 python evaluate.py
```

Or run it containerised against the compose network:

```bash
docker run --rm \
  --network regiq_default \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e BACKEND_URL=http://backend:8000 \
  -v "$PWD/results:/app/results" \
  $(docker build -q .)
```

## What the scores mean

| Metric | Question it answers |
|---|---|
| **faithfulness** | Of the claims in the answer, what fraction are entailed by the retrieved chunks? Low = hallucination. |
| **answer_relevancy** | Does the answer actually address the question, or wander? |
| **context_precision** | Of the retrieved chunks, what fraction are actually relevant to the question? |
| **context_recall** | How much of the ground-truth answer is recoverable from the retrieved chunks? |

All scores are in [0, 1]; higher is better. RAGAs uses Gemini 2.5 Flash as the judge by default (set `RAGAS_JUDGE_MODEL` to override).
