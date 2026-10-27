from pathlib import Path

import pytest

from rag.chunking import chunk_document, make_doc_id, remove_running_lines, split_long
from rag.ingestion import DocumentInfo, discover_pdfs, process_document
from rag.pdf_extract import ExtractedDoc, Line


def doc_from(lines: list[Line], pages: int = 1) -> ExtractedDoc:
    return ExtractedDoc(path="x.pdf", title="Test Doc", page_count=pages, lines=lines)


def paths(chunks):
    return [" > ".join(c.heading_path) for c in chunks]


def test_font_mode_uses_font_sizes_for_levels():
    lines = [
        Line("1. Leave", 2, size=16, bold=True),
        Line("Annual leave", 2, size=11, bold=True),
        Line("Staff receive 25 days per year.", 2, size=9),
        Line("Sick leave", 2, size=11, bold=True),
        Line("Notify your manager on the first day.", 2, size=9),
        Line("2. Travel", 3, size=16, bold=True),
        Line("Book economy class for flights under six hours.", 3, size=9),
    ]
    chunks, stats = chunk_document(doc_from(lines, 3), "d", "Handbook", "HR Policies")
    assert stats.mode == "font"
    assert paths(chunks) == ["1. Leave > Annual leave", "1. Leave > Sick leave", "2. Travel"]
    assert chunks[0].text.startswith("[HR Policies] Handbook > 1. Leave > Annual leave: Staff receive")


def test_text_mode_numbered_keyword_and_caps_headings():
    lines = [
        Line("ARTICLE I - SCOPE", 1, size=10),
        Line("This applies to all suppliers.", 1, size=10),
        Line("2.1 Due diligence", 1, size=10),
        Line("Procurement checks ownership and sanctions lists before onboarding a supplier. Contact", 1, size=10),
        Line("the vendor desk for questions.", 1, size=10),
        Line("CODE OF CONDUCT", 1, size=10),
        Line("Be respectful.", 1, size=10),
    ]
    chunks, stats = chunk_document(doc_from(lines), "d", "Vendor Policy", "Vendor Policies")
    assert stats.mode == "text"
    assert paths(chunks) == ["ARTICLE I - SCOPE", "ARTICLE I - SCOPE > 2.1 Due diligence", "CODE OF CONDUCT"]
    # The wrapped body line must not be mistaken for a heading.
    assert "Contact the vendor desk" in chunks[1].body


def test_single_font_document_with_bold_table_header_uses_numbered_headings():
    lines = [Line("Role Responsibility", 1, size=10, bold=True), Line("Owner Approves exceptions", 1, size=10)]
    names = ["SCOPE", "PRICING", "BILLING", "SUPPORT", "EXIT"]
    for n, name in enumerate(names, 1):
        lines += [Line(f"Overview text for {name.lower()}.", n, size=10), Line(f"{n}. {name}", n, size=10),
                  Line(f"{n}.1 {name.title()} rules", n, size=10),
                  Line(f"Rules for {name.lower()} apply to everyone.", n, size=10),
                  Line(f"Contact the {name.lower()} desk with questions.", n, size=10)]
    lines += [Line("Level Target", 5, size=10, bold=True), Line("L1 5%", 5, size=10)]
    chunks, stats = chunk_document(doc_from(lines, 5), "d", "Policy", "Client Policies")
    assert stats.mode == "text"
    assert "2. PRICING > 2.1 Pricing rules" in paths(chunks)


def test_running_headers_footers_and_page_numbers_removed():
    lines = []
    for p in range(1, 6):
        lines += [Line("ACME Confidential", p, size=8, top=10), Line(f"Body text on page {p}.", p, size=10, top=50),
                  Line(f"Page {p} of 5", p, size=8, top=700)]
    kept, removed = remove_running_lines(lines, 5)
    assert removed == 10
    assert all("ACME" not in l.text and "Page" not in l.text for l in kept)


def test_table_of_contents_removed():
    lines = [
        Line("Contents", 1, size=16, bold=True),
        Line("1. Scope", 1, size=10),
        Line("2. Payments ........ 4", 1, size=10),
        Line("Scope", 2, size=14, bold=True),
        Line("All vendors are covered.", 2, size=10),
    ]
    chunks, stats = chunk_document(doc_from(lines, 2), "d", "T", "C")
    assert stats.toc_lines_removed == 3
    assert [c.body for c in chunks] == ["All vendors are covered."]


def test_faq_lines_become_their_own_chunks():
    lines = [
        Line("FAQ", 1, size=16, bold=True),
        Line("Q: When are expenses due?", 1, size=10),
        Line("Within 30 days.", 1, size=10),
        Line("Can I carry over leave?", 1, size=10),
        Line("Up to five days.", 1, size=10),
    ]
    chunks, _ = chunk_document(doc_from(lines, 2), "d", "T", "C")
    assert [c.heading_path[-1] for c in chunks] == ["Q: When are expenses due?", "Can I carry over leave?"]


def test_duplicate_passages_are_dropped():
    lines = [Line("A", 1, size=14, bold=True), Line("Same boilerplate text.", 1, size=10),
             Line("B", 2, size=14, bold=True), Line("Same boilerplate text.", 2, size=10)]
    chunks, stats = chunk_document(doc_from(lines, 2), "d", "T", "C")
    assert len(chunks) == 1 and stats.duplicates_removed == 1


def test_split_long_respects_limit_and_overlaps():
    text = " ".join(f"Sentence number {i} is here." for i in range(60))
    pieces = split_long(text, 50)
    assert all(len(p.split()) <= 50 for p in pieces)
    assert len(pieces) > 1
    last_sentence_of_first = pieces[0].split(". ")[-1]
    assert last_sentence_of_first.rstrip(".") in pieces[1]


def test_doc_ids_are_stable_and_unique():
    assert make_doc_id("hr/leave.pdf") == make_doc_id("hr/leave.pdf")
    assert make_doc_id("hr/leave.pdf") != make_doc_id("finance/leave.pdf")


# ---------------------------------------------------------------- end-to-end on real PDFs

reportlab = pytest.importorskip("reportlab")


@pytest.fixture(scope="module")
def sample_dir(tmp_path_factory) -> Path:
    from tests.sample_pdfs import build_finance_pdf, build_hr_pdf, build_vendor_pdf

    root = tmp_path_factory.mktemp("data")
    for folder, fn in [("vendor_policies", build_vendor_pdf), ("finance_policies", build_finance_pdf),
                       ("hr_policies", build_hr_pdf)]:
        (root / folder).mkdir()
        fn(root / folder / f"{folder}.pdf")
    return root


def test_discover_assigns_categories_from_folders(sample_dir):
    docs = discover_pdfs([str(sample_dir)], str(sample_dir))
    assert {d.category for d in docs} == {"Vendor Policies", "Finance Policies", "HR Policies"}


def test_vendor_pdf_text_mode_toc_header_and_quarantine(sample_dir):
    info = DocumentInfo(sample_dir / "vendor_policies/vendor_policies.pdf", "vendor_policies/v.pdf", "Vendor")
    chunks, report = process_document(info, 200, ("ssn", "credit_card", "iban"))
    assert report.mode == "text"
    assert report.title == "ACME Vendor Policy"
    joined = " ".join(c.text for c in chunks)
    assert "ARTICLE I - SCOPE" in joined and "Confidential" not in joined and "....." not in joined
    assert len(report.quarantined) == 1                       # the "ignore all previous instructions" passage
    assert "Ignore all previous instructions" not in joined


def test_finance_pdf_font_mode_tables_and_questions(sample_dir):
    info = DocumentInfo(sample_dir / "finance_policies/finance_policies.pdf", "finance_policies/f.pdf", "Finance")
    chunks, report = process_document(info, 200, ())
    assert report.mode == "font" and report.tables == 1
    assert report.title == "Finance Policy Manual"
    table_rows = [c for c in chunks if c.kind == "table"]
    assert any("Hotel per night; Limit: $250" in c.body for c in table_rows)
    assert any(c.heading_path[-1] == "Q: When are expense reports due?" for c in chunks)
    # A long heading wrapped across two lines is merged back together.
    assert any("Who Travel Frequently" in " ".join(c.heading_path) for c in chunks)
