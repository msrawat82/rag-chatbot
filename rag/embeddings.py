"""Text -> vectors, with the query/passage prefixes some models expect."""

from __future__ import annotations

from functools import cached_property


class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        name = model_name.lower()
        if "bge" in name and "-en" in name:
            self.query_prefix, self.passage_prefix = "Represent this sentence for searching relevant passages: ", ""
        elif "e5" in name:
            self.query_prefix, self.passage_prefix = "query: ", "passage: "
        else:
            self.query_prefix = self.passage_prefix = ""

    @cached_property
    def model(self):
        from sentence_transformers import SentenceTransformer  # heavy import, load lazily

        return SentenceTransformer(self.model_name)

    @property
    def dimension(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        vectors = self.model.encode([self.passage_prefix + t for t in texts], batch_size=64,
                                    normalize_embeddings=True, show_progress_bar=show_progress)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.model.encode(self.query_prefix + text, normalize_embeddings=True).tolist()
