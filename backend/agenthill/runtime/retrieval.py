"""Knowledge retrieval over an environment's synthetic knowledge base (TF-IDF cosine)."""
from __future__ import annotations

from functools import lru_cache

from ..environments.base import get_environment
from .text import TfIdf, overlap_terms


def _doc_text(a: dict) -> str:
    return " ".join([a["title"], a["title"], " ".join(a.get("tags", [])), a["body"], " ".join(s["text"] for s in a.get("steps", []))])


@lru_cache(maxsize=8)
def _index(env_name: str) -> tuple[TfIdf, list[dict]]:
    env = get_environment(env_name)
    return TfIdf([_doc_text(a) for a in env.knowledge]), env.knowledge


def retrieve(env_name: str, query: str, top_k: int, min_score: float) -> tuple[list[dict], list[dict]]:
    """Return (accepted documents, all scored candidates) for the query."""
    index, articles = _index(env_name)
    scored = sorted(zip(index.scores(query), articles), key=lambda x: -x[0])
    candidates = [{"id": a["id"], "title": a["title"], "score": round(s, 4), "source": a.get("source", "official")} for s, a in scored[:6]]
    accepted = []
    for s, a in scored[: max(0, int(top_k))]:
        if s < min_score:
            continue
        accepted.append({"id": a["id"], "title": a["title"], "score": round(s, 4), "source": a.get("source", "official"),
                         "matched_terms": overlap_terms(query, _doc_text(a))[:12], "body": a["body"], "article": a})
    return accepted, candidates
