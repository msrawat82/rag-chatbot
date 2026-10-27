"""Vector storage. `PineconeStore` is used in production; `MemoryStore` has the same interface
and runs fully offline (used by tests and the evaluation scripts)."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

MANIFEST_NAMESPACE = "__system__"
MANIFEST_ID = "manifest"


@dataclass
class Match:
    id: str
    score: float
    metadata: dict[str, Any]


class VectorStore(Protocol):
    def upsert(self, items: list[tuple[str, list[float], dict]]) -> None: ...
    def query(self, vector: list[float], top_k: int, categories: list[str] | None = None) -> list[Match]: ...
    def delete_document(self, doc_id: str) -> int: ...
    def load_manifest(self) -> dict: ...
    def save_manifest(self, manifest: dict) -> None: ...


def _category_filter(categories: list[str] | None) -> dict | None:
    return {"category": {"$in": list(categories)}} if categories else None


class PineconeStore:
    def __init__(self, api_key: str, index_name: str, dimension: int | None = None,
                 cloud: str = "aws", region: str = "us-east-1", create: bool = False):
        from pinecone import Pinecone, ServerlessSpec

        self.pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        existing = self.pc.list_indexes().names()
        if index_name not in existing:
            if not create:
                raise RuntimeError(f"Pinecone index '{index_name}' does not exist. Run ingest.py first.")
            self.pc.create_index(name=index_name, dimension=dimension, metric="cosine",
                                 spec=ServerlessSpec(cloud=cloud, region=region))
            while not self.pc.describe_index(index_name).status["ready"]:
                time.sleep(2)
        self.dimension = int(self.pc.describe_index(index_name).dimension)
        if dimension and dimension != self.dimension:
            raise RuntimeError(f"Index '{index_name}' has dimension {self.dimension}, but the embedding "
                               f"model produces {dimension}. Use --recreate or a different INDEX_NAME.")
        self.index = self.pc.Index(index_name)

    @staticmethod
    def delete_index(api_key: str, index_name: str) -> bool:
        from pinecone import Pinecone

        pc = Pinecone(api_key=api_key)
        if index_name in pc.list_indexes().names():
            pc.delete_index(index_name)
            return True
        return False

    def upsert(self, items: list[tuple[str, list[float], dict]]) -> None:
        for start in range(0, len(items), 100):
            batch = [{"id": i, "values": v, "metadata": m} for i, v, m in items[start:start + 100]]
            self.index.upsert(vectors=batch)

    def query(self, vector: list[float], top_k: int, categories: list[str] | None = None) -> list[Match]:
        res = self.index.query(vector=vector, top_k=top_k, include_metadata=True,
                               filter=_category_filter(categories))
        return [Match(m.id, float(m.score), dict(m.metadata or {})) for m in (res.matches or [])]

    def list_ids(self, prefix: str) -> list[str]:
        return [i for page in self.index.list(prefix=prefix) for i in page]

    def delete_document(self, doc_id: str) -> int:
        ids = self.list_ids(f"{doc_id}::")
        for start in range(0, len(ids), 1000):
            self.index.delete(ids=ids[start:start + 1000])
        return len(ids)

    # The manifest (list of ingested documents, models used) is stored inside the index itself,
    # so every machine running the app sees the same data-lineage information. It is split into
    # parts because Pinecone limits metadata to 40 KB per record.
    def load_manifest(self) -> dict:
        first = self.index.fetch(ids=[f"{MANIFEST_ID}-000"], namespace=MANIFEST_NAMESPACE).vectors or {}
        head = first.get(f"{MANIFEST_ID}-000")
        if not head or not head.metadata:
            return {}
        total = int(head.metadata.get("parts", 1))
        ids = [f"{MANIFEST_ID}-{n:03d}" for n in range(1, total)]
        rest = self.index.fetch(ids=ids, namespace=MANIFEST_NAMESPACE).vectors if ids else {}
        parts = [head.metadata["json"]] + [rest[i].metadata["json"] for i in ids]
        return json.loads("".join(parts))

    def save_manifest(self, manifest: dict) -> None:
        text = json.dumps(manifest, separators=(",", ":"))
        parts = [text[i:i + 30000] for i in range(0, len(text), 30000)] or ["{}"]
        unit = [1.0] + [0.0] * (self.dimension - 1)
        self.index.upsert(vectors=[{"id": f"{MANIFEST_ID}-{n:03d}", "values": unit,
                                    "metadata": {"json": part, "parts": len(parts)}}
                                   for n, part in enumerate(parts)], namespace=MANIFEST_NAMESPACE)


class MemoryStore:
    """In-memory store with the same interface, for tests and offline evaluation."""

    def __init__(self):
        self.items: dict[str, tuple[list[float], dict]] = {}
        self.manifest: dict = {}

    def upsert(self, items):
        for i, v, m in items:
            self.items[i] = (v, m)

    def query(self, vector, top_k, categories=None):
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
            return dot / (na * nb) if na and nb else 0.0

        scored = [Match(i, cos(vector, v), m) for i, (v, m) in self.items.items()
                  if not categories or m.get("category") in categories]
        return sorted(scored, key=lambda m: -m.score)[:top_k]

    def delete_document(self, doc_id):
        ids = [i for i in self.items if i.startswith(f"{doc_id}::")]
        for i in ids:
            del self.items[i]
        return len(ids)

    def load_manifest(self):
        return self.manifest

    def save_manifest(self, manifest):
        self.manifest = manifest
