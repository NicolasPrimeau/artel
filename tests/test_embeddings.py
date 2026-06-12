import pytest

import artel.store.embeddings as emb

_embed = emb.embed


class _Arr:
    def tolist(self):
        return [0.0] * 384


class _WorkingModel:
    def __init__(self, name):
        pass

    def embed(self, texts):
        yield _Arr()


@pytest.fixture(autouse=True)
def reset_embeddings_state():
    emb._model = None
    emb._retry_at = 0.0
    yield
    emb._model = None
    emb._retry_at = 0.0


def test_load_failure_returns_none_and_reports_unavailable(monkeypatch):
    attempts = []

    class Boom:
        def __init__(self, name):
            attempts.append(name)
            raise RuntimeError("corrupt model cache")

    monkeypatch.setattr(emb, "TextEmbedding", Boom)

    assert emb.get_model() is None
    assert _embed("anything") is None
    assert emb.embeddings_ok() is False
    assert len(attempts) == 1


def test_failure_does_not_retry_during_cooldown(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(emb.time, "monotonic", lambda: clock["now"])
    attempts = []

    class Boom:
        def __init__(self, name):
            attempts.append(name)
            raise RuntimeError("still broken")

    monkeypatch.setattr(emb, "TextEmbedding", Boom)

    assert _embed("x") is None
    clock["now"] = 1000.0 + emb._RETRY_COOLDOWN - 1
    assert _embed("x") is None
    assert emb.embeddings_ok() is False
    assert len(attempts) == 1


def test_recovers_after_cooldown_elapses(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(emb.time, "monotonic", lambda: clock["now"])
    attempts = []

    class Flaky:
        def __init__(self, name):
            attempts.append(name)
            if len(attempts) == 1:
                raise RuntimeError("transient")

        def embed(self, texts):
            yield _Arr()

    monkeypatch.setattr(emb, "TextEmbedding", Flaky)

    assert _embed("x") is None
    clock["now"] = 1000.0 + emb._RETRY_COOLDOWN + 1
    assert _embed("x") == [0.0] * 384
    assert emb.embeddings_ok() is True
    assert len(attempts) == 2


def test_repeated_failures_keep_extending_cooldown(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(emb.time, "monotonic", lambda: clock["now"])
    attempts = []

    class Boom:
        def __init__(self, name):
            attempts.append(name)
            raise RuntimeError("persistent")

    monkeypatch.setattr(emb, "TextEmbedding", Boom)

    for i in range(3):
        clock["now"] = i * (emb._RETRY_COOLDOWN + 1)
        assert emb.get_model() is None
    assert len(attempts) == 3


def test_loaded_model_is_cached_and_never_reloaded(monkeypatch):
    attempts = []

    class Counting(_WorkingModel):
        def __init__(self, name):
            attempts.append(name)

    monkeypatch.setattr(emb, "TextEmbedding", Counting)

    assert _embed("a") == [0.0] * 384
    assert _embed("b") == [0.0] * 384
    assert emb.embeddings_ok() is True
    assert len(attempts) == 1


def test_embed_call_failure_returns_none_without_poisoning_model(monkeypatch):
    class BrokenInference:
        def __init__(self, name):
            pass

        def embed(self, texts):
            raise RuntimeError("inference blew up")

    monkeypatch.setattr(emb, "TextEmbedding", BrokenInference)

    assert _embed("x") is None
    assert emb.embeddings_ok() is True
