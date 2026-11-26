# Company Policy Assistant (RAG): Context and Requirements

## Context

**The problem:** Employees often can't find the right rule in long policy documents, such as leave, travel expenses, vendor onboarding or information security. They either search through PDFs by hand or ask a colleague.

**The solution:** A chatbot that answers policy questions from your own PDFs. Each answer cites the document, section and page it came from. If the policies don't cover the question, the bot says so instead of guessing.

**Who uses it:**
- **Employees** ask questions in a chat window.
- **Admins or policy owners** review flagged conversations in the Oversight Dashboard.
- **The developer** adds or updates PDFs and runs the ingestion script.

**What it is not:** It is not a substitute for HR, legal or compliance advice. The README and `docs/RESPONSIBLE_AI.md` describe its limits.

## How it works

1. **Ingestion (`ingest.py`, run when documents change):** Reads PDFs, splits them into structure-aware chunks, removes personal data such as SSNs and card numbers, creates embeddings, and stores them in Pinecone.
2. **Answering (`app.py`, every question):** Checks the question for safety, rewrites it for search, finds the best-matching passages, has the LLM write a cited answer, checks the answer, and logs everything for audit.

## Tech stack

| Part | Technology |
|---|---|
| Language | Python 3.10+ |
| Web app and dashboard | Streamlit (`app.py`, `pages/1_Oversight_Dashboard.py`) |
| Answer model (LLM) | Groq API, model `openai/gpt-oss-20b`; the `openai` Python package is used as a client, pointed at Groq |
| Safety classifier | Groq API, model `openai/gpt-oss-safeguard-20b` (optional second layer) |
| Vector database | Pinecone (serverless, AWS us-east-1 by default) |
| Embeddings | `BAAI/bge-small-en-v1.5` via sentence-transformers, run locally |
| PDF reading | pdfplumber (text, font size and bold, tables) |
| Data handling | pandas |
| Configuration | python-dotenv, reading `.env` |
| Audit and manifest | JSON files in `logs/` and an ingestion manifest |
| Tests | pytest (fake models, no API keys needed) |
| Sample data generator | reportlab (`tools/generate_globalomtech_policies.py`) |
| Version control | Git, hosted on GitHub (`msrawat82/rag-chatbot`) |

## Functional requirements

| # | Requirement |
|---|---|
| F1 | Accept PDFs grouped into category folders (for example `hr_policies`, `finance_policies`) |
| F2 | Handle varied layouts: font-based headings, numbered text headings, FAQs and tables |
| F3 | Answer questions in a chat interface, with source citations (document, section, page) |
| F4 | Abstain ("I couldn't find this") when no passage is relevant enough |
| F5 | Filter by category in the app |
| F6 | Ingest incrementally: only new or changed PDFs are uploaded; unchanged ones are skipped |
| F7 | Support adding, listing, removing and pruning documents from the command line |
| F8 | Collect user feedback on answers |
| F9 | Oversight Dashboard (admin-only) to review flagged conversations |

## Non-functional and responsible-AI requirements

| Area | Requirement |
|---|---|
| **Accuracy** | Answers must be grounded in retrieved text. Measured by `eval/retrieval_eval` |
| **Fairness** | Answers must not change based on protected attributes. Checked by `eval/fairness_eval` |
| **Privacy** | Redact SSNs, card numbers and IBANs from documents, questions and answers; optionally redact emails and phone numbers from questions |
| **Safety** | Block prompt-injection attempts and harmful requests (pattern checks plus an LLM safety classifier) |
| **Access control** | Optional `APP_PASSWORD` for the app and `ADMIN_PASSWORD` for the dashboard |
| **Rate limiting** | Default 10 questions per minute per user (`RATE_LIMIT_PER_MINUTE`) |
| **Input limits** | Questions capped at 1,000 characters (`MAX_QUESTION_CHARS`) |
| **Accountability** | Audit log of each interaction, kept 90 days by default (`LOG_RETENTION_DAYS`) |
| **Kill switch** | `MAINTENANCE_MODE=true` takes the assistant offline immediately |
| **Transparency** | Every answer shows its sources; the README explains how chunking works |
| **Reliability** | Tests run without API keys; ingestion stops on model mismatches |

## Technical requirements

**Software:**
- Python 3.10 or newer
- Packages in `requirements.txt`: Streamlit, openai (client library for Groq), Pinecone, sentence-transformers, pdfplumber, python-dotenv, pandas
- For tests and sample generation: `requirements-dev.txt` (pytest, reportlab)

**Accounts and keys (both have free tiers):**
- Groq API key (`GROQ_API_KEY`) for answers and safety checks
- Pinecone API key (`PINECONE_API_KEY`) for storage

**Configuration:** All settings live in `.env`, based on `.env.example`. Key settings are the models (`LLM_MODEL`, `EMBED_MODEL`), `MIN_RELEVANCE` (0.55, the cut-off below which the bot abstains), `TOP_K` (passages used per answer) and the PII settings.

**Hardware:** A standard laptop is enough. The embedding model runs locally, so the first run downloads it.

**Data:** PDFs go in `data/<category>/`. This folder is git-ignored, so real policies never reach GitHub.

## Limitations and open items

- **Scanned PDFs need OCR first.** Text-based PDFs only.
- **Pattern-based detection is imperfect.** Some PII or injection attempts may get through, so human review matters.
- **Production needs more.** Single sign-on and per-role document access are not built in.
- **Sample data is fictional.** The 10 GlobalOmTech PDFs are generated for testing, and some template text can contradict its own tables.
- **Leftover NovaTech references.** `eval/questions.json` and the README's results line still refer to the NovaTech sample data.
- **Not yet ingested.** Your Pinecone index needs `python ingest.py` before the chatbot has any answers to give.
