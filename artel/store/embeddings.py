import time

from fastembed import TextEmbedding

_model: TextEmbedding | None = None
_retry_at: float = 0.0
_RETRY_COOLDOWN = 300.0  # seconds between load attempts after a failure


def get_model() -> TextEmbedding | None:
    # A load failure must NOT latch forever: a corrupted model cache once killed semantic
    # search instance-wide until a manual restart. Retry on a cooldown instead.
    global _model, _retry_at
    if _model is not None:
        return _model
    now = time.monotonic()
    if now < _retry_at:
        return None
    try:
        _model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        _retry_at = now + _RETRY_COOLDOWN
        return None
    return _model


def embed(text: str) -> list[float] | None:
    model = get_model()
    if model is None:
        return None
    try:
        return next(model.embed([text])).tolist()
    except Exception:
        return None


def embeddings_ok() -> bool:
    """Health probe: is the semantic half of retrieval available right now?"""
    return get_model() is not None
