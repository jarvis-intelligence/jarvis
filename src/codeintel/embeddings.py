"""Self-hosted embedding model wrapper (sentence-transformers, lazy-loaded).

The model is never loaded at MCP server startup — only on the first
embed call, mirroring ZoektLifecycle's lazy-spawn pattern. Vectors from
different models must never mix (see semantic.py's model-identity rule),
so the (model_name, revision) identity travels with every embedding.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_BATCH_SIZE = 8
# bge-m3 defaults to 8192 — far beyond our 512-token chunk target and the
# tokenizer's real token count can exceed our chars//4 estimate. Capping well
# above the target but far below the model's default keeps a runaway chunk
# (or an estimate that undercounts) from blowing up encode-time memory:
# attention cost scales with batch x sequence_length^2.
MAX_SEQ_LENGTH = 1024
_INSTALL_HINT = "semantic search requires the 'semantic' extra: uv sync --extra semantic"


class SemanticExtraMissingError(Exception):
    """The `semantic` optional dependencies are not installed."""


class EmbeddingModel:
    def __init__(self, model_name: str | None = None, revision: str | None = None,
                 batch_size: int | None = None) -> None:
        self.model_name = model_name or os.environ.get("CODEINTEL_EMBEDDING_MODEL", DEFAULT_MODEL)
        if revision is not None:
            self.revision = revision
        else:
            self.revision = DEFAULT_REVISION if self.model_name == DEFAULT_MODEL else "unpinned"
        self.batch_size = batch_size or int(os.environ.get("CODEINTEL_EMBEDDING_BATCH_SIZE",
                                                           DEFAULT_BATCH_SIZE))
        self._model = None

    def identity(self) -> tuple[str, str]:
        return (self.model_name, self.revision)

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise SemanticExtraMissingError(_INSTALL_HINT) from exc
            revision = None if self.revision == "unpinned" else self.revision
            self._model = SentenceTransformer(self.model_name, revision=revision,
                                              trust_remote_code=True)
            self._model.max_seq_length = MAX_SEQ_LENGTH
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            # normalize_embeddings: L2-normalize so cosine ranking at query
            # time is exact (standard hubness mitigation).
            for vec in model.encode(texts[i:i + self.batch_size], normalize_embeddings=True):
                vectors.append(list(vec) if not hasattr(vec, "tolist") else vec.tolist())
        return vectors

    def count_oversized(self, texts: list[str]) -> int:
        """How many of `texts` the model will silently truncate at encode
        time. Uses the real tokenizer rather than the chunker's chars//4
        estimate: that estimate is precisely what MAX_SEQ_LENGTH backstops,
        so measuring it with the same approximation would be circular.

        A separate tokenize pass is unavoidable — `encode()` tokenizes
        internally but never exposes the counts. Overhead is ~1-2% against
        the transformer forward pass. Empty input returns 0 without loading
        the model, keeping unit tests offline."""
        if not texts:
            return 0
        tokenizer = self._load().tokenizer
        oversized = 0
        for i in range(0, len(texts), self.batch_size):
            encoded = tokenizer(texts[i:i + self.batch_size])["input_ids"]
            oversized += sum(1 for ids in encoded if len(ids) > MAX_SEQ_LENGTH)
        return oversized

    def embed_query(self, query: str) -> list[float]:
        # bge-m3 needs no query-side instruction prefix (unlike earlier BGE
        # versions) — encode the raw query text directly.
        return self.embed_texts([query])[0]


_default: EmbeddingModel | None = None


def default_model() -> EmbeddingModel:
    global _default
    if _default is None:
        _default = EmbeddingModel()
    return _default
