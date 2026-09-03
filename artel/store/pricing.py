from __future__ import annotations

import json
import os
import re
import time

_CACHE: dict[str, dict] = {}
_FETCHED_AT = 0.0
_TTL = 6 * 3600

SUBSCRIPTION = "subscription"
METERED = "metered"
UNKNOWN = "unknown"


def _rates_from_env() -> dict[str, dict]:
    raw = os.environ.get("MODEL_RATES", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _openrouter_rates() -> dict[str, dict]:
    global _FETCHED_AT
    if _CACHE and time.time() - _FETCHED_AT < _TTL:
        return _CACHE
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {}
    try:
        import httpx

        r = httpx.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
        r.raise_for_status()
        for m in r.json().get("data", []):
            p = m.get("pricing") or {}
            try:
                _CACHE[m["id"]] = {
                    "input": float(p.get("prompt", 0)),
                    "output": float(p.get("completion", 0)),
                    "cache_read": float(p.get("input_cache_read", p.get("prompt", 0)) or 0),
                    "cache_write": float(p.get("input_cache_write", p.get("prompt", 0)) or 0),
                }
            except (TypeError, ValueError, KeyError):
                continue
        _FETCHED_AT = time.time()
    except Exception:
        return _CACHE
    return _CACHE


def _candidates(model: str) -> list[str]:
    """Model ids as Claude Code reports them, mapped to how a rate table names them.

    A session records `claude-opus-4-8`; OpenRouter lists `anthropic/claude-opus-4.8`.
    Without this every Anthropic model is unpriced, which looked like a billing-mode
    problem and was really a string problem.
    """
    out = [model]
    base = re.sub(r"-\d{8}$", "", model)  # drop a trailing date stamp
    dotted = re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", base)
    for m in (base, dotted):
        if m not in out:
            out.append(m)
        if not m.startswith("anthropic/"):
            out.append(f"anthropic/{m}")
    return out


def rate_for(model: str) -> dict | None:
    env, live = _rates_from_env(), _openrouter_rates()
    for candidate in _candidates(model):
        hit = env.get(candidate) or live.get(candidate)
        if hit:
            return hit
    return None


def cost_usd(model: str, billing_mode: str, usage: dict) -> dict:
    """List-price value of a usage rollup.

    Always computed when a rate exists, whatever the billing mode. The tokens are the
    same either way, and on a seat the number is arguably more interesting: it is what
    the work would have cost metered, i.e. what the seat is worth. `billed` says
    whether it is an invoice or an equivalent — the caller labels it, rather than the
    figure being withheld.

    Still no number when there is no rate: "$0" reads as "this was free", which is the
    most expensive way for a spend report to be wrong.
    """
    rate = rate_for(model)
    if not rate:
        return {"amount": None, "reason": f"no published rate for {model!r}"}
    amount = (
        usage.get("input_tokens", 0) * rate["input"]
        + usage.get("output_tokens", 0) * rate["output"]
        + usage.get("cache_read", 0) * rate.get("cache_read", rate["input"])
        + usage.get("cache_write", 0) * rate.get("cache_write", rate["input"])
    )
    return {
        "amount": round(amount, 6),
        "reason": None,
        "billed": billing_mode == METERED,
        "basis": "actual spend" if billing_mode == METERED else "list-price equivalent",
        "rate": rate,
        "derivation": (
            f"{usage.get('input_tokens', 0)}*{rate['input']} + "
            f"{usage.get('output_tokens', 0)}*{rate['output']} + "
            f"{usage.get('cache_read', 0)}*{rate.get('cache_read', rate['input'])} + "
            f"{usage.get('cache_write', 0)}*{rate.get('cache_write', rate['input'])}"
        ),
    }
