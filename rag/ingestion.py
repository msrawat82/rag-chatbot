"""Turn PDF files into clean, labelled chunks ready for embedding.

Category comes from the folder a PDF sits in, e.g.
    data/vendor_policies/onboarding.pdf  ->  "Vendor Policies"
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import guardrails
from .chunking import PARSER_VERSION, Chunk, chunk_document, infer_title, make_doc_id
from .pdf_extract import extract_pdf


@dataclass
class DocumentInfo:
    path: Path
    relative_path: str
    category: str

    @property
    def doc_id(self) -> str:
        return make_doc_id(self.relative_path)


@dataclass
class DocumentReport:
    doc_id: str
    file: str
    title: str
    category: str
    sha256: str
    pages: int
    chunks: int
    mode: str
    headings: int
    tables: int
    duplicates_removed: int
    running_lines_removed: int
    empty_pages: list[int] = field(default_factory=list)
    pii_redacted: dict[str, int] = field(default_factory=dict)
    quarantined: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


ACRONYMS = {"hr", "it", "ai", "qa", "ip", "pr", "uk", "us", "eu", "esg", "hse", "ehs", "gdpr", "sla", "kyc", "aml"}


def category_from_folder(name: str) -> str:
    """'hr_policies' -> 'HR Policies', 'sub-contractor_policies' -> 'Sub Contractor Policies'."""
    words = re.sub(r"[_\-]+", " ", name).split()
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in words) or "General"


def discover_pdfs(inputs: list[str], data_dir: str, category_override: str | None = None) -> list[DocumentInfo]:
    """Find PDFs in the given files/folders. Category = first sub-folder under the data dir."""
    data_root = Path(data_dir).resolve()
    found: dict[str, DocumentInfo] = {}
    for item in inputs:
        p = Path(item)
        files = sorted(p.rglob("*.pdf")) + sorted(p.rglob("*.PDF")) if p.is_dir() else [p]
        for f in files:
            if not f.is_file():
                raise FileNotFoundError(f"PDF not found: {f}")
            full = f.resolve()
            try:
                rel = full.relative_to(data_root).as_posix()
            except ValueError:
                rel = f.name
            parts = Path(rel).parts
            category = category_override or (category_from_folder(parts[0]) if len(parts) > 1 else "General")
            found[rel] = DocumentInfo(path=full, relative_path=rel, category=category)
    return list(found.values())


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def process_document(info: DocumentInfo, max_words: int, pii_types: tuple[str, ...]
                     ) -> tuple[list[Chunk], DocumentReport]:
    doc = extract_pdf(info.path)
    title = infer_title(doc)
    chunks, stats = chunk_document(doc, info.doc_id, title, info.category, max_words=max_words)

    kept: list[Chunk] = []
    pii_total: dict[str, int] = {}
    quarantined: list[str] = []
    for c in chunks:
        # Security: passages that look like instructions to the AI are not indexed.
        if guardrails.detect_injection(c.body):
            quarantined.append(f"p.{c.page}: {c.body[:80]}")
            continue
        # Privacy: remove high-risk personal data before it is embedded or stored.
        c.body, found = guardrails.redact_pii(c.body, pii_types)
        c.text, _ = guardrails.redact_pii(c.text, pii_types)
        for k, v in found.items():
            pii_total[k] = pii_total.get(k, 0) + v
        kept.append(c)

    # Re-number so ids stay contiguous after quarantine.
    for i, c in enumerate(kept):
        c.index = i
        c.id = f"{info.doc_id}::{i:04d}"

    report = DocumentReport(
        doc_id=info.doc_id, file=info.relative_path, title=title, category=info.category,
        sha256=file_sha256(info.path), pages=doc.page_count, chunks=len(kept), mode=stats.mode,
        headings=stats.headings, tables=doc.table_count, duplicates_removed=stats.duplicates_removed,
        running_lines_removed=stats.running_lines_removed, empty_pages=doc.empty_pages,
        pii_redacted=pii_total, quarantined=quarantined)
    return kept, report


def chunk_metadata(chunk: Chunk, report: DocumentReport, ingested_at: str) -> dict:
    """Metadata stored next to each vector. Pinecone allows strings, numbers, booleans and string lists."""
    return {
        "text": chunk.text,
        "body": chunk.body,
        "doc_id": report.doc_id,
        "source": report.file,
        "title": report.title,
        "category": report.category,
        "section_path": " > ".join(chunk.heading_path),
        "section": chunk.heading_path[0] if chunk.heading_path else "",
        "page": chunk.page,
        "kind": chunk.kind,
        "chunk_index": chunk.index,
        "doc_sha256": report.sha256[:16],
        "ingested_at": ingested_at,
        "parser_version": report.parser_version,
    }
