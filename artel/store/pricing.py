from __future__ import annotations

import json
import os
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


def rate_for(model: str) -> dict | None:
    return _rates_from_env().get(model) or _openrouter_rates().get(model)


def cost_usd(model: str, billing_mode: str, usage: dict) -> dict:
    """Dollars for a usage rollup, or an explicit refusal to guess.

    Returns `amount=None` with a reason rather than 0.0 when a figure cannot be
    honestly produced. Two cases matter and they are different:

    - subscription: the tokens were really spent but nobody is billed per token, so a
      dollar figure would be fiction. Volume is the honest unit.
    - unknown model: no rate, so no number. Saying "$0" here reads as "this was free",
      which is the most expensive kind of wrong an spend report can be.
    """
    if billing_mode == SUBSCRIPTION:
        return {"amount": None, "reason": "subscription — billed per seat, not per token"}
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
        "rate": rate,
        "derivation": (
            f"{usage.get('input_tokens', 0)}*{rate['input']} + "
            f"{usage.get('output_tokens', 0)}*{rate['output']} + "
            f"{usage.get('cache_read', 0)}*{rate.get('cache_read', rate['input'])} + "
            f"{usage.get('cache_write', 0)}*{rate.get('cache_write', rate['input'])}"
        ),
    }
