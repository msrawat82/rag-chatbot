"""LLM access (Groq's OpenAI-compatible API) with timeouts and automatic retries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from . import prompts


@dataclass
class ModerationResult:
    flagged: bool
    category: str
    error: str = ""


class LLM:
    def __init__(self, api_key: str, model: str, base_url: str, safety_model: str = "",
                 temperature: float = 0.1, timeout: float = 60.0, max_retries: int = 3):
        # The OpenAI client retries rate limits / server errors with exponential backoff.
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.safety_model = safety_model
        self.temperature = temperature

    def complete(self, messages: list[dict], model: str | None = None, temperature: float | None = None) -> str:
        res = self.client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
        )
        return (res.choices[0].message.content or "").strip()

    def rewrite_query(self, question: str, history: list[dict]) -> str:
        if not history and len(question.split()) <= 8:
            return question           # already a short, standalone query
        convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history)
        try:
            query = self.complete([
                {"role": "system", "content": prompts.REWRITE_SYSTEM},
                {"role": "user", "content": prompts.REWRITE_USER.format(history=convo, question=question)},
            ], temperature=0)
            return query.strip().strip('"')[:300] or question
        except Exception:
            return question

    def answer(self, question: str, sources_block: str, history: list[dict], notes: list[str]) -> str:
        system = prompts.ANSWER_SYSTEM + ("\n\n" + "\n\n".join(notes) if notes else "")
        messages = [{"role": "system", "content": system}, *history,
                    {"role": "user", "content": prompts.ANSWER_USER.format(sources=sources_block, question=question)}]
        return self.complete(messages)

    def moderate(self, text: str) -> ModerationResult:
        """Optional second safety layer using a safety-tuned model. Fails open (rule-based checks still apply)."""
        if not self.safety_model:
            return ModerationResult(False, "disabled")
        try:
            raw = self.complete([
                {"role": "system", "content": prompts.MODERATION_POLICY},
                {"role": "user", "content": text[:4000]},
            ], model=self.safety_model, temperature=0)
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0)) if match else {}
            flagged = str(data.get("violation", "0")).strip() == "1"
            return ModerationResult(flagged, str(data.get("category", "none")))
        except Exception as exc:  # model unavailable, bad JSON, network...
            return ModerationResult(False, "error", error=type(exc).__name__)
