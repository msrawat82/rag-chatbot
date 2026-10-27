"""Load policy PDFs into the vector index.

Put PDFs in sub-folders of data/; the folder name becomes the policy category:

    data/employee_policies/handbook.pdf      -> "Employee Policies"
    data/vendor_policies/onboarding.pdf      -> "Vendor Policies"

Examples:
    python ingest.py data/                    # add new/changed PDFs (unchanged files are skipped)
    python ingest.py data/ --dry-run          # check how PDFs will be chunked, upload nothing
    python ingest.py data/ --dry-run --show-chunks
    python ingest.py data/ --prune            # also remove documents that were deleted from data/
    python ingest.py data/ --recreate         # delete the whole index and rebuild it
    python ingest.py some.pdf --category "Client Policies"
    python ingest.py --remove data/vendor_policies/onboarding.pdf
    python ingest.py --list                   # show documents currently in the index
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag.audit import utc_now
from rag.chunking import PARSER_VERSION, make_doc_id
from rag.config import get_settings
from rag.ingestion import chunk_metadata, discover_pdfs, file_sha256, process_document


def print_report(r) -> None:
    flags = []
    if r.empty_pages:
        flags.append(f"⚠ {len(r.empty_pages)} page(s) without text (scanned? run OCR first): {r.empty_pages[:10]}")
    if r.quarantined:
        flags.append(f"⚠ {len(r.quarantined)} passage(s) quarantined (look like instructions to an AI):")
        flags += [f"    - {q}" for q in r.quarantined[:5]]
    if r.pii_redacted:
        flags.append(f"ℹ personal data redacted: {r.pii_redacted}")
    if r.mode == "text" and r.headings == 0:
        flags.append("⚠ no headings detected - chunks are split by length only")
    print(f"  {r.file}\n    title: {r.title} | category: {r.category} | pages: {r.pages} | chunks: {r.chunks}"
          f" | headings: {r.headings} ({r.mode} mode) | tables: {r.tables} | duplicates dropped: "
          f"{r.duplicates_removed}")
    for f in flags:
        print("    " + f)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="PDF files and/or folders (default: DATA_DIR)")
    parser.add_argument("--category", help="Category for all given PDFs (overrides folder names)")
    parser.add_argument("--dry-run", action="store_true", help="Chunk and report only; upload nothing")
    parser.add_argument("--show-chunks", action="store_true", help="With --dry-run: print every chunk")
    parser.add_argument("--force", action="store_true", help="Re-ingest files even if unchanged")
    parser.add_argument("--prune", action="store_true", help="Remove indexed documents not found in the paths")
    parser.add_argument("--recreate", action="store_true", help="Delete and rebuild the whole index")
    parser.add_argument("--remove", nargs="+", metavar="PDF_OR_DOC_ID", help="Remove documents from the index")
    parser.add_argument("--list", action="store_true", help="List documents in the index")
    args = parser.parse_args()
    s = get_settings()

    # ---------------------------------------------------------- read-only / removal commands
    if args.list or args.remove:
        store = _store(s, dimension=None, create=False)
        manifest = store.load_manifest() or {"documents": {}}
        if args.remove:
            for item in args.remove:
                doc_id = item if "::" not in item and not item.lower().endswith(".pdf") else _doc_id_for(item, s)
                n = store.delete_document(doc_id)
                manifest.get("documents", {}).pop(doc_id, None)
                print(f"Removed {n} chunks for {doc_id}")
            manifest["updated_at"] = utc_now()
            store.save_manifest(manifest)
        docs = manifest.get("documents", {})
        print(f"\nIndex '{s.index_name}' | embedding model: {manifest.get('embed_model', '?')} | "
              f"{len(docs)} document(s)")
        for d in sorted(docs.values(), key=lambda d: (d["category"], d["file"])):
            print(f"  [{d['category']}] {d['file']} - {d['chunks']} chunks, ingested {d['ingested_at']}")
        return 0

    # ---------------------------------------------------------- chunk the PDFs
    paths = args.paths or [s.data_dir]
    docs = discover_pdfs(paths, s.data_dir, args.category)
    if not docs:
        print(f"No PDFs found in {paths}. Put PDFs in sub-folders of {s.data_dir}/ (e.g. {s.data_dir}/hr_policies/).")
        return 1
    print(f"Found {len(docs)} PDF(s).")

    if args.dry_run:
        total = 0
        for info in docs:
            chunks, report = process_document(info, s.max_chunk_words, s.document_pii_types)
            total += len(chunks)
            print_report(report)
            if args.show_chunks:
                for c in chunks:
                    print(f"      [{c.id} | p.{c.page} | {c.kind}] {c.text}")
        print(f"\nDry run: {total} chunks from {len(docs)} document(s). Nothing was uploaded.")
        return 0

    if not s.pinecone_api_key:
        print("PINECONE_API_KEY is not set (environment variable or .env file).")
        return 1

    from rag.embeddings import Embedder

    embedder = Embedder(s.embed_model)
    if args.recreate:
        from rag.vectorstore import PineconeStore

        if PineconeStore.delete_index(s.pinecone_api_key, s.index_name):
            print(f"Deleted index '{s.index_name}'.")
    store = _store(s, dimension=embedder.dimension, create=True)
    manifest = store.load_manifest() or {}
    if manifest.get("embed_model") and manifest["embed_model"] != s.embed_model:
        print(f"The index was built with '{manifest['embed_model']}' but EMBED_MODEL is '{s.embed_model}'. "
              "Mixing models breaks search. Re-run with --recreate.")
        return 1
    documents: dict = manifest.get("documents", {})

    # ---------------------------------------------------------- upload changed documents
    reports = []
    for info in docs:
        sha = file_sha256(info.path)
        existing = documents.get(info.doc_id)
        if existing and existing.get("sha256") == sha and not args.force:
            print(f"  {info.relative_path}: unchanged, skipped")
            continue
        chunks, report = process_document(info, s.max_chunk_words, s.document_pii_types)
        print_report(report)
        removed = store.delete_document(info.doc_id)      # replace old version cleanly
        if removed:
            print(f"    replaced {removed} old chunk(s)")
        now = utc_now()
        vectors = embedder.embed_documents([c.text for c in chunks], show_progress=len(chunks) > 200)
        store.upsert([(c.id, v, chunk_metadata(c, report, now)) for c, v in zip(chunks, vectors)])
        documents[info.doc_id] = {
            "file": report.file, "title": report.title, "category": report.category,
            "sha256": report.sha256, "pages": report.pages, "chunks": report.chunks, "mode": report.mode,
            "empty_pages": len(report.empty_pages), "quarantined": len(report.quarantined),
            "pii_redacted": report.pii_redacted, "ingested_at": now}
        reports.append(report.to_dict())

    if args.prune:
        wanted = {d.doc_id for d in docs}
        for doc_id in [d for d in documents if d not in wanted]:
            n = store.delete_document(doc_id)
            print(f"  pruned {documents[doc_id]['file']} ({n} chunks)")
            documents.pop(doc_id)

    manifest = {"index": s.index_name, "embed_model": s.embed_model, "dimension": embedder.dimension,
                "parser_version": PARSER_VERSION, "updated_at": utc_now(), "documents": documents}
    store.save_manifest(manifest)

    # Keep a local record of every ingestion run (data lineage).
    log_dir = Path(s.log_dir) / "ingest_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_file = log_dir / f"{utc_now().replace(':', '-')}.json"
    run_file.write_text(json.dumps({"manifest": manifest, "changed": reports}, indent=2), encoding="utf-8")

    total = sum(d["chunks"] for d in documents.values())
    print(f"\nDone. Index '{s.index_name}' now holds {len(documents)} document(s), {total} chunks. "
          f"Run report: {run_file}")
    return 0


def _store(s, dimension, create):
    from rag.vectorstore import PineconeStore

    if not s.pinecone_api_key:
        sys.exit("PINECONE_API_KEY is not set (environment variable or .env file).")
    return PineconeStore(s.pinecone_api_key, s.index_name, dimension=dimension,
                         cloud=s.pinecone_cloud, region=s.pinecone_region, create=create)


def _doc_id_for(pdf_path: str, s) -> str:
    docs = discover_pdfs([pdf_path], s.data_dir) if Path(pdf_path).exists() else []
    if docs:
        return docs[0].doc_id
    rel = Path(pdf_path).resolve()
    try:
        rel_s = rel.relative_to(Path(s.data_dir).resolve()).as_posix()
    except ValueError:
        rel_s = Path(pdf_path).name
    return make_doc_id(rel_s)


if __name__ == "__main__":
    sys.exit(main())
