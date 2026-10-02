"""Embedding provider abstraction (Phase 3).

Design doc 04: the production embedder is a small sentence-transformers model
(e.g. BAAI/bge-small-en-v1.5, ~130 MB, CPU-feasible on the target workstation)
selected after a Phase-3 retrieval-quality evaluation. That model download is
NOT available in this environment, so the default provider here is a
deterministic, dependency-free hashed n-gram embedder. It is genuinely usable
for lexical-ish semantic search (shared terminology -> shared trigrams -> high
cosine similarity) and keeps the full pipeline testable offline. Swap by
config (`AIQBE_EMBEDDING_PROVIDER=sentence_transformers`) once validated.

All vectors are L2-normalized so cosine == dot product.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

from pydantic import BaseModel


class EmbeddingInfo(BaseModel):
    provider: str
    model_id: str
    dim: int


class IEmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def info(self) -> EmbeddingInfo: ...


_WORD_RE = re.compile(r"[a-z0-9]+(?:['.][a-z]+)?")


def _normalize(text: str) -> str:
    return " ".join(w.lower() for w in _WORD_RE.findall(text))


class HashedNgramEmbedder(IEmbeddingProvider):
    """Deterministic feature-hashing over word uni/bi-grams.

    TF weighting with sublinear scaling + L2 norm. No training, no downloads,
    stable across processes (md5-based bucket assignment).
    """

    def __init__(self, dim: int = 512) -> None:
        self._dim = dim
        self._info = EmbeddingInfo(provider="hashed_ngram",
                                   model_id=f"hashed-ngram-{dim}", dim=dim)

    @property
    def info(self) -> EmbeddingInfo:
        return self._info

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _bucket(self, gram: str) -> tuple[int, float]:
        h = hashlib.md5(gram.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % self._dim
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def _embed_one(self, text: str) -> list[float]:
        words = _normalize(text).split()
        vec = [0.0] * self._dim
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        for a, b in zip(words, words[1:]):
            g = f"{a}_{b}"
            counts[g] = counts.get(g, 0) + 1
        for gram, c in counts.items():
            tf = 1.0 + math.log(c)
            idx, sign = self._bucket(gram)
            vec[idx] += sign * tf
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformersEmbedder(IEmbeddingProvider):  # pragma: no cover
    """Production embedder; requires `sentence-transformers` + model files."""

    def __init__(self, model_id: str = "BAAI/bge-small-en-v1.5") -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_id)
        self._info = EmbeddingInfo(provider="sentence_transformers",
                                   model_id=model_id,
                                   dim=int(self._model.get_sentence_embedding_dimension()))

    @property
    def info(self) -> EmbeddingInfo:
        return self._info

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]


_default: IEmbeddingProvider | None = None


def get_embedder() -> IEmbeddingProvider:
    global _default
    if _default is None:
        from backend.core.config import get_settings
        s = get_settings()
        kind = getattr(s, "embedding_provider", "hashed_ngram")
        if kind == "sentence_transformers":
            _default = SentenceTransformersEmbedder(
                getattr(s, "embedding_model", "BAAI/bge-small-en-v1.5"))
        else:
            _default = HashedNgramEmbedder(dim=getattr(s, "embedding_dim", 512))
    return _default


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed helper (chunked to bound memory on the target workstation)."""
    provider = get_embedder()
    out: list[list[float]] = []
    B = 64
    for i in range(0, len(texts), B):
        out.extend(provider.embed(texts[i:i + B]))
    return out
