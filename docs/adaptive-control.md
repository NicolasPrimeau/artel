# Artel — Adaptive Control Architecture

## Thesis

Artel is instrumented like a control system but has run **open-loop**. The archivist
records rich signals — `utilization_rate`, `decay_regret_count`, `synthesis_uptake_rate`,
`contradiction_count`, corroboration edges — into `archivist_metrics`, but nothing read
them back to change behavior. Every decision (decay rate, synthesis cadence, task routing,
what to inject into a prompt) was static config.

The genuinely adaptive parts that already existed — Hebbian co-retrieval edges, stigmergic
trails, per-agent tag affinity — are all *local, usage-driven* kernels. The archivist's
*global* policy was not adaptive at all.

This document describes the move to a **closed-loop, adaptive system**: control theory
provides the stability layer; a policy layer (bandits / RL / emergent algorithms) sits on
top. They must be built together — a learning policy without damping and saturation will
thrash the corpus.

## The plant

| Sensors (measured each cycle) | Actuators (were static) |
|---|---|
| `utilization_rate`, `decay_regret_count`, `synthesis_uptake_rate`, `contradiction_count`, `net_growth`, corpus size | `decay_rate`, `synthesis_interval`, recall injection budget, promotion thresholds |

## Control layer

### PI control with stability guardrails

`artel/archivist/control.py` — a pure, side-effect-free SISO PI controller:

- **Proportional + Integral** toward a setpoint.
- **Clamping anti-windup** — when the actuator saturates in the direction of the error,
  the integral is frozen (no windup, prompt recovery). Verified: bounded output and
  return-to-bias within a few cycles after a sustained disturbance clears.
- **Deadband / hysteresis** — errors within a tolerance produce no action, killing chatter.
- **Leaky integrator** — the integral bleeds toward zero, so the actuator returns to its
  bias operating point once the disturbance clears (bounded memory; no permanent drift).

All properties are proven by `tests/test_control.py` (steady-state at bias, back-off on
error, saturation bounds, anti-windup recovery, deadband, monotone return-to-bias,
fixed-point convergence under constant load).

### Loop #1 — regret-servo on decay rate (implemented)

The first closed loop, live in the archivist scheduler:

- **Sensor:** `decay_regret_count` (entries decayed then needed again), already computed in
  `capture_metrics`.
- **Controller:** PI, `setpoint = 0` regret, `bias = settings.decay_rate` (0.9),
  clamped to `[control_decay_min, control_decay_max]`.
- **Actuator:** `decay_rate`, persisted in the `kv` store, read by `decay_confidence`.
- **Behavior:** while regret is zero, hold at bias. When regret appears, back off decay
  (raise `decay_rate` toward `max`, i.e. decay more gently). As regret clears, the leak
  returns it to bias. Safe by construction — it only ever *loosens* decay in response to
  regret; raising `control_decay_regret_setpoint` above 0 later enables the system to seek
  the most aggressive decay that keeps regret near a tolerance.
- **Ordering:** `capture_metrics` runs first each cycle (steps the controller), then
  `decay_confidence` applies the new rate — a one-cycle closed loop.
- **Reversible:** `control_decay_enabled` (default on) falls back to the static rate.

Wiring: `run_decay_control` / `controlled_decay_rate` in `synthesis.py`; config knobs
`control_decay_*` in `archivist/config.py`; integration tests in `tests/test_decay_control.py`.

### Observability fix

`synthesis_uptake_rate` was hardcoded to `0.0` — the key reward signal was never observed.
It is now real: the fraction of archivist-authored entries created in the window that were
subsequently read. This is the sensor the policy layer's reward bus will consume.

## Roadmap — remaining control loops

- **Cascade control** — nest a fast inner loop (per-entry retention) inside a slow outer
  loop (global corpus-size setpoint) so coupled controllers (decay / promotion / synthesis)
  don't fight.
- **Quorum-sensing trigger** — replace the fixed `synthesis_interval` with a load-adaptive
  trigger: consolidate a topic region when local write/capture density crosses a threshold.
- **Kalman usefulness estimator** — estimate true per-entry usefulness from noisy
  read/uptake signals before feeding it to controllers.
- **MPC for the LLM budget** — model-predictive allocation of the archivist's per-cycle
  token budget across synthesis / merge / headline actions.
- **Lyapunov analysis** — a corpus "energy" function to certify the closed loop is a
  contraction (no unbounded growth or collapse) before running policies unsupervised.

## Policy layer (on top of control)

The control layer keeps things stable; these decide *what* to do. Each turns an existing
Artel signal into a reward.

### Reinforcement learning

- **A1 — contextual bandit for recall-injection gating.** LinUCB/Thompson over
  (prompt cluster, agent, project, hook). Reward = uptake. Stops injecting ignored memory,
  personalizes, and makes `synthesis_uptake_rate` the training signal. Also learns the RRF
  re-score weights (`memory.py` `_score`) instead of hardcoding them.
- **A2 — regret-minimizing retention policy.** The generalization of Loop #1: an online
  policy predicting `P(needed again | features)`, trained on regret events.
- **A3 — contextual bandit for task routing.** Arms = agents, reward = task outcome
  (success / latency / re-assignment). Generalizes the affinity table from claims to
  outcomes; the existing LLM `suggest_task_assignment` becomes the cold-start prior.

### Emergent behavior

- **B1 — EigenTrust reputation** over the `corroborates` / `contradicts` graph. Writer
  reliability as the principal eigenvector; feeds confidence priors and the CRDT semantic
  tiebreak (trust-weighted instead of pure LWW). Answers the open "writer-scope staleness"
  need.
- **B2 — quorum sensing** (also a control trigger, above).
- **B3 — STDP** — make Hebbian edges directional/timing-aware (A-before-B strengthens
  A→B), learning a predictive transition model over memory.

### Plugin integration

- **C1 — diverse recall via DPP / MMR.** Select relevant *and* mutually diverse memories
  instead of top-k near-duplicates. Pure quality win, no learning required. Same math makes
  capture compression a submodular coverage problem under a token budget.
- **C2 — predictive JIT retrieval hook.** From the trajectory of files/tools just touched,
  walk STDP edges (B3) + spreading activation to pre-surface the next-needed memory; gated
  by the A1 bandit.
- **C3 — collaborative filtering** over the (agent × memory) uptake matrix — "agents like
  you found this useful."

## The keystone: a reward bus

A1, A2, A3, B1, and C3 all need the same thing: an **uptake / outcome signal** already
latent in captures, memory-access logs, and task results. Building that single signal is
what flips Artel from open-loop to closed-loop across the board. Loop #1 and the
`synthesis_uptake_rate` fix are the first tap into it.

## Guardrails

- Keep an ε-exploration floor; never suppress directives / high-confidence docs.
- Treat reward as noisy and delayed — batch updates, never tune on a single session.
- Every policy behind a flag, reversible (as `control_decay_enabled` is).
- Prove or bound stability (anti-windup, saturation, Lyapunov) before autonomy.
