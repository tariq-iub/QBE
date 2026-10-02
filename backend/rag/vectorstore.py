"""Vector store abstraction + local JSON index (Phase 3).

Design doc 04: production deployment uses Qdrant (embedded or sidecar). This
module defines the `IVectorStore` contract and ships a dependency-free local
JSON-file implementation suitable for single-workstation MVP operation and
offline tests. Switching to Qdrant is a config change (`AIQBE_VECTOR_STORE=qdrant`)
plus one adapter class — retrieval call-sites never change.

Points carry full payload metadata so search can filter by topic/source before
scoring (§7: semantic relevance AND metadata filters).
"""
from __future__ import annotations

import json
import math
import os
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class VectorPoint:
    point_id: str
    vector: list[float]
    payload: dict = field(default_factory=dict)


@dataclass
class SearchHit:
    point_id: str
    score: float
    payload: dict


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class IVectorStore(ABC):
    @abstractmethod
    def upsert(self, points: list[VectorPoint]) -> None: ...

    @abstractmethod
    def delete_by_payload(self, flt: dict) -> int: ...

    @abstractmethod
    def search(self, query: list[float], *, limit: int = 8,
               flt: dict | None = None) -> list[SearchHit]: ...

    @abstractmethod
    def count(self) -> int: ...


def _payload_matches(payload: dict, flt: dict) -> bool:
    """Filter semantics:
      - scalar value  => payload[key] == value
      - list value    => overlap (any element of the filter list is present in
        the payload value, where a scalar payload value counts as [value]).
    This gives Qdrant-style "match any of" for topic_ids lists and exact match
    for scalars like document_id.
    """
    def overlaps(pv, fv):
        pv_set = pv if isinstance(pv, (list, tuple, set)) else [pv]
        return any(x in pv_set for x in fv)

    for k, v in flt.items():
        pv = payload.get(k)
        if pv is None:
            return False
        if isinstance(v, (list, tuple, set)):
            if not overlaps(pv, v):
                return False
        elif pv != v:
            return False
    return True


class LocalJsonVectorStore(IVectorStore):
    """Persistent single-file vector index. O(n) scan; fine for MVP corpus
    sizes (tens of thousands of chunks on an 8 GB workstation). Not intended
    for millions of vectors — that is the documented growth path to Qdrant.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._points: dict[str, dict] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._points = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._points = {}

    def _flush(self) -> None:
        tmp = self._path + ".tmp"
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._points, f)
        os.replace(tmp, self._path)

    def upsert(self, points: list[VectorPoint]) -> None:
        with self._lock:
            for p in points:
                self._points[p.point_id] = {
                    "vector": [round(v, 6) for v in p.vector], "payload": p.payload}
            self._flush()

    def delete_by_payload(self, flt: dict) -> int:
        with self._lock:
            doomed = [pid for pid, rec in self._points.items()
                      if _payload_matches(rec["payload"], flt)]
            for pid in doomed:
                del self._points[pid]
            if doomed:
                self._flush()
            return len(doomed)

    def search(self, query: list[float], *, limit: int = 8,
               flt: dict | None = None) -> list[SearchHit]:
        with self._lock:
            scored: list[SearchHit] = []
            for pid, rec in self._points.items():
                if flt and not _payload_matches(rec["payload"], flt):
                    continue
                scored.append(SearchHit(point_id=pid,
                                        score=_cosine(query, rec["vector"]),
                                        payload=rec["payload"]))
            scored.sort(key=lambda h: h.score, reverse=True)
            return scored[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._points)


class QdrantVectorStore(IVectorStore):  # pragma: no cover - needs qdrant server
    """Thin adapter over the qdrant-client REST API (no extra SDK needed)."""

    def __init__(self, url: str, collection: str = "aiqbe_chunks") -> None:
        import httpx
        self._http = httpx.Client(base_url=url.rstrip("/"), timeout=30)
        self.collection = collection
        r = self._http.put(f"/collections/{collection}", json={
            "vectors": {"size": 512, "distance": "Cosine"}})
        if r.status_code >= 400 and "already exists" not in r.text.lower():
            r.raise_for_status()

    def upsert(self, points: list[VectorPoint]) -> None:
        self._http.post(f"/collections/{self.collection}/points", json={
            "points": [{"id": p.point_id, "vector": p.vector, "payload": p.payload}
                       for p in points]}).raise_for_status()

    def delete_by_payload(self, flt: dict) -> int:  # exact count not returned
        must = [{"key": k, "match": {"value": v}} for k, v in flt.items()
                if not isinstance(v, (list, tuple, set))]
        self._http.post(f"/collections/{self.collection}/points/delete",
                        json={"filter": {"must": must}}).raise_for_status()
        return -1

    def search(self, query: list[float], *, limit: int = 8,
               flt: dict | None = None) -> list[SearchHit]:
        body: dict = {"vector": query, "limit": limit}
        if flt:
            body["filter"] = {"must": [{"key": k, "match": {"value": v}}
                                      for k, v in flt.items()]}
        r = self._http.post(f"/collections/{self.collection}/points/search",
                            json=body).raise_for_status()
        return [SearchHit(point_id=str(h["id"]), score=float(h["score"]),
                          payload=h.get("payload", {}))
                for h in r.json()["result"]]

    def count(self) -> int:  # pragma: no cover
        r = self._http.get(f"/collections/{self.collection}/count").raise_for_status()
        return int(r.json()["result"]["count"])


_store: IVectorStore | None = None


def reset_vector_store() -> None:
    """Test hook: drop the cached singleton (e.g. after changing AIQBE_DATA_DIR)."""
    global _store
    _store = None


def get_vector_store() -> IVectorStore:
    global _store
    if _store is None:
        from backend.core.config import get_settings
        s = get_settings()
        kind = getattr(s, "vector_store", "local_json")
        if kind == "qdrant":
            _store = QdrantVectorStore(getattr(s, "qdrant_url", "http://localhost:6333"))
        else:
            base = getattr(s, "data_dir", "./data")
            _store = LocalJsonVectorStore(os.path.join(base, "vector_index.json"))
    return _store
