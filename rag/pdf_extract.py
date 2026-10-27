"""PDF -> lines of text with layout hints (font size, bold, position) and tables as rows.

Works for any text-based PDF. Scanned (image-only) pages have no text layer and are
reported in `empty_pages` so they can be OCR'd before ingestion.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

BOLD_HINTS = ("bold", "black", "heavy", "semibold", "demi")
CID_ARTIFACT = re.compile(r"\(cid:\d+\)")
BULLETS = re.compile(r"^[•●▪■‣⁃∙•·▪■◦\-–*]\s+")


@dataclass
class Line:
    text: str
    page: int                 # 1-based
    size: float = 0.0         # median font size
    bold: bool = False        # most characters bold
    top: float = 0.0
    bottom: float = 0.0
    kind: str = "text"        # "text" or "table"


@dataclass
class ExtractedDoc:
    path: str
    title: str                # best guess; see chunking.infer_title for the final choice
    page_count: int
    metadata_title: str = ""
    lines: list[Line] = field(default_factory=list)
    empty_pages: list[int] = field(default_factory=list)
    table_count: int = 0


def clean_text(text: str) -> str:
    text = CID_ARTIFACT.sub("", text)
    text = text.replace("­", "")               # soft hyphen
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"[“”]", '"', text)
    text = re.sub(r"\s+", " ", text).strip()
    return BULLETS.sub("", text)


def _is_bold(fontname: str) -> bool:
    name = fontname.lower()
    return any(hint in name for hint in BOLD_HINTS)


def _inside(line: dict, bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    cx = (line["x0"] + line["x1"]) / 2
    cy = (line["top"] + line["bottom"]) / 2
    return x0 - 1 <= cx <= x1 + 1 and top - 1 <= cy <= bottom + 1


def table_rows_to_text(rows: list[list[str | None]]) -> list[str]:
    """Turn a table into one readable sentence per row, e.g. 'Topic: Annual PTO; Baseline: 20 days'."""
    cleaned = [[clean_text(c or "") for c in row] for row in rows if row]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return []
    header = cleaned[0]
    has_header = len(cleaned) > 1 and all(header) and all(len(h) <= 40 for h in header)
    body = cleaned[1:] if has_header else cleaned
    out = []
    for row in body:
        if has_header:
            parts = [f"{h}: {v}" for h, v in zip(header, row) if v]
        else:
            parts = [v for v in row if v]
        if parts:
            out.append("; ".join(parts) + ".")
    return out


def extract_pdf(path: str | Path) -> ExtractedDoc:
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        meta_title = clean_text(str((pdf.metadata or {}).get("Title") or ""))
        doc = ExtractedDoc(path=str(path), title=_title_from_filename(path),
                           page_count=len(pdf.pages), metadata_title=meta_title)

        for page_no, page in enumerate(pdf.pages, start=1):
            try:
                tables = page.find_tables()
            except Exception:
                tables = []
            bboxes = [t.bbox for t in tables]

            raw_lines = page.extract_text_lines(return_chars=True) or []
            if not raw_lines and not tables:
                doc.empty_pages.append(page_no)
                continue

            items: list[Line] = []
            for raw in raw_lines:
                if any(_inside(raw, b) for b in bboxes):
                    continue
                text = clean_text(raw["text"])
                if not text:
                    continue
                chars = [c for c in raw.get("chars", []) if c.get("text", "").strip()]
                size = statistics.median(c["size"] for c in chars) if chars else 0.0
                bold = bool(chars) and sum(_is_bold(c.get("fontname", "")) for c in chars) > len(chars) / 2
                items.append(Line(text=text, page=page_no, size=round(size, 1), bold=bold,
                                  top=raw["top"], bottom=raw["bottom"]))

            for table in tables:
                try:
                    rows = table.extract()
                except Exception:
                    continue
                row_texts = table_rows_to_text(rows)
                if not row_texts:
                    continue
                doc.table_count += 1
                x0, top, x1, bottom = table.bbox
                step = (bottom - top) / max(len(row_texts), 1)
                for i, row_text in enumerate(row_texts):
                    items.append(Line(text=row_text, page=page_no, top=top + i * step,
                                      bottom=top + (i + 1) * step, kind="table"))

            items.sort(key=lambda ln: ln.top)
            doc.lines.extend(items)

    return doc


def _title_from_filename(path: Path) -> str:
    return re.sub(r"[_\-]+", " ", path.stem).strip().title()


def body_font_size(lines: list[Line]) -> float:
    """The most common font size, weighted by text length = the body text size."""
    counts: Counter[float] = Counter()
    for ln in lines:
        if ln.kind == "text" and ln.size:
            counts[ln.size] += len(ln.text)
    return counts.most_common(1)[0][0] if counts else 0.0
