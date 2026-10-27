"""Append-only audit trail (JSON Lines) for accountability and human oversight.

logs/audit.jsonl     one record per question: redacted question, sources, answer, flags, models
logs/feedback.jsonl  thumbs up/down and comments from users
logs/reviews.jsonl   decisions made by human reviewers in the oversight dashboard

Questions are stored with PII already redacted. API keys are never logged.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    def __init__(self, log_dir: str):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.paths = {k: self.dir / f"{k}.jsonl" for k in ("audit", "feedback", "reviews")}

    def _append(self, kind: str, record: dict) -> None:
        with _lock, self.paths[kind].open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def log_interaction(self, record: dict) -> None:
        self._append("audit", {"ts": utc_now(), **record})

    def log_feedback(self, interaction_id: str, rating: str, comment: str = "", session_id: str = "") -> None:
        self._append("feedback", {"ts": utc_now(), "interaction_id": interaction_id, "rating": rating,
                                  "comment": comment[:1000], "session_id": session_id})

    def log_review(self, interaction_id: str, reviewer: str, decision: str, note: str = "") -> None:
        self._append("reviews", {"ts": utc_now(), "interaction_id": interaction_id, "reviewer": reviewer,
                                 "decision": decision, "note": note[:2000]})

    def read(self, kind: str) -> list[dict]:
        path = self.paths[kind]
        if not path.exists():
            return []
        rows = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def purge_older_than(self, days: int) -> int:
        """Delete records older than the retention period. Returns how many were removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        with _lock:
            for path in self.paths.values():
                if not path.exists():
                    continue
                kept = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        ts = datetime.fromisoformat(json.loads(line)["ts"])
                    except (ValueError, KeyError, json.JSONDecodeError):
                        continue
                    if ts >= cutoff:
                        kept.append(line)
                    else:
                        removed += 1
                path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        return removed
