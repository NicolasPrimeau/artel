import pytest

from artel.store import bandit


def test_sigmoid_bounds():
    assert bandit.sigmoid(0.0) == pytest.approx(0.5)
    assert bandit.sigmoid(-100.0) == 0.0
    assert bandit.sigmoid(100.0) == 1.0


def test_initial_prediction_is_one_half():
    state = bandit.initial_state(3)
    assert bandit.predict(state, [1.0, 0.5, 0.2]) == pytest.approx(0.5)


def test_update_moves_prediction_toward_reward():
    state = bandit.initial_state(2)
    features = [1.0, 1.0]
    for _ in range(200):
        state = bandit.update(state, features, 1.0, lr=0.1)
    assert bandit.predict(state, features) > 0.9


def test_learns_a_predictive_feature():
    # reward is 1 when the second feature is 1, else 0
    state = bandit.initial_state(2)
    for _ in range(300):
        state = bandit.update(state, [1.0, 1.0], 1.0, lr=0.2)
        state = bandit.update(state, [1.0, 0.0], 0.0, lr=0.2)
    assert bandit.predict(state, [1.0, 1.0]) > bandit.predict(state, [1.0, 0.0])
    assert bandit.predict(state, [1.0, 1.0]) > 0.7
    assert bandit.predict(state, [1.0, 0.0]) < 0.5


def test_negative_feature_gets_negative_weight():
    state = bandit.initial_state(2)
    for _ in range(300):
        state = bandit.update(state, [1.0, 1.0], 0.0, lr=0.2)
        state = bandit.update(state, [1.0, 0.0], 1.0, lr=0.2)
    assert state.weights[1] < 0.0


def test_serialize_round_trip():
    state = bandit.BanditState(weights=[0.3, -1.2, 0.5])
    restored = bandit.loads(bandit.dumps(state), 3)
    assert restored.weights == pytest.approx(state.weights)


def test_loads_rejects_wrong_dim_and_garbage():
    assert bandit.loads('{"weights": [1.0, 2.0]}', 3).weights == [0.0, 0.0, 0.0]
    assert bandit.loads("not json", 2).weights == [0.0, 0.0]
