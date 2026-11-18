"""Thin OpenAI-compatible client with timeouts and retries.

Anything with ``complete`` and ``embed`` satisfies the ``LLM`` protocol, so tests and other
providers can be swapped in.
"""

from __future__ import annotations

from typing import Protocol

from bnpl_metrics.settings import Settings


class LLM(Protocol):
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings, batch_size: int = 64) -> None:
        from openai import OpenAI

        self._settings = settings
        self._batch_size = batch_size
        self._client = OpenAI(
            api_key=settings.require_api_key(),
            base_url=settings.base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        response = self._client.chat.completions.create(
            model=self._settings.llm_model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("the model returned an empty reply")
        return content

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = self._client.embeddings.create(
                model=self._settings.embedding_model, input=batch
            )
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors
