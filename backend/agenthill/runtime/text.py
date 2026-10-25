"""Small, dependency-free text utilities: tokenisation, light stemming and TF-IDF cosine."""
from __future__ import annotations

import math
import re
from collections import Counter

STOPWORDS = set("""
a an the and or of to in on for with by at from as is are was were be been being it its this that these those
i me my we our you your he she they them their his her can could should would will shall may might must do does
did done have has had not no so if then than when where which who whom what how why all any each some such only
own same too very just also into out up down over under again further once here there about after before between
during through above below off both few more most other because until while please hi hello thanks thank need want
get got im i'm cant can't dont don't isnt keep keeps
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def stem(word: str) -> str:
    w = word
    for suf in ("ations", "ation", "ions", "ion", "ings", "ing", "edly", "ly", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w


def tokens(text: str, keep_stop: bool = False) -> list[str]:
    out = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        for part in raw.replace("_", "-").split("-"):
            if not part or (not keep_stop and part in STOPWORDS):
                continue
            out.append(stem(part))
    return out


class TfIdf:
    """Binary-TF, smoothed-IDF vector space fitted on a small corpus."""

    def __init__(self, docs: list[str]):
        self.doc_tokens = [set(tokens(d)) for d in docs]
        n = len(docs)
        df: Counter = Counter()
        for toks in self.doc_tokens:
            df.update(toks)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.default_idf = math.log(n + 1) + 1.0
        self.doc_vecs = [self.vector_from_tokens(t) for t in self.doc_tokens]

    def vector_from_tokens(self, toks: set[str]) -> dict[str, float]:
        return {t: self.idf.get(t, self.default_idf) for t in toks}

    def vector(self, text: str) -> dict[str, float]:
        return self.vector_from_tokens(set(tokens(text)))

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(w * b[t] for t, w in a.items() if t in b)
        na = math.sqrt(sum(w * w for w in a.values()))
        nb = math.sqrt(sum(w * w for w in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def scores(self, text: str) -> list[float]:
        q = self.vector(text)
        return [self.cosine(q, d) for d in self.doc_vecs]


def overlap_terms(a: str, b: str) -> list[str]:
    return sorted(set(tokens(a)) & set(tokens(b)))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]
