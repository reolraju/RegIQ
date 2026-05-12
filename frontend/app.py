import os
from datetime import date

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="RegIQ — Regulatory Intelligence",
    page_icon="📋",
    layout="wide",
)

st.title("📋 RegIQ — Regulatory Intelligence")
st.caption("Ask plain-English questions about RBI and SEBI regulations. Every answer is traced to source circulars.")

# Sidebar filters
with st.sidebar:
    st.header("Filters")
    regulator_filter = st.selectbox(
        "Regulator",
        options=["All", "RBI", "SEBI"],
        index=0,
    )

    st.subheader("Date range")
    use_date_filter = st.checkbox("Filter by issue date", value=False)
    date_from = st.date_input(
        "From",
        value=date(2015, 1, 1),
        min_value=date(2000, 1, 1),
        max_value=date.today(),
        disabled=not use_date_filter,
    )
    date_to = st.date_input(
        "To",
        value=date.today(),
        min_value=date(2000, 1, 1),
        max_value=date.today(),
        disabled=not use_date_filter,
    )

    st.divider()
    st.markdown(
        """
        **About**
        RegIQ uses a LangGraph agent on top of hybrid retrieval (dense + BM25
        with RRF fusion) and a cross-encoder reranker. An intent classifier
        routes each question to the right path — simple lookup, RBI ↔ SEBI
        comparison, or compliance checklist — and a hallucination guard
        verifies every claim against the source circulars before answering.
        """
    )

# Example questions
example_questions = [
    "What are the KYC requirements for digital lending?",
    "How do RBI and SEBI differ on outsourcing of financial services?",
    "Give me a compliance checklist for a digital lending app",
    "What is the minimum investment for accredited investors in AIFs?",
    "What are the cybersecurity reporting timelines for MIIs?",
]

INTENT_LABELS = {
    "simple_lookup": "Simple lookup",
    "comparison": "RBI ↔ SEBI comparison",
    "checklist": "Compliance checklist",
}

st.subheader("Example Questions")
cols = st.columns(len(example_questions))
selected_example = None
for i, q in enumerate(example_questions):
    if cols[i].button(q, use_container_width=True):
        selected_example = q

# Question input
question = st.text_area(
    "Your question",
    value=selected_example or "",
    height=80,
    placeholder="e.g. What are the KYC norms for digital lending apps?",
)

ask_btn = st.button("Ask RegIQ", type="primary", use_container_width=True)

if ask_btn:
    if not question.strip():
        st.warning("Please enter a question.")
    else:
        payload: dict = {"question": question.strip()}
        if regulator_filter != "All":
            payload["regulator"] = regulator_filter
        if use_date_filter:
            if date_from > date_to:
                st.error("'From' date must be on or before 'To' date.")
                st.stop()
            payload["date_from"] = date_from.isoformat()
            payload["date_to"] = date_to.isoformat()

        with st.spinner("Searching regulatory documents..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/query",
                    json=payload,
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.ConnectionError:
                st.error("Cannot connect to the backend. Make sure the backend service is running.")
                st.stop()
            except requests.exceptions.Timeout:
                st.error("Request timed out. The model may be busy — please try again.")
                st.stop()
            except requests.exceptions.HTTPError as e:
                st.error(f"Backend error: {e}")
                st.stop()

        intent = data.get("intent", "simple_lookup")
        product_type = data.get("product_type")
        grounded = data.get("grounded", True)

        badge_cols = st.columns([2, 2, 2, 4])
        badge_cols[0].metric("Route", INTENT_LABELS.get(intent, intent))
        if product_type:
            badge_cols[1].metric("Product", product_type)
        badge_cols[2].metric("Grounded", "Yes" if grounded else "Partial")
        if data.get("guard_notes"):
            badge_cols[3].caption(f"Guard: {data['guard_notes']}")

        st.subheader("Answer")
        st.markdown(data["answer"])

        if data.get("sources"):
            st.subheader("Source Documents")
            for i, src in enumerate(data["sources"], 1):
                title_bits = [src["source"], src.get("regulator", "Unknown")]
                if src.get("date"):
                    title_bits.append(src["date"])
                if src.get("reference"):
                    title_bits.append(src["reference"])
                with st.expander(f"[{i}] " + " — ".join(title_bits)):
                    st.text(src["content"])
        else:
            st.info("No source documents matched the current filters.")
