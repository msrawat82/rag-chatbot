"""Build an offline, in-memory copy of the knowledge base (no Pinecone, no API keys needed)."""

from __future__ import annotations

import tempfile

from rag.audit import AuditLog, utc_now
from rag.config import Settings
from rag.embeddings import Embedder
from rag.ingestion import chunk_metadata, discover_pdfs, process_document
from rag.pipeline import RAGPipeline
from rag.vectorstore import MemoryStore


def build_offline_pipeline(data_dir: str, settings: Settings) -> tuple[RAGPipeline, int]:
    embedder = Embedder(settings.embed_model)
    store = MemoryStore()
    total = 0
    for info in discover_pdfs([data_dir], data_dir):
        chunks, report = process_document(info, settings.max_chunk_words, settings.document_pii_types)
        vectors = embedder.embed_documents([c.text for c in chunks])
        now = utc_now()
        store.upsert([(c.id, v, chunk_metadata(c, report, now)) for c, v in zip(chunks, vectors)])
        total += len(chunks)
    audit = AuditLog(tempfile.mkdtemp())
    return RAGPipeline(settings, embedder, store, llm=None, audit=audit), total
