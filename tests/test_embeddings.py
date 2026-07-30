"""Unit tests for the embedding wrapper. The heavy sentence-transformers
dependency is faked via sys.modules — these tests run without the extra."""
import sys
import types

import pytest

from codeintel import embeddings
from codeintel.embeddings import EmbeddingModel, SemanticExtraMissingError


class _FakeST:
    def __init__(self, model_name, revision=None, trust_remote_code=False):
        self.model_name, self.revision = model_name, revision
        self.calls: list[list[str]] = []
        self.normalize_flags: list[bool] = []

    def encode(self, batch, normalize_embeddings=False):
        self.calls.append(list(batch))
        self.normalize_flags.append(normalize_embeddings)
        return [[float(len(t)), 1.0] for t in batch]


def _install_fake(monkeypatch):
    holder = {}

    def ctor(*args, **kwargs):
        holder["model"] = _FakeST(*args, **kwargs)
        return holder["model"]

    fake_module = types.SimpleNamespace(SentenceTransformer=ctor)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    return holder


def test_missing_extra_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    model = EmbeddingModel()
    with pytest.raises(SemanticExtraMissingError, match="uv sync --extra semantic"):
        model.embed_texts(["x"])


def test_lazy_load_and_batching(monkeypatch):
    holder = _install_fake(monkeypatch)
    model = EmbeddingModel(model_name="m", revision="r", batch_size=2)
    assert "model" not in holder  # nothing loaded at construction
    vectors = model.embed_texts(["a", "bb", "ccc"])
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    assert holder["model"].calls == [["a", "bb"], ["ccc"]]  # batch_size respected
    assert holder["model"].normalize_flags == [True, True]  # L2-normalized vectors


def test_embed_query_encodes_raw_text(monkeypatch):
    # bge-m3 dropped the query-instruction-prefix requirement present in
    # earlier BGE versions, so embed_query is a direct passthrough.
    holder = _install_fake(monkeypatch)
    model = EmbeddingModel(model_name="m", revision="r")
    model.embed_query("auth")
    assert holder["model"].calls[0][0] == "auth"


def test_load_caps_max_seq_length(monkeypatch):
    # bge-m3 defaults to 8192, far beyond our chunk target; an oversized or
    # under-estimated chunk must not be free to blow encode-time memory.
    holder = _install_fake(monkeypatch)
    model = EmbeddingModel(model_name="m", revision="r")
    model.embed_texts(["x"])
    assert holder["model"].max_seq_length == embeddings.MAX_SEQ_LENGTH


def test_default_batch_size_is_conservative():
    assert EmbeddingModel().batch_size == embeddings.DEFAULT_BATCH_SIZE == 8


def test_identity_and_env_overrides(monkeypatch):
    monkeypatch.setenv("CODEINTEL_EMBEDDING_MODEL", "custom/model")
    monkeypatch.setenv("CODEINTEL_EMBEDDING_BATCH_SIZE", "7")
    model = EmbeddingModel()
    assert model.identity() == ("custom/model", "unpinned")
    assert model.batch_size == 7
    default = EmbeddingModel(model_name=embeddings.DEFAULT_MODEL)
    assert default.identity() == (embeddings.DEFAULT_MODEL, embeddings.DEFAULT_REVISION)


def test_count_oversized_returns_zero_for_empty_without_loading_model():
    """The empty guard must short-circuit before _load(), so unit tests
    never trigger a model download."""
    from codeintel.embeddings import EmbeddingModel
    model = EmbeddingModel()

    def _fail():
        raise AssertionError("model must not load for empty input")

    model._load = _fail
    assert model.count_oversized([]) == 0


def test_count_oversized_counts_texts_past_max_seq_length(monkeypatch):
    from codeintel import embeddings
    from codeintel.embeddings import EmbeddingModel

    class _FakeTokenizer:
        def __call__(self, texts):
            # one token per character keeps the lengths trivially controllable
            return {"input_ids": [list(range(len(t))) for t in texts]}

    class _FakeModel:
        tokenizer = _FakeTokenizer()

    model = EmbeddingModel(batch_size=2)
    monkeypatch.setattr(model, "_load", lambda: _FakeModel())
    short = "a" * 10
    long_text = "a" * (embeddings.MAX_SEQ_LENGTH + 1)
    assert model.count_oversized([short, long_text, long_text]) == 2
