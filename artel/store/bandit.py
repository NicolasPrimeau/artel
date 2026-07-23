from __future__ import annotations

import json
import math
from dataclasses import dataclass


def sigmoid(z: float) -> float:
    if z <= -60.0:
        return 0.0
    if z >= 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


@dataclass(frozen=True)
class BanditState:
    weights: list[float]


def initial_state(dim: int) -> BanditState:
    return BanditState(weights=[0.0] * dim)


def predict(state: BanditState, features: list[float]) -> float:
    z = sum(w * x for w, x in zip(state.weights, features))
    return sigmoid(z)


def update(state: BanditState, features: list[float], reward: float, lr: float) -> BanditState:
    error = reward - predict(state, features)
    return BanditState(weights=[w + lr * error * x for w, x in zip(state.weights, features)])


def dumps(state: BanditState) -> str:
    return json.dumps({"weights": state.weights})


def loads(value: str, dim: int) -> BanditState:
    try:
        data = json.loads(value)
        weights = data["weights"]
    except (TypeError, ValueError, KeyError):
        return initial_state(dim)
    if not isinstance(weights, list) or len(weights) != dim:
        return initial_state(dim)
    try:
        return BanditState(weights=[float(w) for w in weights])
    except (TypeError, ValueError):
        return initial_state(dim)
