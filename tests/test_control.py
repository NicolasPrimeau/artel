from artel.archivist import control

PARAMS = control.PIParams(
    kp=0.01,
    ki=0.02,
    setpoint=0.0,
    out_min=0.6,
    out_max=0.99,
    bias=0.9,
    deadband=0.5,
    leak=0.1,
)


def test_at_setpoint_holds_at_bias():
    state = control.initial_state(PARAMS)
    result = control.pi_step(PARAMS, state, 0.0)
    assert result.output == PARAMS.bias
    assert result.integral == 0.0


def test_positive_error_backs_off_above_bias():
    state = control.initial_state(PARAMS)
    result = control.pi_step(PARAMS, state, 3.0)
    assert result.output > PARAMS.bias


def test_output_always_within_saturation_bounds():
    state = control.initial_state(PARAMS)
    for measurement in (-1000.0, -1.0, 0.0, 1.0, 5.0, 1000.0):
        for _ in range(50):
            state = control.pi_step(PARAMS, state, measurement)
            assert PARAMS.out_min <= state.output <= PARAMS.out_max


def test_anti_windup_recovers_promptly_after_sustained_saturation():
    state = control.initial_state(PARAMS)
    for _ in range(200):
        state = control.pi_step(PARAMS, state, 100.0)
    assert state.output == PARAMS.out_max
    # integral must not have wound up unbounded — leak + clamping cap it
    assert abs(state.integral) <= 1.0
    # once the disturbance clears, the actuator returns to bias within a few cycles
    for _ in range(5):
        state = control.pi_step(PARAMS, state, 0.0)
    assert abs(state.output - PARAMS.bias) < 1e-9


def test_deadband_suppresses_chatter():
    state = control.pi_step(PARAMS, control.initial_state(PARAMS), 0.0)
    for measurement in (0.4, -0.4, 0.3, 0.5):
        stepped = control.pi_step(PARAMS, state, measurement)
        assert abs(stepped.output - PARAMS.bias) < 1e-9


def test_leak_returns_to_bias_monotonically_after_perturbation():
    state = control.initial_state(PARAMS)
    for _ in range(10):
        state = control.pi_step(PARAMS, state, 4.0)
    assert state.output > PARAMS.bias
    prev = state.output
    for _ in range(200):
        state = control.pi_step(PARAMS, state, 0.0)
        assert state.output <= prev + 1e-12
        prev = state.output
    assert abs(state.output - PARAMS.bias) < 1e-3


def test_converges_to_steady_state_under_constant_load():
    state = control.initial_state(PARAMS)
    outputs = []
    for _ in range(300):
        state = control.pi_step(PARAMS, state, 1.0)
        outputs.append(state.output)
    tail = outputs[-10:]
    assert max(tail) - min(tail) < 1e-6


def test_serialize_round_trip():
    state = control.PIState(integral=3.5, output=0.87, last_error=1.0)
    restored = control.loads(control.dumps(state), PARAMS)
    assert restored.integral == state.integral
    assert restored.output == state.output


def test_loads_garbage_falls_back_to_initial():
    assert control.loads("not json", PARAMS).output == PARAMS.bias
    assert control.loads("[]", PARAMS).output == PARAMS.bias
    # out-of-range output is rejected in favor of the bias
    assert control.loads('{"output": 5.0}', PARAMS).output == PARAMS.bias
