"""Company Policy Assistant - Streamlit chat app.

Run:  streamlit run app.py
"""

from __future__ import annotations

import hmac
import os
import time
import uuid

import streamlit as st

st.set_page_config(page_title="Policy Assistant", page_icon="📘", layout="centered")


def _secrets_to_env() -> None:
    """Allow keys to come from .streamlit/secrets.toml as well as environment variables / .env."""
    try:
        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float, bool)):
                os.environ.setdefault(key, str(value))
    except Exception:
        pass


_secrets_to_env()

from rag.audit import AuditLog  # noqa: E402  (imports after secrets are loaded into the environment)
from rag.config import get_settings  # noqa: E402
from rag.embeddings import Embedder  # noqa: E402
from rag.llm import LLM  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402
from rag.vectorstore import PineconeStore  # noqa: E402

s = get_settings()

# ------------------------------------------------------------------ human oversight: kill switch
if s.maintenance_mode:
    st.title("📘 Company Policy Assistant")
    st.warning("The assistant is temporarily unavailable for maintenance. "
               f"For urgent policy questions please contact {s.escalation_contact}.")
    st.stop()

# ------------------------------------------------------------------ security: optional access control
if s.app_password and not st.session_state.get("authenticated"):
    st.title("📘 Company Policy Assistant")
    with st.form("login"):
        password = st.text_input("Access password", type="password")
        if st.form_submit_button("Sign in"):
            if hmac.compare_digest(password, s.app_password):
                st.session_state.authenticated = True
                st.rerun()
            else:
                time.sleep(1)  # slow down guessing
                st.error("Incorrect password.")
    st.stop()

# ------------------------------------------------------------------ transparency: AI disclosure
st.title("📘 Company Policy Assistant")
st.caption("🤖 You are chatting with an AI assistant. Answers are generated from the company's policy "
           "documents and can be incomplete or wrong. Always check the cited sources. The assistant gives "
           f"information, not decisions or legal/medical advice. For anything important, contact "
           f"{s.escalation_contact}.")

# ------------------------------------------------------------------ credentials
groq_key = s.groq_api_key or st.sidebar.text_input("Groq API key", type="password")
pinecone_key = s.pinecone_api_key or st.sidebar.text_input("Pinecone API key", type="password")
if not (groq_key and pinecone_key):
    st.info("Add GROQ_API_KEY and PINECONE_API_KEY to a .env file (recommended) or enter them in the sidebar.")
    st.stop()


@st.cache_resource(show_spinner="Loading the embedding model...")
def get_embedder(model_name: str) -> Embedder:
    embedder = Embedder(model_name)
    _ = embedder.model  # load once
    return embedder


@st.cache_resource(show_spinner=False)
def get_store(api_key: str, index_name: str) -> PineconeStore:
    return PineconeStore(api_key, index_name)


@st.cache_resource(show_spinner=False)
def get_audit(log_dir: str) -> AuditLog:
    return AuditLog(log_dir)


@st.cache_data(ttl=300, show_spinner=False)
def get_manifest(api_key: str, index_name: str) -> dict:
    return get_store(api_key, index_name).load_manifest()


try:
    store = get_store(pinecone_key, s.index_name)
    manifest = get_manifest(pinecone_key, s.index_name)
except Exception as exc:
    st.error(f"Could not open the knowledge base '{s.index_name}': {exc}")
    st.stop()

# Reliability: refuse to run if the index was built with a different embedding model.
if manifest.get("embed_model") and manifest["embed_model"] != s.embed_model:
    st.error(f"The index was built with '{manifest['embed_model']}' but the app is configured for "
             f"'{s.embed_model}'. Set EMBED_MODEL to match, or re-run ingest.py --recreate.")
    st.stop()

audit = get_audit(s.log_dir)
llm = LLM(groq_key, s.llm_model, s.llm_base_url,
          safety_model=s.safety_model if s.enable_llm_moderation else "", temperature=s.llm_temperature)
pipeline = RAGPipeline(s, get_embedder(s.embed_model), store, llm, audit)

# ------------------------------------------------------------------ session state
st.session_state.setdefault("session_id", uuid.uuid4().hex[:12])
st.session_state.setdefault("messages", [])
st.session_state.setdefault("asked_at", [])
st.session_state.setdefault("feedback_logged", set())

# ------------------------------------------------------------------ sidebar: scope, transparency, privacy
documents = list(manifest.get("documents", {}).values())
categories = sorted({d["category"] for d in documents})
with st.sidebar:
    st.subheader("Search scope")
    selected = st.multiselect("Policy areas", categories, placeholder="All policy areas",
                              help="Leave empty to search every policy area.")

    if st.button("🗑️ Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    with st.expander("ℹ️ About this assistant"):
        st.markdown(
            f"- **Answer model:** `{s.llm_model}` (via Groq)\n"
            f"- **Search model:** `{s.embed_model}`\n"
            f"- **Safety checks:** rule-based"
            f"{' + `' + s.safety_model + '`' if s.enable_llm_moderation else ''}\n"
            f"- **Knowledge base:** `{s.index_name}`, last updated {manifest.get('updated_at', 'unknown')}\n"
            "- Answers use only the documents below and cite them as [1], [2]...\n"
            "- Topics that can significantly affect people are flagged for human review.")
        st.markdown(f"**Documents ({len(documents)})**")
        for d in sorted(documents, key=lambda d: (d["category"], d["title"])):
            st.markdown(f"- *{d['category']}* — {d['title']} ({d['pages']} pages, "
                        f"ingested {d['ingested_at'][:10]})")

    with st.expander("🔒 Privacy & data use"):
        st.markdown(
            "- Please don't enter personal data (IDs, bank details, health information) about yourself "
            "or others.\n"
            "- Personal data in questions is automatically removed before it is sent to the AI model "
            "or stored.\n"
            f"- Questions, answers and feedback are logged without your name for quality and safety "
            f"review, and deleted after {s.log_retention_days} days.\n"
            f"- Questions about your rights or data: {s.escalation_contact}.")

# ------------------------------------------------------------------ chat rendering


def render_assistant(msg: dict) -> None:
    st.markdown(msg["content"])
    for w in msg.get("warnings", []):
        st.caption(f"⚠️ {w}")
    if msg.get("status") == "answered":
        st.caption(f"Confidence: **{msg['confidence']}** · searched for: “{msg['search_query']}”")
    if msg.get("sources"):
        with st.expander(f"📄 Sources ({len(msg['sources'])})"):
            for src in msg["sources"]:
                st.markdown(f"**[{src['number']}] {src['title']}** — {src['section'] or 'General'} · "
                            f"page {src['page']} · *{src['category']}* · relevance {src['score']:.2f}")
                st.caption(src["text"])

    iid = msg.get("interaction_id")
    if not iid:
        return
    rating = st.feedback("thumbs", key=f"fb_{iid}")
    if rating is not None and iid not in st.session_state.feedback_logged:
        audit.log_feedback(iid, "up" if rating == 1 else "down", session_id=st.session_state.session_id)
        st.session_state.feedback_logged.add(iid)
    if rating == 0:
        comment = st.text_input("What was wrong? (optional, please don't include personal data)",
                                key=f"fbc_{iid}")
        if comment and st.button("Send feedback", key=f"fbs_{iid}"):
            audit.log_feedback(iid, "comment", comment=comment, session_id=st.session_state.session_id)
            st.success("Thanks - your feedback will be reviewed.")


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_assistant(msg)
        else:
            st.markdown(msg["content"])

# ------------------------------------------------------------------ new question
question = st.chat_input("Ask about a company policy...", max_chars=s.max_question_chars)
if question:
    now = time.time()
    st.session_state.asked_at = [t for t in st.session_state.asked_at if now - t < 60]
    with st.chat_message("user"):
        st.markdown(question)

    if len(st.session_state.asked_at) >= s.rate_limit_per_minute:
        with st.chat_message("assistant"):
            st.warning("You're asking questions very quickly. Please wait a minute and try again.")
        st.stop()
    st.session_state.asked_at.append(now)

    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("assistant"):
        with st.spinner("Searching the policies..."):
            ans = pipeline.ask(question, history, st.session_state.session_id, selected or None)
        msg = {
            "role": "assistant", "content": ans.text, "interaction_id": ans.interaction_id,
            "status": ans.status, "confidence": ans.confidence, "search_query": ans.search_query,
            "warnings": ans.warnings,
            "sources": [vars(src) for src in ans.sources],
        }
        st.session_state.messages.append(msg)
        render_assistant(msg)
