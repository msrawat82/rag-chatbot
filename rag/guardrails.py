"""Rule-based guardrails: privacy (PII), security (prompt injection), safety (crisis),
fairness (protected characteristics, decisions about individuals) and human oversight
(high-stakes topics), plus checks on generated answers (citations, groundedness).

These are fast, deterministic and explainable. An optional LLM safety classifier
(see llm.py) adds a second layer on top of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------ privacy (PII)

PII_PATTERNS: dict[str, re.Pattern] = {
    # Order matters: specific patterns run before broad ones.
    "credit_card": re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<![\w$])(?<!\d[ .-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])"
                        r"\d{3,4}[ .-]?\d{3,4}(?!\w)(?![ .-]?\d)"),
}


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _valid(kind: str, value: str) -> bool:
    if kind == "credit_card":
        return _luhn_ok(value)
    if kind == "phone":
        return 9 <= len(re.sub(r"\D", "", value)) <= 15
    return True


def redact_pii(text: str, types: tuple[str, ...] | list[str]) -> tuple[str, dict[str, int]]:
    """Replace PII with placeholders like [REDACTED_EMAIL]. Returns (text, counts per type)."""
    counts: dict[str, int] = {}
    for kind, pattern in PII_PATTERNS.items():
        if kind not in types:
            continue

        def _sub(m: re.Match, kind=kind) -> str:
            if not _valid(kind, m.group(0)):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            return f"[REDACTED_{kind.upper()}]"

        text = pattern.sub(_sub, text)
    return text, counts


# ------------------------------------------------------------------ security (prompt injection)

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|system|your)\b.{0,20}"
    r"\b(instructions?|prompts?|system messages?)\b",
    r"\b(ignore|disregard|forget)\b.{0,10}\b(your|the assistant'?s?|the ai'?s?)\b.{0,15}\b(rules|guidelines|guardrails)\b",
    r"\b(reveal|show|print|repeat|output|leak|tell me)\b.{0,25}\b(system|hidden|initial|developer)\s+"
    r"(prompt|instructions?|message)",
    r"\byou are now\b|\bfrom now on,? you\b",
    r"\b(jailbreak|DAN mode|developer mode|god mode)\b",
    r"\bpretend\b.{0,30}\b(no|without)\b.{0,15}\b(rules|restrictions|guidelines|filters)\b",
    r"\b(bypass|disable|turn off|deactivate)\b.{0,20}\b(safety|guardrails?|filters?|restrictions|moderation)\b",
    r"</?\s*(system|assistant|instructions?)\s*>|\[/?INST\]|<\|im_start\|>",
    r"\bnew (system )?instructions?\s*:",
]
_INJECTION_RE = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def detect_injection(text: str) -> list[str]:
    """Return the patterns that look like attempts to override the assistant's instructions."""
    return [p.pattern[:40] for p in _INJECTION_RE if p.search(text)]


# ------------------------------------------------------------------ safety, fairness, oversight

TOPIC_PATTERNS: dict[str, str] = {
    # Topics where answers can materially affect a person -> human review & escalation notice.
    "employment_decision": r"\b(terminat\w*|fir(e|ed|ing)\b|dismiss\w*|lay ?offs?|redundanc\w*|disciplin\w*|"
                           r"demot\w*|performance improvement plan|warning letter|probation)",
    "harassment_discrimination": r"\b(harass\w*|discriminat\w*|retaliat\w*|bully\w*|hostile work|"
                                 r"sexual misconduct|assault\w*)",
    "health_medical": r"\b(medical (condition|record|history)|disabilit\w*|pregnan\w*|mental health|"
                      r"injur\w*|diagnos\w*|chronic illness)",
    "legal": r"\b(lawsuit|sue\b|suing|legal advice|attorney|lawyer|court|tribunal|whistle-?blow\w*|"
             r"breach of contract|liabilit\w*)",
    "compensation_individual": r"\b(his|her|their|[A-Z][a-z]+'s) (salary|pay|bonus|compensation)\b",
}
_TOPIC_RE = {k: re.compile(v + r"\b", re.I) for k, v in TOPIC_PATTERNS.items()}

CRISIS_RE = re.compile(
    r"\b(suicid\w*|kill (myself|me)|end my life|self[- ]harm|hurt myself|want to die|"
    r"being (abused|assaulted)|in danger)\b", re.I)

PROTECTED_RE = re.compile(
    r"\b(age|aged|older|elderly|young|younger|gender|female|male|woman|women|man|men|non-binary|transgender|"
    r"race|racial|black|white|asian|hispanic|latin[oax]|arab|"
    r"ethnic\w*|religio\w*|muslim|christian|jewish|hindu|sikh|buddhist|nationality|immigrant|"
    r"disab\w*|pregnan\w*|sexual orientation|gay|lesbian|marital status|married|single parent|"
    r"veteran|caste|colou?r)\b", re.I)

INDIVIDUAL_DECISION_RE = re.compile(
    r"\b(should (we|i|the company|my manager) (fire|terminate|dismiss|promote|hire|discipline|demote|"
    r"reject|approve)|is [A-Z][a-z]+ (eligible|entitled|guilty|lying|a good fit)|"
    r"(rank|rate|score|compare) (these |the )?(employees|candidates|applicants|staff)|"
    r"which (employee|candidate|applicant|person) (should|is better|deserves))", re.I)


_NEUTRALIZE = [
    (re.compile(r"\b(with|who has|who have|having)\s+(a\s+)?(disabilit\w*|chronic illness|medical condition)\b", re.I), ""),
    (re.compile(r"\b(aged?|over|under)\s+\d{1,3}(\s+years?( old)?)?\b", re.I), ""),
    (re.compile(r"\bsingle (parent|mother|father|mom|dad)s?\b", re.I), "employee"),
    (re.compile(r"\b(male|female|non-binary|nonbinary|transgender|trans|cisgender|older|elderly|senior|younger|young|"
                r"muslim|christian|catholic|jewish|hindu|sikh|buddhist|atheist|black|white|asian|hispanic|latino|latina|"
                r"latinx|arab|immigrant|foreign|migrant|gay|lesbian|bisexual|queer|lgbtq?\+?|married|unmarried|"
                r"divorced|widowed|pregnant|disabled|veteran)\b", re.I), ""),
    (re.compile(r"\b(man|woman|men|women|mother|father|mom|dad|husband|wife|girl|boy)\b", re.I), "employee"),
]


def neutralize_protected(text: str) -> str:
    """Remove protected characteristics from a search query, e.g.
    'Is a pregnant Muslim employee eligible for remote work?' -> 'Is an employee eligible for remote work?'
    Used so that everyone retrieves the same general policy passages."""
    out = text
    for pattern, repl in _NEUTRALIZE:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\b(employee)( employee)+\b", r"\1", out, flags=re.I)
    out = re.sub(r"\b(a|an) (?=[aeiouAEIOU])", "an ", out)
    out = re.sub(r"\ban (?=[^aeiouAEIOU\s])", "a ", out)
    return out


@dataclass
class InputAssessment:
    redacted_text: str
    pii_found: dict[str, int] = field(default_factory=dict)
    injection: list[str] = field(default_factory=list)
    crisis: bool = False
    high_stakes: list[str] = field(default_factory=list)
    protected_attributes: bool = False
    individual_decision: bool = False
    too_long: bool = False

    @property
    def blocked(self) -> bool:
        return bool(self.injection) or self.too_long

    @property
    def needs_review(self) -> bool:
        return self.crisis or bool(self.high_stakes) or self.individual_decision


def assess_question(question: str, pii_types: tuple[str, ...], max_chars: int) -> InputAssessment:
    redacted, pii = redact_pii(question, pii_types)
    return InputAssessment(
        redacted_text=redacted,
        pii_found=pii,
        injection=detect_injection(question),
        crisis=bool(CRISIS_RE.search(question)),
        high_stakes=[k for k, r in _TOPIC_RE.items() if r.search(question)],
        protected_attributes=bool(PROTECTED_RE.search(question)),
        individual_decision=bool(INDIVIDUAL_DECISION_RE.search(question)),
        too_long=len(question) > max_chars,
    )


# ------------------------------------------------------------------ output checks (reliability)

CITATION_RE = re.compile(r"\[(\d{1,2})\]")
ABSTAIN_RE = re.compile(r"\b(i don'?t know|couldn'?t find|could not find|not (covered|mentioned|found) in)\b", re.I)
STOPWORDS = set("""a an the and or but if then than that this these those to of in on at by for with from as is are
was were be been being it its into about over under not no yes can may must should will would could do does did
has have had your you our we they their them he she his her i me my any all each per such other also only more
most less least very""".split())


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$%]+(?:[.,][0-9]+)?", text.lower())
            if len(w) > 2 and w not in STOPWORDS}


@dataclass
class AnswerCheck:
    text: str
    citations: list[int]
    invalid_citations: list[int]
    abstained: bool
    groundedness: float          # share of answer content words found in the sources (0-1)
    pii_redacted: dict[str, int]
    warnings: list[str]


def check_answer(answer: str, source_texts: list[str], pii_types: tuple[str, ...]) -> AnswerCheck:
    text, pii = redact_pii(answer or "", pii_types)
    cited = sorted({int(n) for n in CITATION_RE.findall(text)})
    invalid = [n for n in cited if n < 1 or n > len(source_texts)]
    abstained = bool(ABSTAIN_RE.search(text)) and len(text) < 400

    answer_words = _content_words(CITATION_RE.sub("", text))
    source_words = set().union(*(_content_words(s) for s in source_texts)) if source_texts else set()
    grounded = len(answer_words & source_words) / len(answer_words) if answer_words else 1.0

    warnings = []
    if not abstained and not cited:
        warnings.append("The answer does not cite any source, so it could not be verified.")
    if invalid:
        warnings.append(f"The answer cites sources that do not exist: {invalid}.")
    if not abstained and grounded < 0.6:
        warnings.append("Parts of the answer use wording not found in the sources; please verify.")
    if pii:
        warnings.append("Sensitive personal data was removed from the answer.")
    return AnswerCheck(text, cited, invalid, abstained, round(grounded, 2), pii, warnings)


def confidence_label(top_score: float, groundedness: float, warnings: list[str], min_relevance: float) -> str:
    if top_score >= min_relevance + 0.15 and groundedness >= 0.8 and not warnings:
        return "High"
    if top_score >= min_relevance and groundedness >= 0.6:
        return "Medium"
    return "Low"
