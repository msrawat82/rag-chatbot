"""Measure retrieval quality offline: does search return a passage containing the expected answer?

    python -m eval.retrieval_eval --data data --questions eval/questions.json

Questions file: a JSON list of
    {"question": "...", "expect": ["text that must appear in a retrieved passage", ...]}
Use "expect": [] for out-of-scope questions that should return nothing (tests MIN_RELEVANCE).
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import replace

from rag.config import get_settings

from .common import build_offline_pipeline


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None, help="Folder of PDFs (default: DATA_DIR)")
    ap.add_argument("--questions", default="eval/questions.json")
    ap.add_argument("--min-relevance", type=float, default=None, help="Override MIN_RELEVANCE")
    args = ap.parse_args()

    s = get_settings()
    if args.min_relevance is not None:
        s = replace(s, min_relevance=args.min_relevance)
    data = args.data or s.data_dir
    questions = json.load(open(args.questions, encoding="utf-8"))

    # Retrieve with no threshold so score distributions can be shown, then apply it per question.
    pipe, n = build_offline_pipeline(data, replace(s, min_relevance=-1.0))
    print(f"Indexed {n} chunks from {data} | model {s.embed_model} | top_k {s.top_k} | "
          f"MIN_RELEVANCE {s.min_relevance}\n")

    hits, in_scope_scores, out_scope_scores, abstain_ok, failures = 0, [], [], 0, []
    in_scope = [q for q in questions if q["expect"]]
    for q in questions:
        matches = pipe.retrieve(q["question"])
        top = matches[0].score if matches else 0.0
        kept = [m for m in matches if m.score >= s.min_relevance]
        if q["expect"]:
            in_scope_scores.append(top)
            bodies = [m.metadata.get("body", "").lower() for m in kept]
            rank = next((i + 1 for i, b in enumerate(bodies) if any(e.lower() in b for e in q["expect"])), None)
            hits += rank is not None
            status = f"hit @ {rank}" if rank else "MISS"
            if not rank:
                failures.append(q["question"])
        else:
            out_scope_scores.append(top)
            abstain_ok += not kept
            status = "abstained" if not kept else f"returned {len(kept)} passages (should abstain)"
        print(f"  {top:.2f}  {status:<35} {q['question']}")

    print(f"\nRetrieval hit rate: {hits}/{len(in_scope)} = {hits / max(len(in_scope), 1):.0%}")
    if out_scope_scores:
        print(f"Out-of-scope correctly abstained: {abstain_ok}/{len(out_scope_scores)}")
    if in_scope_scores and out_scope_scores:
        print(f"Top-score range  in-scope: {min(in_scope_scores):.2f}-{max(in_scope_scores):.2f} "
              f"(median {statistics.median(in_scope_scores):.2f}) | out-of-scope: "
              f"{min(out_scope_scores):.2f}-{max(out_scope_scores):.2f}")
        print("A good MIN_RELEVANCE sits between the highest out-of-scope and the lowest in-scope score.")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
