import os
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
    st.divider()
    use_date_filter = st.checkbox("Filter by circular year")
    if use_date_filter:
        year_range = st.slider(
            "Year range",
            min_value=2000,
            max_value=2026,
            value=(2015, 2026),
            step=1,
        )
        year_from, year_to = year_range
    else:
        year_from, year_to = None, None
    st.divider()
    st.markdown(
        """
        **About**
        RegIQ uses hybrid RAG (dense + BM25 + cross-encoder reranking) to answer questions
        about Indian financial regulations. Answers are grounded in actual
        regulatory circulars, not the model's parametric memory.
        """
    )

# Example questions
example_questions = [
    "What are the KYC requirements for digital lending?",
    "What is the minimum investment for accredited investors in AIFs?",
    "How should cash withdrawals from PPIs be handled?",
    "What are the RPT disclosure thresholds under SEBI LODR?",
    "What are the cybersecurity reporting timelines for MIIs?",
]

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
        if year_from is not None:
            payload["year_from"] = year_from
        if year_to is not None:
            payload["year_to"] = year_to

        with st.spinner("Searching regulatory documents..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/query",
                    json=payload,
                    timeout=60,
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

        st.subheader("Answer")
        st.markdown(data["answer"])

        if data.get("sources"):
            st.subheader("Source Documents")
            for i, src in enumerate(data["sources"], 1):
                with st.expander(f"[{i}] {src['source']} — {src['regulator']}"):
                    st.text(src["content"])
