"""Structure-aware chunking that adapts to each PDF's layout.

How headings are found (per document):
  * Font mode   - if the PDF uses larger or bold fonts for headings (most Word/Google Docs
                  exports), heading levels come from font size/weight.
  * Text mode   - otherwise, headings are found from text patterns: "4.2 Title",
                  "Section 3", "ARTICLE IV", ALL-CAPS lines and short title lines.
  * Q&A lines   - "FAQ-12: ...", "Q: ..." or a short line ending in "?" become their own chunk.

Also removes running headers/footers, page numbers and tables of contents, turns table rows
into sentences, drops duplicate passages and splits long sections on sentence boundaries.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .pdf_extract import ExtractedDoc, Line, body_font_size

PARSER_VERSION = "2.0"

NUMBERED = re.compile(r"^((?:\d{1,2}\.)*\d{1,2})\.?\s+(\S.{0,118})$")
KEYWORD = re.compile(
    r"^(section|article|chapter|part|appendix|annex|annexure|schedule|clause)\s+"
    r"([0-9]+(?:\.[0-9]+)*|[IVXLC]+|[A-Z])\b", re.I)
QUESTION = re.compile(r"^(?:FAQ[\s\-]?\d+|Q\s?\d*|Question\s?\d*)\s*[:.)\-]\s*(.+)$", re.I)
TOC_TITLE = re.compile(r"^(table of contents|contents|index)$", re.I)
DOT_LEADER = re.compile(r"(\.\s?){4,}\s*\d+\s*$")
TRAILING_PAGE = re.compile(r"\s\d{1,3}$")
PAGE_NUMBER = re.compile(r"^(page\s*)?#(\s*(of|/)\s*#)?$")
SENTENCE_END = (".", "?", "!", ":", ";", '"', ")")


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str                 # what gets embedded (breadcrumb + body)
    body: str                 # the passage itself
    heading_path: list[str]
    page: int
    kind: str                 # "text" or "table"
    index: int


@dataclass
class ChunkStats:
    mode: str = ""
    headings: int = 0
    running_lines_removed: int = 0
    toc_lines_removed: int = 0
    duplicates_removed: int = 0
    table_rows: int = 0
    blocks: int = 0
    level_styles: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers

def make_doc_id(relative_path: str) -> str:
    stem = re.sub(r"\.pdf$", "", relative_path, flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:60] or "doc"
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _words(text: str) -> int:
    return len(text.split())


def remove_running_lines(lines: list[Line], page_count: int) -> tuple[list[Line], int]:
    """Drop headers/footers that repeat at the top or bottom of many pages, and bare page numbers."""
    by_page: dict[int, list[Line]] = defaultdict(list)
    for ln in lines:
        if ln.kind == "text":
            by_page[ln.page].append(ln)

    edge_ids: set[int] = set()
    counts: Counter[str] = Counter()
    for page_lines in by_page.values():
        page_lines.sort(key=lambda l: l.top)
        n = 2 if len(page_lines) >= 6 else 1          # short pages: only the very first/last line
        edges = page_lines[:n] + page_lines[-n:]
        edge_ids.update(id(l) for l in edges)
        counts.update({_norm(l.text) for l in edges})

    threshold = max(3, int(0.4 * page_count))
    running = {k for k, c in counts.items() if c >= threshold} if page_count >= 3 else set()

    kept, removed = [], 0
    for ln in lines:
        key = _norm(ln.text)
        is_edge = id(ln) in edge_ids
        if ln.kind == "text" and (PAGE_NUMBER.match(key) or (is_edge and key in running)):
            removed += 1
            continue
        kept.append(ln)
    return kept, removed


def _is_toc_entry(text: str, same_page_as_title: bool) -> bool:
    if DOT_LEADER.search(text):
        return True
    short = _words(text) <= 15
    if short and bool(TRAILING_PAGE.search(text)) and not text.endswith("."):
        return True
    # Entries without page numbers only count on the page that has the "Contents" title.
    return same_page_as_title and short and bool(NUMBERED.match(text) or KEYWORD.match(text))


def _ends_sentence(text: str) -> bool:
    return text.endswith(SENTENCE_END)


def _is_question(text: str, paragraph_start: bool) -> str | None:
    m = QUESTION.match(text)
    if m and _words(text) <= 40:
        return text
    if paragraph_start and text.endswith("?") and _words(text) <= 25 and text[0].isupper():
        return text
    return None


def split_long(text: str, max_words: int) -> list[str]:
    """Split on sentence boundaries with one sentence of overlap; hard-split very long sentences."""
    if _words(text) <= max_words:
        return [text]
    sentences: list[str] = []
    for s in re.split(r"(?<=[.?!;])\s+", text):
        words = s.split()
        while len(words) > max_words:
            sentences.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if words:
            sentences.append(" ".join(words))

    pieces, current = [], []
    for s in sentences:
        if current and _words(" ".join(current + [s])) > max_words:
            pieces.append(" ".join(current))
            current = [current[-1]] if _words(current[-1]) < max_words // 2 else []
        current.append(s)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _join_lines(parts: list[str]) -> str:
    text = ""
    for p in parts:
        if not text:
            text = p
        elif text.endswith("-") and p[:1].islower():
            text += p                      # word broken across lines: "home-" + "office"
        else:
            text += " " + p
    return text


# ---------------------------------------------------------------- heading detection

class HeadingDetector:
    def __init__(self, lines: list[Line]):
        text_lines = [l for l in lines if l.kind == "text"]
        self.body_size = body_font_size(text_lines)
        bold_chars = sum(len(l.text) for l in text_lines if l.bold)
        all_chars = sum(len(l.text) for l in text_lines) or 1
        self.body_is_bold = bold_chars / all_chars > 0.5

        candidates = [l for l in text_lines if self._font_heading_candidate(l)]
        # Styles used only on page 1 are cover-page title fonts, not section headings.
        style_pages: dict[tuple[float, bool], set[int]] = defaultdict(set)
        for l in candidates:
            style_pages[(l.size, l.bold)].add(l.page)
        last_page = max((l.page for l in text_lines), default=1)
        self.cover_styles = {s for s, pages in style_pages.items() if pages == {1} and last_page > 1}
        candidates = [l for l in candidates if (l.size, l.bold) not in self.cover_styles]
        styles = sorted({(l.size, l.bold) for l in candidates}, key=lambda s: (-s[0], not s[1]))
        self.font_mode = len(candidates) >= 2
        self.style_level = {s: i + 1 for i, s in enumerate(styles[:4])}
        self.max_level = max(self.style_level.values(), default=3) if self.font_mode else 3

    def _font_heading_candidate(self, ln: Line) -> bool:
        if not ln.size or _words(ln.text) > 20 or ln.text.endswith((".", ",", ";")):
            return False
        bigger = self.body_size and ln.size >= self.body_size * 1.12
        bold = ln.bold and not self.body_is_bold and ln.size >= self.body_size * 0.95
        return bool(bigger or bold) and ln.text[:1].isalnum()

    def level(self, ln: Line, paragraph_start: bool, continues: bool = False) -> int | None:
        """Return the heading level (1 = top) or None if the line is body text.

        `continues` is True when the next line starts in lower case, i.e. this line is part of
        a wrapped sentence and cannot be a heading.
        """
        text = ln.text
        if self.font_mode:
            if (ln.size, ln.bold) in self.cover_styles:
                return None
            if self._font_heading_candidate(ln):
                return self.style_level.get((ln.size, ln.bold), self.max_level)
            return None

        if continues or _words(text) > 15 or text.endswith((".", ",", ";")):
            return None
        m = NUMBERED.match(text)
        if m and m.group(2)[:1].isupper():
            return min(m.group(1).count(".") + 1, 4)
        if KEYWORD.match(text):
            return 1
        letters = [c for c in text if c.isalpha()]
        if len(letters) >= 4 and text.upper() == text and _words(text) <= 12:
            return 1
        if (paragraph_start and _words(text) <= 10 and len(text) <= 60
                and text[0].isupper() and not text.endswith("?")):
            return 3
        return None


# ---------------------------------------------------------------- document title

GENERIC_TITLES = re.compile(r"^(untitled|document\d*|microsoft word.*|title)$", re.I)


def infer_title(doc: ExtractedDoc) -> str:
    """PDF metadata title, else the large cover-page text on page 1, else the file name."""
    if doc.metadata_title and not GENERIC_TITLES.match(doc.metadata_title):
        return doc.metadata_title
    page1 = [l for l in doc.lines if l.page == 1 and l.kind == "text" and l.size]
    body = body_font_size(doc.lines)
    if page1 and body:
        biggest = max(l.size for l in page1)
        if biggest >= body * 1.3:
            parts = [l.text for l in page1 if l.size == biggest]
            title = " ".join(parts)
            if 1 <= _words(title) <= 15:
                return title
    return doc.title


# ---------------------------------------------------------------- main entry point

def chunk_document(doc: ExtractedDoc, doc_id: str, title: str, category: str,
                   max_words: int = 200) -> tuple[list[Chunk], ChunkStats]:
    stats = ChunkStats()
    lines, stats.running_lines_removed = remove_running_lines(doc.lines, doc.page_count)
    detector = HeadingDetector(lines)
    stats.mode = "font" if detector.font_mode else "text"
    stats.level_styles = [f"L{lvl}: {size}pt{' bold' if bold else ''}"
                          for (size, bold), lvl in detector.style_level.items()] if detector.font_mode else []

    stack: list[tuple[int, str]] = []       # (level, heading text)
    blocks: list[dict] = []
    current: dict | None = None
    prev_text: str | None = None
    prev_heading: Line | None = None
    in_toc, toc_page = False, 0

    def path() -> list[str]:
        return [t for _, t in stack]

    def flush():
        nonlocal current
        if current and current["parts"]:
            blocks.append(current)
        current = None

    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        continues = bool(nxt and nxt.kind == "text" and nxt.page == ln.page and nxt.text[:1].islower())

        if ln.kind == "table":
            flush()
            table_path = path()
            last = blocks[-1] if blocks else None
            # A table after a heading's own text belongs to the parent section, not that heading.
            if last and last["kind"] == "text" and last["path"] == table_path and table_path:
                table_path = table_path[:-1] + ["Table"]
            elif (last and last["kind"] == "table" and table_path
                  and last["path"][:len(table_path) - 1] == table_path[:-1]):
                table_path = last["path"]
            blocks.append({"path": table_path, "page": ln.page, "parts": [ln.text], "kind": "table"})
            stats.table_rows += 1
            prev_text, prev_heading = ln.text, None
            continue

        text = ln.text
        if TOC_TITLE.match(text):
            flush()
            in_toc, toc_page = True, ln.page
            stats.toc_lines_removed += 1
            continue
        if in_toc:
            if _is_toc_entry(text, ln.page == toc_page) and ln.page <= toc_page + 3:
                stats.toc_lines_removed += 1
                continue
            in_toc = False
        if DOT_LEADER.search(text):
            stats.toc_lines_removed += 1
            continue

        paragraph_start = prev_text is None or prev_heading is not None or _ends_sentence(prev_text)

        question = _is_question(text, paragraph_start)
        level = None if question else detector.level(ln, paragraph_start, continues)

        if question:
            flush()
            q_level = detector.max_level + 1
            while stack and stack[-1][0] >= q_level:
                stack.pop()
            stack.append((q_level, question))
            stats.headings += 1
            prev_text, prev_heading = text, None   # answers follow directly
            continue

        if level is not None:
            # A heading that wraps onto a second line: merge with the previous heading line.
            if (detector.font_mode and prev_heading is not None and stack and prev_heading.page == ln.page
                    and (prev_heading.size, prev_heading.bold) == (ln.size, ln.bold)
                    and ln.top - prev_heading.bottom < max(ln.size, 1) * 0.8):
                lvl, old = stack[-1]
                stack[-1] = (lvl, f"{old} {text}")
                prev_heading = ln
                continue
            flush()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            stats.headings += 1
            prev_text, prev_heading = text, ln
            continue

        if current is None or current["path"] != path():
            flush()
            current = {"path": path(), "page": ln.page, "parts": [], "kind": "text"}
        current["parts"].append(text)
        prev_text, prev_heading = text, None

    flush()
    stats.blocks = len(blocks)

    chunks: list[Chunk] = []
    seen: set[str] = set()
    for block in blocks:
        body = _join_lines(block["parts"])
        key = re.sub(r"\W+", " ", body.lower()).strip()
        if not key or key == re.sub(r"\W+", " ", title.lower()).strip():
            continue                      # empty, or just the document title from the cover page
        if key in seen:
            stats.duplicates_removed += 1
            continue
        seen.add(key)
        breadcrumb = " > ".join([title] + block["path"])
        for piece in split_long(body, max_words):
            idx = len(chunks)
            chunks.append(Chunk(
                id=f"{doc_id}::{idx:04d}",
                doc_id=doc_id,
                text=f"[{category}] {breadcrumb}: {piece}",
                body=piece,
                heading_path=list(block["path"]),
                page=block["page"],
                kind=block["kind"],
                index=idx,
            ))
    return chunks, stats
