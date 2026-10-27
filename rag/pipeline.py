"""The question-answering pipeline with responsible-AI controls at every step.

    question
      -> input guardrails (length, PII redaction, prompt injection, crisis, LLM safety check)
      -> fairness / oversight flags (protected characteristics, decisions about people, high-stakes topics)
      -> query rewrite -> retrieval (+ relevance threshold, drop suspicious passages)
      -> grounded answer with citations
      -> output checks (PII, citations, groundedness, confidence) -> human-oversight notice
      -> audit log
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import guardrails, prompts
from .audit import AuditLog
from .config import Settings
from .embeddings import Embedder
from .llm import LLM
from .vectorstore import Match, VectorStore


@dataclass
class Source:
    number: int
    title: str
    category: str
    section: str
    page: int
    score: float
    text: str
    chunk_id: str


@dataclass
class Answer:
    text: str
    interaction_id: str
    status: str                      # answered | no_context | blocked | crisis | error
    sources: list[Source] = field(default_factory=list)
    search_query: str = ""
    confidence: str = ""
    warnings: list[str] = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    needs_review: bool = False


class RAGPipeline:
    def __init__(self, settings: Settings, embedder: Embedder, store: VectorStore, llm: LLM, audit: AuditLog):
        self.s = settings
        self.embedder = embedder
        self.store = store
        self.llm = llm
        self.audit = audit

    # -------------------------------------------------------------- retrieval

    def retrieve(self, query: str, categories: list[str] | None = None) -> list[Match]:
        matches = self.store.query(self.embedder.embed_query(query), self.s.candidate_k, categories)
        kept = []
        for m in matches:
            if m.score < self.s.min_relevance:
                continue
            if guardrails.detect_injection(m.metadata.get("text", "")):
                continue                         # defence in depth: never pass suspicious passages on
            kept.append(m)
            if len(kept) == self.s.top_k:
                break
        return kept

    def search(self, query: str, categories: list[str] | None = None, protected: bool = False) -> list[Match]:
        """Fairness: when the query mentions a protected characteristic, search with a neutral version
        first so everyone gets the same general policy, then add at most two passages that are specific
        to the characteristic (e.g. a pregnancy accommodation) from the original query."""
        if not protected:
            return self.retrieve(query, categories)
        neutral = guardrails.neutralize_protected(query)
        if neutral.lower() == query.lower():
            return self.retrieve(query, categories)
        base = self.retrieve(neutral, categories)
        seen = {m.id for m in base}
        extra = [m for m in self.retrieve(query, categories) if m.id not in seen][:2]
        return base + extra

    @staticmethod
    def to_sources(matches: list[Match]) -> list[Source]:
        out = []
        for n, m in enumerate(matches, start=1):
            md = m.metadata
            out.append(Source(number=n, title=str(md.get("title", "")), category=str(md.get("category", "")),
                              section=str(md.get("section_path", "")), page=int(md.get("page", 0) or 0),
                              score=round(m.score, 3), text=str(md.get("body") or md.get("text", "")),
                              chunk_id=m.id))
        return out

    @staticmethod
    def sources_block(sources: list[Source]) -> str:
        return "\n\n".join(
            f"[{s.number}] (document: {s.title}; category: {s.category}; section: {s.section or '-'}; "
            f"page {s.page})\n{s.text}" for s in sources)

    # -------------------------------------------------------------- main entry

    def ask(self, question: str, history: list[dict], session_id: str,
            categories: list[str] | None = None) -> Answer:
        started = time.time()
        iid = self.audit.new_id()
        contact = self.s.escalation_contact
        check = guardrails.assess_question(question, self.s.question_pii_types, self.s.max_question_chars)
        flags: dict = {
            "pii_in_question": check.pii_found,
            "injection": check.injection,
            "crisis": check.crisis,
            "high_stakes": check.high_stakes,
            "protected_attributes": check.protected_attributes,
            "individual_decision": check.individual_decision,
        }

        def finish(ans: Answer) -> Answer:
            ans.flags = flags
            self.audit.log_interaction({
                "interaction_id": iid, "session_id": session_id, "status": ans.status,
                "question_redacted": check.redacted_text, "search_query": ans.search_query,
                "categories": categories or [], "answer": ans.text,
                "sources": [{"chunk_id": s.chunk_id, "title": s.title, "page": s.page, "score": s.score}
                            for s in ans.sources],
                "confidence": ans.confidence, "warnings": ans.warnings, "flags": flags,
                "needs_review": ans.needs_review, "latency_ms": int((time.time() - started) * 1000),
                "models": {"llm": self.s.llm_model, "embedding": self.s.embed_model,
                           "safety": self.s.safety_model if self.s.enable_llm_moderation else None},
                "prompt_version": prompts.PROMPT_VERSION, "index": self.s.index_name,
            })
            return ans

        # 1. Hard blocks: oversized input, prompt injection.
        if check.too_long:
            return finish(Answer(f"Please keep questions under {self.s.max_question_chars} characters.",
                                 iid, "blocked"))
        if check.injection:
            return finish(Answer(prompts.BLOCKED_MESSAGE, iid, "blocked", needs_review=True))

        # 2. Safety: people in crisis are routed to humans, not to the policy bot.
        if check.crisis:
            return finish(Answer(prompts.CRISIS_MESSAGE.format(contact=contact), iid, "crisis", needs_review=True))

        # 3. Optional LLM safety classifier (second layer).
        if self.s.enable_llm_moderation:
            mod = self.llm.moderate(check.redacted_text)
            flags["moderation"] = {"flagged": mod.flagged, "category": mod.category, "error": mod.error}
            if mod.flagged:
                return finish(Answer(prompts.BLOCKED_MESSAGE, iid, "blocked", needs_review=True))

        # 4. Retrieval on a cleaned-up query. History is redacted before it leaves the app.
        safe_history = [{"role": m["role"], "content": guardrails.redact_pii(m["content"],
                                                                             self.s.question_pii_types)[0]}
                        for m in history[-self.s.history_turns:]]
        try:
            search_query = self.llm.rewrite_query(check.redacted_text, safe_history)
            # Redaction placeholders carry no meaning for search and can pull in unrelated passages.
            search_query = re.sub(r"\s*\[REDACTED_[A-Z_]+\]", "", search_query).strip() or search_query
            sources = self.to_sources(self.search(search_query, categories, check.protected_attributes))
        except Exception as exc:
            return finish(Answer(f"Sorry, the knowledge base is unavailable right now ({type(exc).__name__}). "
                                 "Please try again later.", iid, "error"))

        if not sources:
            return finish(Answer(prompts.NO_CONTEXT_MESSAGE.format(contact=contact), iid, "no_context",
                                 search_query=search_query, confidence="Low"))

        # 5. Grounded answer, with extra instructions for fairness-sensitive questions.
        notes = []
        if check.individual_decision:
            notes.append(prompts.INDIVIDUAL_DECISION_NOTE)
        if check.protected_attributes:
            notes.append(prompts.PROTECTED_ATTRIBUTE_NOTE)
        try:
            raw = self.llm.answer(check.redacted_text, self.sources_block(sources), safe_history, notes)
        except Exception as exc:
            return finish(Answer(f"Sorry, the answer service is unavailable right now ({type(exc).__name__}).",
                                 iid, "error", sources=sources, search_query=search_query))

        # 6. Output checks.
        result = guardrails.check_answer(raw, [s.text for s in sources], self.s.answer_pii_types)
        confidence = "Low" if result.abstained else guardrails.confidence_label(
            sources[0].score, result.groundedness, result.warnings, self.s.min_relevance)
        flags["groundedness"] = result.groundedness
        flags["citations"] = result.citations

        text = result.text
        needs_review = check.needs_review or bool(result.invalid_citations)
        if check.needs_review:
            text += "\n\n" + prompts.HUMAN_OVERSIGHT_NOTICE.format(contact=contact)

        cited = [s for s in sources if s.number in result.citations] or sources
        return finish(Answer(text, iid, "no_context" if result.abstained else "answered", sources=cited,
                             search_query=search_query, confidence=confidence, warnings=result.warnings,
                             needs_review=needs_review))
