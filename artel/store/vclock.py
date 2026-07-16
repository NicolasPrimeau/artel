import json

DOMINATES = "dominates"
DOMINATED = "dominated"
EQUAL = "equal"
CONCURRENT = "concurrent"


def parse(raw: str | dict | None) -> dict[str, int]:
    if isinstance(raw, dict):
        data = raw
    elif not raw:
        return {}
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
    out: dict[str, int] = {}
    for k, v in data.items():
        if isinstance(v, bool) or not isinstance(v, int | float):
            continue
        out[str(k)] = int(v)
    return out


def dump(vc: dict[str, int]) -> str | None:
    return json.dumps(vc, sort_keys=True) if vc else None


def bump(vc: dict[str, int], instance: str) -> dict[str, int]:
    out = dict(vc)
    out[instance] = out.get(instance, 0) + 1
    return out


def merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in a.keys() | b.keys()}


def compare(a: dict[str, int], b: dict[str, int]) -> str:
    a_ahead = any(v > b.get(k, 0) for k, v in a.items())
    b_ahead = any(v > a.get(k, 0) for k, v in b.items())
    if a_ahead and b_ahead:
        return CONCURRENT
    if a_ahead:
        return DOMINATES
    if b_ahead:
        return DOMINATED
    return EQUAL
