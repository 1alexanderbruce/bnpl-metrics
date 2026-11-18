import json

import pytest

from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.llm_extractor import LLMExtractor
from bnpl_metrics.extraction.retrieval import ChunkIndex, chunk_text, keyword_scores

DOC = (
    "Overview of the business and its history.\n\n" * 40
    + "Gross merchandise volume was $12.5 billion for the full year, up 10%.\n\n"
    + "Properties and facilities are leased under various agreements.\n\n" * 40
)


class FakeLLM:
    def __init__(self, reply, embed_error=None):
        self.reply = reply
        self.embed_error = embed_error
        self.prompts = []
        self.embed_calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.prompts.append(user)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)

    def embed(self, texts):
        self.embed_calls += 1
        if self.embed_error:
            raise self.embed_error
        vocabulary = ["merchandise", "volume", "billion", "properties", "business"]
        return [[t.lower().count(w) for w in vocabulary] or [0] for t in texts]


def answer(**overrides):
    base = {
        "value": 12.5,
        "unit": "billion USD",
        "confidence": "high",
        "evidence": "Gross merchandise volume was $12.5 billion for the full year",
    }
    base.update(overrides)
    return base


def test_chunking_covers_text_with_overlap():
    chunks = chunk_text("word " * 3000, size=1000, overlap=100)
    assert len(chunks) > 10
    assert all(len(c) <= 1000 for c in chunks)
    assert chunk_text("") == []
    assert chunk_text("short") == ["short"]
    with pytest.raises(ValueError):
        chunk_text("x", size=10, overlap=10)


def test_keyword_scores_prefer_relevant_chunk():
    chunks = ["leases and properties", "gross merchandise volume figure", "risk factors"]
    scores = keyword_scores("what is gross merchandise volume", chunks)
    assert scores.index(max(scores)) == 1


def test_index_uses_embeddings_when_available():
    llm = FakeLLM(answer())
    index = ChunkIndex(DOC, llm.embed, size=500)
    top = index.top("gross merchandise volume billion", k=2)
    assert any("12.5 billion" in chunk for chunk in top)
    assert index.used_embeddings


def test_index_falls_back_to_keywords_when_embeddings_fail(caplog):
    llm = FakeLLM(answer(), embed_error=RuntimeError("no embeddings endpoint"))
    index = ChunkIndex(DOC, llm.embed, size=500)
    top = index.top("gross merchandise volume billion", k=2)
    assert any("12.5 billion" in chunk for chunk in top)
    assert not index.used_embeddings
    assert "falling back to keyword search" in caplog.text


def test_extractor_returns_typed_value():
    extractor = LLMExtractor(FakeLLM(answer()))
    value = extractor.ask_number("What was GMV?", DOC, source="filing:test")
    assert value.value == 12.5
    assert value.unit == "billion USD"
    assert value.confidence == "high"
    assert value.source == "filing:test"
    assert "12.5 billion" in value.evidence


def test_extractor_reuses_index_across_questions():
    llm = FakeLLM(answer())
    extractor = LLMExtractor(llm)
    extractor.ask_number("What was GMV?", DOC)
    first = llm.embed_calls
    extractor.ask_number("What was gross merchandise volume?", DOC)
    assert llm.embed_calls == first + 1  # only the new question is embedded


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (answer(value=None), "does not state"),
        (answer(value="12.5"), "non-numeric"),
        (answer(value=True), "non-numeric"),
        ("I could not find it", "unparsable"),
        (answer(confidence="low"), "below the required"),
    ],
)
def test_extractor_rejects_bad_answers(reply, message):
    with pytest.raises(InputError, match=message):
        LLMExtractor(FakeLLM(reply)).ask_number("q", DOC)


def test_unverifiable_evidence_is_downgraded_and_rejected():
    reply = answer(evidence="the company sold lots of things")
    with pytest.raises(InputError, match="not found verbatim"):
        LLMExtractor(FakeLLM(reply)).ask_number("What was GMV?", DOC)


def test_lower_threshold_accepts_unverified_evidence_but_keeps_the_note():
    reply = answer(evidence="the company sold lots of things")
    value = LLMExtractor(FakeLLM(reply)).ask_number("What was GMV?", DOC, min_confidence="low")
    assert value.confidence == "low"
    assert "not found verbatim" in value.note


def test_empty_document_rejected():
    with pytest.raises(InputError, match="no text"):
        LLMExtractor(FakeLLM(answer())).ask_number("q", "   ")
