# Responsible AI

This assistant answers employees' questions about company policies. Wrong answers can affect pay,
leave, employment and compliance, so responsible-AI controls are built in at three levels:

| Level | What it means here |
|---|---|
| **Code** | Guardrails that run on every request and cannot be skipped by users (in `rag/`). |
| **Configuration** | Switches and thresholds an administrator sets in `.env` without changing code. |
| **Policy & process** | Human responsibilities: ownership, review, incident handling, documentation. |

The tables below map each principle to its controls. File references point to where each control lives.

---

## 1. Fairness

| Control | Level | Where |
|---|---|---|
| **Attribute-neutral retrieval.** When a question mentions a protected characteristic (gender, age, religion, ethnicity, disability, pregnancy, sexual orientation, marital status...), search runs on a neutralised query ("a pregnant Muslim employee" → "an employee"), so everyone gets the same general policy. Up to two group-specific passages are added from the original query, for example a pregnancy accommodation. | Code | `guardrails.neutralize_protected`, `pipeline.search` |
| Answer prompt requires identical treatment, no stereotypes and inclusive language. | Code | `prompts.ANSWER_SYSTEM` rule 5, `PROTECTED_ATTRIBUTE_NOTE` |
| **No decisions about individuals.** Requests to hire/fire/promote/rank specific people are detected; the model explains the policy and names the accountable human instead of deciding. | Code | `guardrails.INDIVIDUAL_DECISION_RE`, `prompts.INDIVIDUAL_DECISION_NOTE` |
| **Counterfactual fairness test.** Runs the same questions for 19 demographic variants and measures whether they get the same passages. Measured on the sample data: coverage rose from 0.83 (plain search) to 1.00 with neutral retrieval. | Code / Process | `python -m eval.fairness_eval` |
| The policy documents themselves are the main source of bias. Policy owners review flagged fairness issues and fix the source document. | Policy | Section 9 |

## 2. Transparency

| Control | Level | Where |
|---|---|---|
| AI disclosure banner on every page: answers are AI-generated, may be wrong, and are not decisions. | Code | `app.py` |
| Every fact is cited as `[n]`. A **Sources** panel shows document, section, page, category and relevance score. | Code | `prompts.ANSWER_SYSTEM` rule 2, `app.render_assistant` |
| A confidence label (High/Medium/Low) based on retrieval score and groundedness, plus the search query actually used. | Code | `guardrails.confidence_label` |
| "About this assistant" lists the models used, the knowledge-base version and every document with its ingestion date. | Code | `app.py` sidebar, Pinecone manifest |
| All prompts are in one versioned file; the version is logged with every answer. | Code | `rag/prompts.py` (`PROMPT_VERSION`) |
| This document, the README and the model/data details are published with the code. | Policy | `docs/`, `README.md` |

## 3. Accountability

| Control | Level | Where |
|---|---|---|
| **Audit trail** of every interaction: redacted question, search query, sources used, answer, flags, confidence, models, prompt version and latency. | Code | `rag/audit.py` → `logs/audit.jsonl` |
| **Data lineage.** Each ingested document's SHA-256 hash, title, category, chunk count and ingestion time are stored in the index manifest and in `logs/ingest_runs/`. | Code | `ingest.py`, `vectorstore.save_manifest` |
| Every human review is attributed (reviewer name required) and timestamped. | Code | Oversight Dashboard, `logs/reviews.jsonl` |
| Named owners: a **system owner** (runs the app), **policy owners** (one per category) and **reviewers** (work the oversight queue). | Policy | Section 9 |
| CI runs the test suite on every push, so guardrails can't silently break. | Process | `.github/workflows/tests.yml` |

## 4. Privacy

| Control | Level | Where |
|---|---|---|
| **PII redaction in questions** (SSN, card numbers with checksum validation, IBAN, email, phone) *before* they reach the LLM provider or the logs. | Code | `guardrails.redact_pii`, `pipeline.ask` |
| **PII redaction in documents** before embedding/storage (default: SSN, cards, IBAN; role emails and phone numbers are usually needed in policies so are kept). | Code + Config | `ingestion.process_document`, `DOCUMENT_PII_TYPES` |
| **PII redaction in answers.** | Code + Config | `guardrails.check_answer`, `ANSWER_PII_TYPES` |
| Chat history is redacted before it is sent back to the model. | Code | `pipeline.ask` |
| No user identity is stored, only a random session id. API keys are never logged. | Code | `audit.py` |
| **Retention.** Logs older than `LOG_RETENTION_DAYS` (default 90) can be purged from the dashboard. | Config + Process | Oversight Dashboard |
| A privacy notice in the app tells users what is logged and why. | Code | `app.py` sidebar |
| Policy PDFs, logs and `.env` are excluded from git. | Code | `.gitignore` |
| Data processing agreements with the LLM and vector-DB providers (Groq, Pinecone); check their data-retention terms. | Policy | Section 9 |

## 5. Safety

| Control | Level | Where |
|---|---|---|
| **Crisis detection.** Self-harm or danger signals get a supportive message pointing to emergency services and human contacts instead of a policy answer, and are flagged for follow-up. | Code | `guardrails.CRISIS_RE`, `prompts.CRISIS_MESSAGE` |
| **Second-layer safety classifier.** An optional safety model (`openai/gpt-oss-safeguard-20b`) checks each question against a written policy. It fails open, so rule-based checks still apply if it's unavailable. | Code + Config | `llm.moderate`, `ENABLE_LLM_MODERATION` |
| No legal, medical, tax or financial advice; the assistant refers users to experts. | Code | `prompts.ANSWER_SYSTEM` rule 7 |
| **High-stakes topics** (employment decisions, harassment/discrimination, health, legal, individual pay) get a human-oversight notice and are queued for review. | Code | `guardrails.TOPIC_PATTERNS` |

## 6. Security

| Control | Level | Where |
|---|---|---|
| **Prompt-injection detection on user input.** Attempts to override instructions or extract the system prompt are blocked before the LLM. | Code | `guardrails.detect_injection` |
| **Indirect-injection defence.** Document passages that contain instructions to an AI are quarantined at ingestion and filtered again at query time. The prompt treats all sources as untrusted data. | Code | `ingestion.process_document`, `pipeline.retrieve`, prompt rule 4 |
| Secrets come only from environment variables, `.env` or Streamlit secrets, never from code. | Code | `rag/config.py` |
| Optional app password (`APP_PASSWORD`) and an admin password for the dashboard, with constant-time comparison and a brute-force delay. | Code + Config | `app.py`, dashboard |
| Per-session rate limiting and a maximum question length. | Code + Config | `RATE_LIMIT_PER_MINUTE`, `MAX_QUESTION_CHARS` |
| Stack traces are hidden from users; XSRF protection is on; usage statistics are off. | Config | `.streamlit/config.toml` |
| **For production:** put the app behind company SSO, use HTTPS, and restrict which documents each user group can search (metadata filters per role). | Policy | Section 10 |

## 7. Reliability

| Control | Level | Where |
|---|---|---|
| **Grounding.** The model may use only the retrieved passages and must cite them. | Code | `prompts.ANSWER_SYSTEM` rule 1 |
| **Abstention.** If no passage scores above `MIN_RELEVANCE`, the bot says it couldn't find the answer *without* calling the LLM, so it can't invent one. | Code + Config | `pipeline.ask`, `MIN_RELEVANCE` |
| **Answer verification.** Checks for missing or invalid citations and measures groundedness (overlap with sources). Warnings are shown to the user and logged. | Code | `guardrails.check_answer` |
| **Structure-aware chunking** that adapts to each PDF's layout, plus a per-document ingestion report (headings found, tables, scanned pages needing OCR, quarantined passages). | Code | `rag/chunking.py`, `ingest.py` |
| Embedding-model consistency check: the app refuses to run against an index built with a different model. | Code | `app.py` |
| Low temperature, automatic retries with back-off, timeouts and graceful error messages. | Code | `rag/llm.py` |
| Idempotent ingestion: unchanged files are skipped, changed files fully replace their old chunks, and `--prune` removes deleted documents. | Code | `ingest.py` |
| **Evaluation.** A retrieval benchmark (hit rate, abstention on out-of-scope questions, threshold calibration) and unit tests. | Process | `eval/retrieval_eval.py`, `tests/` |

## 8. Human oversight

| Control | Level | Where |
|---|---|---|
| **Oversight Dashboard.** A queue of conversations needing review (high-stakes, crisis, blocked, low confidence, 👎 feedback), with details, attributed review decisions, CSV export and retention purge. | Code | `pages/1_Oversight_Dashboard.py` |
| **User feedback.** 👍/👎 on every answer, with an optional comment. | Code | `app.py` |
| **Human-in-the-loop for consequential topics.** The assistant informs, and a human decides. | Code + Policy | `HUMAN_OVERSIGHT_NOTICE` |
| **Kill switch.** `MAINTENANCE_MODE=true` takes the assistant offline immediately. | Config | `app.py` |
| Escalation contact shown throughout (`ESCALATION_CONTACT`). | Config | `.env` |

---

## 9. Operating procedures (policy level)

1. **Ownership.** Assign a system owner, a policy owner for every category folder in `data/`, and at
   least one reviewer. Record their names in your internal runbook.
2. **Document onboarding.** Only policy owners add or replace PDFs. Run `python ingest.py --dry-run` and check the
   report (headings detected, scanned pages, quarantined passages) before uploading.
3. **Review cadence.** Reviewers clear the "Needs review" queue at least weekly, and crisis flags within one
   business day.
4. **Quality gates before each release:** `pytest` passes, `eval.retrieval_eval` hit rate ≥ 90% with all
   out-of-scope questions abstaining, and `eval.fairness_eval` mean coverage ≥ 0.95. Add real employee
   questions to `eval/questions.json` over time.
5. **Incidents.** For a harmful or wrong answer that was acted on: set `MAINTENANCE_MODE=true` if needed,
   export the interaction from the dashboard, fix the document/prompt/threshold, add a test or eval
   question that reproduces it, then re-enable.
6. **Change management.** Any prompt change bumps `PROMPT_VERSION`. Any change of embedding model requires
   `ingest.py --recreate`.
7. **Vendors.** Review Groq's and Pinecone's data-processing and retention terms. Don't index documents
   whose classification forbids third-party processing.
8. **Retention.** Purge logs according to `LOG_RETENTION_DAYS` and your records policy.

## 10. Known limitations

- **Rule-based detectors** (PII, injection, topics) use patterns. They can miss unusual phrasing and
  sometimes flag harmless questions. The LLM safety layer and human review reduce, but don't remove,
  this risk.
- **Groundedness** is a word-overlap heuristic, not proof of correctness. Citations must still be
  checked for important decisions.
- **Scanned PDFs** have no text layer and need OCR before ingestion (the report lists such pages).
- **PDF layout.** Complex multi-column layouts and nested tables may chunk imperfectly. Use
  `--dry-run --show-chunks` to inspect them.
- **Access control** in this version is all-or-nothing. Production use with confidential categories should add
  per-role filtering (Pinecone metadata filters on `category`) behind SSO.
- **Model behaviour** can change when the provider updates a model. Re-run the evaluations after any
  model change.
