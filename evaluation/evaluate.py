"""RAGAs evaluation runner for RegIQ.

Reads the golden dataset, queries the live backend for each question, then
scores the system on four standard RAG quality metrics:

  • faithfulness        — is every claim in the answer supported by the
                          retrieved context?
  • answer_relevancy    — does the answer actually address the question?
  • context_precision   — are the retrieved chunks relevant to the question?
  • context_recall      — do the retrieved chunks cover the ground truth?

Outputs:
  - results/eval_summary.json   — aggregate scores + run metadata
  - results/eval_per_question.csv — per-question scores for drill-down

Usage:
  BACKEND_URL=http://localhost:8000 GEMINI_API_KEY=... python evaluate.py
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from datasets import Dataset
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
JUDGE_MODEL = os.getenv("RAGAS_JUDGE_MODEL", "gemini-2.5-flash")
EMBED_MODEL = os.getenv("RAGAS_EMBED_MODEL", "models/gemini-embedding-001")
DATASET_PATH = Path(os.getenv("DATASET_PATH", Path(__file__).parent / "golden_dataset.json"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", Path(__file__).parent / "results"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "180"))
SLEEP_BETWEEN = float(os.getenv("SLEEP_BETWEEN", "1.0"))


def load_dataset(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload.get("questions") or []
    if not questions:
        raise ValueError(f"No 'questions' in dataset at {path}")
    log.info("Loaded %d golden questions from %s", len(questions), path)
    return questions


def query_backend(item: dict) -> dict | None:
    payload: dict = {"question": item["question"]}
    if item.get("regulator"):
        payload["regulator"] = item["regulator"]
    log.info("[%s] querying backend", item["id"])
    try:
        resp = requests.post(f"{BACKEND_URL}/query", json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("[%s] backend request failed: %s", item["id"], e)
        return None
    return resp.json()


def build_eval_records(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Hit the backend and return (records_for_ragas, raw_results)."""
    records: list[dict] = []
    raw: list[dict] = []
    for item in items:
        result = query_backend(item)
        if result is None:
            continue
        contexts = [s.get("content", "") for s in result.get("sources", [])]
        if not contexts:
            log.warning("[%s] no sources returned — skipping for RAGAs", item["id"])
            continue
        records.append({
            "question": item["question"],
            "answer": result.get("answer", ""),
            "contexts": contexts,
            "ground_truth": item["ground_truth"],
        })
        raw.append({
            "id": item["id"],
            "intent_expected": item.get("intent"),
            "intent_actual": result.get("intent"),
            "regulator": item.get("regulator"),
            "grounded": result.get("grounded"),
            "metrics": result.get("metrics") or {},
            "answer": result.get("answer", ""),
            "ground_truth": item["ground_truth"],
            "sources": [s.get("source") for s in result.get("sources", [])],
        })
        time.sleep(SLEEP_BETWEEN)
    return records, raw


def run_ragas(records: list[dict]) -> dict:
    if not records:
        raise RuntimeError("No usable records to evaluate — check the backend and dataset")

    ds = Dataset.from_list(records)
    judge = LangchainLLMWrapper(
        ChatGoogleGenerativeAI(
            model=JUDGE_MODEL,
            google_api_key=GEMINI_API_KEY,
            temperature=0.0,
        )
    )
    embeddings = LangchainEmbeddingsWrapper(
        GoogleGenerativeAIEmbeddings(
            model=EMBED_MODEL,
            google_api_key=GEMINI_API_KEY,
        )
    )

    log.info("Running RAGAs on %d records with judge=%s", len(records), JUDGE_MODEL)
    scores = evaluate(
        ds,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge,
        embeddings=embeddings,
    )
    return scores


def write_outputs(scores, raw: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = OUTPUT_DIR / "eval_summary.json"
    per_q_path = OUTPUT_DIR / "eval_per_question.csv"

    aggregate = {k: float(v) for k, v in dict(scores).items()}
    df = scores.to_pandas()

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "backend_url": BACKEND_URL,
        "judge_model": JUDGE_MODEL,
        "embedding_model": EMBED_MODEL,
        "num_questions": len(df),
        "aggregate_scores": aggregate,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("Wrote %s", summary_path)

    metric_cols = [c for c in df.columns if c not in {"question", "answer", "contexts", "ground_truth"}]
    with per_q_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["id", "question", "intent_expected", "intent_actual", "grounded"]
            + metric_cols
            + ["latency_ms", "cost_usd", "tokens_input", "tokens_output"]
        )
        for i, row in df.iterrows():
            r = raw[i] if i < len(raw) else {}
            m = r.get("metrics") or {}
            writer.writerow(
                [
                    r.get("id", ""),
                    row.get("question", ""),
                    r.get("intent_expected", ""),
                    r.get("intent_actual", ""),
                    r.get("grounded", ""),
                ]
                + [row.get(c, "") for c in metric_cols]
                + [
                    m.get("total_ms", ""),
                    m.get("cost_usd", ""),
                    m.get("tokens_input", ""),
                    m.get("tokens_output", ""),
                ]
            )
    log.info("Wrote %s", per_q_path)

    print("\n=== RAGAs aggregate scores ===")
    for name, value in aggregate.items():
        print(f"  {name:25s} {value:.4f}")
    print(f"  questions scored          {len(df)}")


def main() -> int:
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY is required")
        return 2

    items = load_dataset(DATASET_PATH)
    records, raw = build_eval_records(items)
    if not records:
        log.error("Backend returned no usable answers; aborting")
        return 1
    scores = run_ragas(records)
    write_outputs(scores, raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
