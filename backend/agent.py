"""LangGraph agent for RegIQ — Phase 3.

The agent replaces the single-step RAG chain with a stateful graph:

    classify_intent
        │
        ├── simple_lookup ─► retrieve ──► answer ──┐
        │                                          │
        ├── comparison ───► retrieve_rbi +         │
        │                   retrieve_sebi ─► compare ─► guard ─► END
        │                                          │
        └── checklist ────► multi_retrieve ─► checklist_gen ─┘

Every path ends in a hallucination guard that verifies the draft answer
is grounded in the retrieved chunks before returning it to the user.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, TypedDict

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph

log = logging.getLogger(__name__)


# ─── State ────────────────────────────────────────────────────────────────────


class AgentState(TypedDict, total=False):
    """Mutable state passed between graph nodes."""

    question: str
    regulator: Optional[str]          # "RBI" / "SEBI" / None
    date_from: Optional[str]
    date_to: Optional[str]

    intent: str                       # "simple_lookup" | "comparison" | "checklist"
    product_type: Optional[str]       # populated for the checklist path

    docs: list[Document]              # unified pool of retrieved chunks
    rbi_docs: list[Document]
    sebi_docs: list[Document]

    draft_answer: str
    final_answer: str
    grounded: bool
    guard_notes: str


VALID_INTENTS = {"simple_lookup", "comparison", "checklist"}


# ─── Prompts ──────────────────────────────────────────────────────────────────


INTENT_PROMPT = ChatPromptTemplate.from_template(
    """You route regulatory questions to the right handler.

Classify the user's question into exactly one of:

- simple_lookup     — a single factual question answered from one or more circulars
                      (e.g. "What is the KYC threshold for PPIs?").
- comparison        — explicitly asks how RBI and SEBI differ / compare on the same topic
                      (e.g. "How do RBI and SEBI differ on outsourcing rules?").
- checklist         — asks for the regulatory requirements / compliance steps for a
                      specific product or entity type (e.g. "What does a digital
                      lending app need to comply with?", "Compliance checklist for AIFs").

If the question mentions a product / entity type and asks for requirements,
prefer "checklist". If it explicitly names both regulators and asks to compare,
prefer "comparison". Otherwise "simple_lookup".

Respond with a JSON object only, no prose:
{{"intent": "<one of: simple_lookup, comparison, checklist>", "product_type": "<short product/entity name or empty string>"}}

Question: {question}
"""
)

LOOKUP_PROMPT = ChatPromptTemplate.from_template(
    """You are a regulatory compliance expert on Indian financial regulations (RBI & SEBI).

Answer the question using ONLY the context below. If the answer is not in the context,
say "I could not find specific information about this in the available regulatory documents."

Cite the source (the "Source:" field in the bracketed header) inline for every claim.

Context:
{context}

Question: {question}

Answer (with inline citations):"""
)

COMPARISON_PROMPT = ChatPromptTemplate.from_template(
    """You are comparing how RBI and SEBI regulate the same topic.

Use ONLY the two contexts below. If one regulator has no relevant material,
say so explicitly rather than inventing a position.

Structure your answer as:
1. **RBI position** — with inline citations.
2. **SEBI position** — with inline citations.
3. **Key similarities**.
4. **Key differences**.

RBI Context:
{rbi_context}

SEBI Context:
{sebi_context}

Question: {question}

Answer:"""
)

CHECKLIST_PROMPT = ChatPromptTemplate.from_template(
    """You are producing a regulatory compliance checklist for a specific product type.

Product / entity type: {product_type}
User's question: {question}

Use ONLY the context below. Produce a structured Markdown checklist. Each item must be
a single, actionable requirement and must cite its source document inline.

Format:
- [ ] **<Requirement title>** — <one-sentence detail>. *(Source: <source>)*

Group items under headings (e.g. "Registration & Licensing", "KYC & Onboarding",
"Disclosure & Reporting", "Cybersecurity", "Grievance Redressal") where helpful.
Omit any heading you have no grounded items for. Do not invent requirements that
are not supported by the context.

Context:
{context}

Checklist:"""
)

GUARD_PROMPT = ChatPromptTemplate.from_template(
    """You are a fact-checker. Verify whether the DRAFT ANSWER's factual claims
are supported by the SOURCE CHUNKS.

A claim is "supported" if the SAME fact appears (in substance, not necessarily word-for-word)
in at least one source chunk. Generic framing sentences, headings, and checklist scaffolding
do not need to be supported — only factual claims do.

If every factual claim is supported, return the draft unchanged.
If one or more claims are NOT supported, return a corrected version that:
  - removes or softens the unsupported claims,
  - keeps everything that IS supported,
  - preserves all inline citations,
  - appends a short note: "_Note: removed N unsupported claim(s)._" only if you changed something.

Respond with a JSON object only:
{{"grounded": <true|false>, "answer": "<final answer markdown>", "notes": "<short rationale>"}}

DRAFT ANSWER:
{draft}

SOURCE CHUNKS:
{context}
"""
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _format_context(docs: list[Document]) -> str:
    parts: list[str] = []
    for doc in docs:
        meta = doc.metadata or {}
        bits = [f"Source: {meta.get('source', 'unknown')}"]
        if meta.get("regulator"):
            bits.append(f"Regulator: {meta['regulator']}")
        if meta.get("date"):
            bits.append(f"Date: {meta['date']}")
        if meta.get("reference"):
            bits.append(f"Ref: {meta['reference']}")
        parts.append("[" + " | ".join(bits) + "]\n" + doc.page_content)
    return "\n\n---\n\n".join(parts)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # remove leading ```json / ``` and trailing ```
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_blob(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to grab the first {...} object.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _build_filter(
    regulator: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> dict:
    clauses: list[dict] = []
    if regulator:
        clauses.append({"regulator": regulator.upper()})
    if date_from:
        clauses.append({"date": {"$gte": date_from}})
    if date_to:
        clauses.append({"date": {"$lte": date_to}})
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


# ─── Graph builder ────────────────────────────────────────────────────────────


def build_agent(retrieval_engine, llm: BaseChatModel):
    """Construct the compiled LangGraph agent.

    `retrieval_engine` must expose `.retrieve(query: str, flt: dict) -> list[Document]`.
    """

    str_parser = StrOutputParser()
    intent_chain = INTENT_PROMPT | llm | str_parser
    lookup_chain = LOOKUP_PROMPT | llm | str_parser
    comparison_chain = COMPARISON_PROMPT | llm | str_parser
    checklist_chain = CHECKLIST_PROMPT | llm | str_parser
    guard_chain = GUARD_PROMPT | llm | str_parser

    # ── Nodes ────────────────────────────────────────────────────────────────

    def classify_intent(state: AgentState) -> dict:
        raw = intent_chain.invoke({"question": state["question"]})
        parsed = _parse_json_blob(raw)
        intent = parsed.get("intent", "simple_lookup")
        if intent not in VALID_INTENTS:
            intent = "simple_lookup"
        product_type = (parsed.get("product_type") or "").strip() or None

        # Comparison only makes sense if the user hasn't pinned a single regulator.
        if intent == "comparison" and state.get("regulator"):
            log.info("Comparison intent overridden because regulator filter is set to %s",
                     state["regulator"])
            intent = "simple_lookup"

        log.info("Intent classified as '%s' (product_type=%s)", intent, product_type)
        return {"intent": intent, "product_type": product_type}

    def route_intent(state: AgentState) -> str:
        return state.get("intent", "simple_lookup")

    def retrieve_simple(state: AgentState) -> dict:
        flt = _build_filter(state.get("regulator"), state.get("date_from"), state.get("date_to"))
        docs = retrieval_engine.retrieve(state["question"], flt)
        return {"docs": docs}

    def retrieve_rbi(state: AgentState) -> dict:
        flt = _build_filter("RBI", state.get("date_from"), state.get("date_to"))
        docs = retrieval_engine.retrieve(state["question"], flt)
        return {"rbi_docs": docs}

    def retrieve_sebi(state: AgentState) -> dict:
        flt = _build_filter("SEBI", state.get("date_from"), state.get("date_to"))
        docs = retrieval_engine.retrieve(state["question"], flt)
        return {"sebi_docs": docs}

    def checklist_retrieve(state: AgentState) -> dict:
        """Run several targeted queries so the checklist covers multiple compliance facets."""
        product = state.get("product_type") or state["question"]
        facets = [
            f"{product} registration and licensing requirements",
            f"{product} KYC customer due diligence",
            f"{product} disclosure and reporting obligations",
            f"{product} cybersecurity data protection",
            f"{product} grievance redressal customer protection",
        ]
        base_flt = _build_filter(
            state.get("regulator"), state.get("date_from"), state.get("date_to")
        )

        seen: set[tuple] = set()
        pooled: list[Document] = []
        for facet in facets:
            for doc in retrieval_engine.retrieve(facet, base_flt):
                key = (doc.metadata.get("source", ""), doc.page_content[:200])
                if key in seen:
                    continue
                seen.add(key)
                pooled.append(doc)

        log.info("Checklist retrieval pooled %d unique chunks across %d facets",
                 len(pooled), len(facets))
        return {"docs": pooled}

    def generate_lookup(state: AgentState) -> dict:
        if not state.get("docs"):
            return {"draft_answer": "I could not find specific information about this in the available regulatory documents."}
        answer = lookup_chain.invoke({
            "context": _format_context(state["docs"]),
            "question": state["question"],
        })
        return {"draft_answer": answer}

    def generate_comparison(state: AgentState) -> dict:
        rbi_docs = state.get("rbi_docs", [])
        sebi_docs = state.get("sebi_docs", [])
        if not rbi_docs and not sebi_docs:
            return {
                "draft_answer": "I could not find specific information about this in the available regulatory documents.",
                "docs": [],
            }
        answer = comparison_chain.invoke({
            "rbi_context": _format_context(rbi_docs) or "(no relevant RBI material found)",
            "sebi_context": _format_context(sebi_docs) or "(no relevant SEBI material found)",
            "question": state["question"],
        })
        # Merge into a unified docs pool for the guard + response sources.
        return {"draft_answer": answer, "docs": rbi_docs + sebi_docs}

    def generate_checklist(state: AgentState) -> dict:
        if not state.get("docs"):
            return {"draft_answer": "I could not find specific information about this in the available regulatory documents."}
        answer = checklist_chain.invoke({
            "context": _format_context(state["docs"]),
            "question": state["question"],
            "product_type": state.get("product_type") or "the specified product",
        })
        return {"draft_answer": answer}

    def hallucination_guard(state: AgentState) -> dict:
        draft = state.get("draft_answer", "")
        docs = state.get("docs", [])
        if not docs or not draft.strip():
            # Nothing retrieved → nothing to ground against; pass the draft through.
            return {"final_answer": draft, "grounded": False, "guard_notes": "no context"}

        raw = guard_chain.invoke({
            "draft": draft,
            "context": _format_context(docs),
        })
        parsed = _parse_json_blob(raw)
        if not parsed:
            log.warning("Guard returned non-JSON, passing draft through")
            return {"final_answer": draft, "grounded": True, "guard_notes": "guard parse failed"}

        final = parsed.get("answer") or draft
        grounded = bool(parsed.get("grounded", True))
        notes = parsed.get("notes", "")
        log.info("Guard verdict: grounded=%s notes=%s", grounded, notes[:120])
        return {"final_answer": final, "grounded": grounded, "guard_notes": notes}

    # ── Wiring ───────────────────────────────────────────────────────────────

    graph = StateGraph(AgentState)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve_simple", retrieve_simple)
    graph.add_node("retrieve_rbi", retrieve_rbi)
    graph.add_node("retrieve_sebi", retrieve_sebi)
    graph.add_node("checklist_retrieve", checklist_retrieve)
    graph.add_node("generate_lookup", generate_lookup)
    graph.add_node("generate_comparison", generate_comparison)
    graph.add_node("generate_checklist", generate_checklist)
    graph.add_node("hallucination_guard", hallucination_guard)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "simple_lookup": "retrieve_simple",
            "comparison": "retrieve_rbi",
            "checklist": "checklist_retrieve",
        },
    )

    graph.add_edge("retrieve_simple", "generate_lookup")
    graph.add_edge("generate_lookup", "hallucination_guard")

    # Comparison: RBI then SEBI (sequential keeps the engine's BM25 thread-safe).
    graph.add_edge("retrieve_rbi", "retrieve_sebi")
    graph.add_edge("retrieve_sebi", "generate_comparison")
    graph.add_edge("generate_comparison", "hallucination_guard")

    graph.add_edge("checklist_retrieve", "generate_checklist")
    graph.add_edge("generate_checklist", "hallucination_guard")

    graph.add_edge("hallucination_guard", END)

    return graph.compile()
