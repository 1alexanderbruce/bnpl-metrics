"""Ask a model for one figure from a document, and check the answer."""

from __future__ import annotations

import hashlib
import re

from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.parsing import ParseError, extract_json
from bnpl_metrics.extraction.retrieval import ChunkIndex
from bnpl_metrics.llm import LLM
from bnpl_metrics.models import ExtractedValue

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

SYSTEM_PROMPT = (
    "You extract one figure from excerpts of a financial document. Use only the excerpts; "
    "never estimate or recall figures from memory. "
    "Reply with a single JSON object and nothing else:\n"
    '{"value": <number or null>, '
    '"unit": "<e.g. billion USD, million USD, million, ratio, percent>", '
    '"confidence": "high|medium|low", '
    '"evidence": "<short verbatim quote containing the figure>"}\n'
    "Use null for value if the excerpts do not state the figure."
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower().strip("\"'")


class LLMExtractor:
    def __init__(self, llm: LLM, top_k: int = 6, use_embeddings: bool = True) -> None:
        self._llm = llm
        self._top_k = top_k
        self._use_embeddings = use_embeddings
        self._indexes: dict[str, ChunkIndex] = {}

    def _index(self, text: str) -> ChunkIndex:
        key = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        if key not in self._indexes:
            embed = self._llm.embed if self._use_embeddings else None
            self._indexes[key] = ChunkIndex(text, embed)
        return self._indexes[key]

    def ask_number(
        self,
        question: str,
        text: str,
        *,
        source: str = "",
        min_confidence: str = "medium",
    ) -> ExtractedValue:
        """Extract a number. Raises InputError if it is missing, unparsable or low confidence."""
        if min_confidence not in CONFIDENCE_ORDER:
            raise ValueError(f"unknown confidence level {min_confidence!r}")
        excerpts = self._index(text).top(question, self._top_k)
        if not excerpts:
            raise InputError("document has no text")
        prompt = (
            f"Question: {question}\n\nExcerpts:\n"
            + "\n\n---\n\n".join(excerpts)
            + "\n\nReply with the JSON object only."
        )
        reply = self._llm.complete(SYSTEM_PROMPT, prompt)
        try:
            data = extract_json(reply)
        except ParseError as exc:
            raise InputError(f"unparsable model reply: {exc}") from exc

        value = data.get("value")
        if value is None:
            raise InputError("the document does not state this figure")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputError(f"model returned a non-numeric value: {value!r}")

        confidence = str(data.get("confidence", "low")).lower()
        if confidence not in CONFIDENCE_ORDER:
            confidence = "low"
        evidence = str(data.get("evidence") or "").strip()
        note = None
        if not evidence or _squash(evidence) not in _squash(" ".join(excerpts)):
            confidence = "low"
            note = "evidence quote was missing or not found verbatim in the retrieved text"
        if CONFIDENCE_ORDER[confidence] < CONFIDENCE_ORDER[min_confidence]:
            raise InputError(
                f"confidence '{confidence}' is below the required '{min_confidence}'"
                + (f" ({note})" if note else "")
            )
        unit = data.get("unit")
        return ExtractedValue(
            value=float(value),
            unit=str(unit) if unit else None,
            source=source,
            confidence=confidence,
            evidence=evidence or None,
            note=note,
        )
