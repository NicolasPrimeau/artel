from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PIParams:
    kp: float
    ki: float
    setpoint: float
    out_min: float
    out_max: float
    bias: float = 0.0
    deadband: float = 0.0
    leak: float = 0.0


@dataclass(frozen=True)
class PIState:
    integral: float = 0.0
    output: float = 0.0
    last_error: float = 0.0


def pi_step(params: PIParams, state: PIState, measurement: float) -> PIState:
    error = measurement - params.setpoint
    leaked = state.integral * (1.0 - params.leak)
    effective = 0.0 if abs(error) <= params.deadband else error
    trial_integral = leaked + effective
    unclamped = params.bias + params.kp * effective + params.ki * trial_integral

    if unclamped > params.out_max:
        output = params.out_max
        integral = state.integral if effective > 0 else trial_integral
    elif unclamped < params.out_min:
        output = params.out_min
        integral = state.integral if effective < 0 else trial_integral
    else:
        output = unclamped
        integral = trial_integral

    return PIState(integral=integral, output=output, last_error=error)


def initial_state(params: PIParams) -> PIState:
    return PIState(integral=0.0, output=params.bias, last_error=0.0)


def dumps(state: PIState) -> str:
    return json.dumps(
        {"integral": state.integral, "output": state.output, "last_error": state.last_error}
    )


def loads(value: str, params: PIParams) -> PIState:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return initial_state(params)
    if not isinstance(data, dict):
        return initial_state(params)
    output = data.get("output", params.bias)
    if not isinstance(output, (int, float)) or not params.out_min <= output <= params.out_max:
        output = params.bias
    return PIState(
        integral=float(data.get("integral", 0.0)),
        output=float(output),
        last_error=float(data.get("last_error", 0.0)),
    )
