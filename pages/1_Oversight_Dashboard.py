"""Human oversight dashboard: review flagged conversations, user feedback and blocked requests.

Only available when ADMIN_PASSWORD is set.
"""

from __future__ import annotations

import hmac
import os
import time

import pandas as pd
import streamlit as st

try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float, bool)):
            os.environ.setdefault(_k, str(_v))
except Exception:
    pass

from rag.audit import AuditLog  # noqa: E402
from rag.config import get_settings  # noqa: E402

st.set_page_config(page_title="Oversight Dashboard", page_icon="🛡️", layout="wide")
s = get_settings()
st.title("🛡️ Oversight Dashboard")

if not s.admin_password:
    st.info("Set ADMIN_PASSWORD (in .env or secrets) to enable this dashboard.")
    st.stop()
if not st.session_state.get("admin"):
    with st.form("admin_login"):
        pw = st.text_input("Admin password", type="password")
        if st.form_submit_button("Sign in"):
            if hmac.compare_digest(pw, s.admin_password):
                st.session_state.admin = True
                st.rerun()
            time.sleep(1)
            st.error("Incorrect password.")
    st.stop()

audit = AuditLog(s.log_dir)
interactions = audit.read("audit")
feedback = audit.read("feedback")
reviews = audit.read("reviews")

if not interactions:
    st.info("No conversations logged yet.")
    st.stop()

ratings: dict[str, str] = {}
comments: dict[str, list[str]] = {}
for f in feedback:
    if f["rating"] in ("up", "down"):
        ratings[f["interaction_id"]] = f["rating"]
    elif f.get("comment"):
        comments.setdefault(f["interaction_id"], []).append(f["comment"])
latest_review = {r["interaction_id"]: r for r in reviews}

rows = []
for r in interactions:
    iid = r["interaction_id"]
    flags = r.get("flags", {})
    reasons = []
    if r.get("needs_review"):
        reasons += flags.get("high_stakes", [])
        if flags.get("crisis"):
            reasons.append("crisis")
        if flags.get("individual_decision"):
            reasons.append("decision about a person")
        if flags.get("injection"):
            reasons.append("prompt injection")
    if r["status"] == "blocked":
        reasons.append("blocked")
    if ratings.get(iid) == "down":
        reasons.append("negative feedback")
    if r.get("confidence") == "Low" and r["status"] == "answered":
        reasons.append("low confidence")
    rows.append({
        "time": r["ts"], "id": iid, "status": r["status"], "question": r["question_redacted"],
        "confidence": r.get("confidence", ""), "feedback": ratings.get(iid, ""),
        "reasons": ", ".join(dict.fromkeys(reasons)), "reviewed": latest_review.get(iid, {}).get("decision", ""),
    })
df = pd.DataFrame(rows).sort_values("time", ascending=False)

# ------------------------------------------------------------------ metrics
total = len(df)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Conversations", total)
c2.metric("Need review", int((df["reasons"] != "").sum()))
c3.metric("Not yet reviewed", int(((df["reasons"] != "") & (df["reviewed"] == "")).sum()))
answered = df[df["status"] == "answered"]
c4.metric("Answered", f"{len(answered) / total:.0%}")
votes = df[df["feedback"] != ""]
c5.metric("Helpful (👍)", f"{(votes['feedback'] == 'up').mean():.0%}" if len(votes) else "–")

# ------------------------------------------------------------------ queue
view = st.radio("Show", ["Needs review", "All", "Blocked", "Negative feedback", "No answer found"],
                horizontal=True)
filtered = {
    "Needs review": df[df["reasons"] != ""],
    "All": df,
    "Blocked": df[df["status"] == "blocked"],
    "Negative feedback": df[df["feedback"] == "down"],
    "No answer found": df[df["status"] == "no_context"],
}[view]
st.dataframe(filtered, use_container_width=True, hide_index=True)
st.download_button("⬇️ Export as CSV", filtered.to_csv(index=False), "oversight_export.csv", "text/csv")

# ------------------------------------------------------------------ review one conversation
st.subheader("Review a conversation")
if filtered.empty:
    st.caption("Nothing to review in this view.")
    st.stop()
choice = st.selectbox("Conversation", filtered["id"].tolist(),
                      format_func=lambda i: f"{i} - {df.set_index('id').loc[i, 'question'][:80]}")
record = next(r for r in interactions if r["interaction_id"] == choice)
left, right = st.columns(2)
with left:
    st.markdown(f"**Question (redacted):** {record['question_redacted']}")
    st.markdown(f"**Search query:** {record.get('search_query', '')}")
    st.markdown("**Answer:**")
    st.markdown(record["answer"])
    for c in comments.get(choice, []):
        st.info(f"User comment: {c}")
with right:
    st.markdown("**Sources used**")
    st.json(record.get("sources", []), expanded=False)
    st.markdown("**Flags & checks**")
    st.json({"status": record["status"], "confidence": record.get("confidence"),
             "warnings": record.get("warnings"), "flags": record.get("flags"),
             "models": record.get("models"), "prompt_version": record.get("prompt_version")}, expanded=False)

if choice in latest_review:
    r = latest_review[choice]
    st.success(f"Last review: **{r['decision']}** by {r['reviewer']} on {r['ts'][:10]} — {r.get('note', '')}")

with st.form("review"):
    reviewer = st.text_input("Reviewer name")
    decision = st.selectbox("Decision", ["Answer correct", "Answer needs correction", "Escalated to policy owner",
                                         "Document needs updating", "No action needed"])
    note = st.text_area("Notes / corrective action")
    if st.form_submit_button("Save review"):
        if not reviewer.strip():
            st.error("Please enter your name - reviews must be attributable.")
        else:
            audit.log_review(choice, reviewer.strip(), decision, note)
            st.success("Review saved.")
            st.rerun()

# ------------------------------------------------------------------ retention
with st.expander("Data retention"):
    st.write(f"Logs older than {s.log_retention_days} days should be deleted (LOG_RETENTION_DAYS).")
    if st.button(f"Delete records older than {s.log_retention_days} days"):
        n = audit.purge_older_than(s.log_retention_days)
        st.success(f"Deleted {n} old record(s).")
