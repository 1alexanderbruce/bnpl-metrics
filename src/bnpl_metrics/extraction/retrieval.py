"""Chunk a document and pick the passages most relevant to a question."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable

import numpy as np

from bnpl_metrics.logging_utils import get_logger

log = get_logger(__name__)
Embedder = Callable[[list[str]], list[list[float]]]
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "and", "or", "to", "is", "are", "was", "what",
    "how", "does", "do", "that", "this", "with", "by", "as", "at", "its", "it", "be", "from",
}  # fmt: skip


def chunk_text(text: str, size: int = 4000, overlap: int = 300) -> list[str]:
    if size <= overlap:
        raise ValueError("chunk size must exceed overlap")
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            end = boundary if boundary != -1 else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def keyword_scores(question: str, chunks: list[str]) -> list[float]:
    """Term-overlap scores weighted by how rare each term is across the chunks."""
    query = set(_terms(question))
    docs = [Counter(_terms(c)) for c in chunks]
    frequency = Counter(term for doc in docs for term in doc)
    n = max(len(chunks), 1)
    scores = []
    for doc in docs:
        score = 0.0
        for term in query & doc.keys():
            score += (1 + math.log(doc[term])) * math.log(1 + n / frequency[term])
        scores.append(score)
    return scores


class ChunkIndex:
    """Chunks of one document plus, when an embedder is available, their vectors."""

    def __init__(self, text: str, embed: Embedder | None = None, size: int = 4000) -> None:
        self.chunks = chunk_text(text, size=size)
        self._embed = embed
        self._vectors: np.ndarray | None = None
        self.used_embeddings = False

    def top(self, question: str, k: int = 6) -> list[str]:
        if not self.chunks:
            return []
        scores = self._embedding_scores(question)
        if scores is None:
            scores = keyword_scores(question, self.chunks)
        order = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)[:k]
        return [self.chunks[i] for i in sorted(order)]  # keep document order for readability

    def _embedding_scores(self, question: str) -> list[float] | None:
        if self._embed is None:
            return None
        try:
            if self._vectors is None:
                self._vectors = _normalize(np.array(self._embed(self.chunks), dtype=float))
            query = _normalize(np.array(self._embed([question]), dtype=float))
        except Exception as exc:
            log.warning("embedding failed (%s); falling back to keyword search", exc)
            self._embed = None
            return None
        self.used_embeddings = True
        return (self._vectors @ query[0]).tolist()


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms
