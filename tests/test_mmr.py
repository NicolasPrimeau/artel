import pytest

from artel.store import mmr


def test_cosine_identical():
    assert mmr.cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert mmr.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_is_zero():
    assert mmr.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_mismatched_length_is_zero():
    assert mmr.cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_lambda_one_preserves_relevance_order():
    ids = ["a", "b", "c"]
    rel = {"a": 0.9, "b": 0.5, "c": 0.1}
    vecs = {i: [1.0, 0.0] for i in ids}
    assert mmr.mmr_select(ids, rel, vecs, 1.0, 3) == ["a", "b", "c"]


def test_duplicate_demoted_for_diverse_alternative():
    ids = ["a", "b", "c"]
    rel = {"a": 1.0, "b": 1.0, "c": 1.0}
    vecs = {"a": [1.0, 0.0], "b": [1.0, 0.0], "c": [0.0, 1.0]}
    assert mmr.mmr_select(ids, rel, vecs, 0.7, 2) == ["a", "c"]


def test_missing_vectors_treated_as_diverse():
    ids = ["a", "b"]
    rel = {"a": 1.0, "b": 0.5}
    vecs = {"a": [1.0, 0.0]}
    assert set(mmr.mmr_select(ids, rel, vecs, 0.7, 2)) == {"a", "b"}


def test_k_larger_than_pool_returns_all():
    assert set(mmr.mmr_select(["a", "b"], {"a": 1.0, "b": 1.0}, {}, 0.7, 10)) == {"a", "b"}


def test_empty_pool_returns_empty():
    assert mmr.mmr_select([], {}, {}, 0.7, 5) == []


def test_high_relevance_survives_when_gap_exceeds_diversity_weight():
    # a strongly-relevant duplicate is kept over a barely-relevant diverse item
    ids = ["a", "b", "c"]
    rel = {"a": 1.0, "b": 0.95, "c": 0.0}
    vecs = {"a": [1.0, 0.0], "b": [1.0, 0.0], "c": [0.0, 1.0]}
    assert mmr.mmr_select(ids, rel, vecs, 0.7, 2) == ["a", "b"]
