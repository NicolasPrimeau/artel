import argparse
import asyncio
import json
import pathlib
import re
import sqlite3
import sys
import uuid

import anthropic

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from artel.ledger.facts import _TOIL, _is_toil  # noqa: E402

OUT = pathlib.Path(__file__).with_name("sandbox_seed.json")
MODEL = "claude-opus-5"
BATCH = 12
NOTES_PER_PROJECT = 40
UNSCOPED_NOTES = 60

PROJECTS = {
    "nimbus": "atlas",
    "formulai": "quill",
    "artel": "artel",
    "lighthouse": "beacon",
    "longshot": "kite",
    "yeet": "sprout",
}
AGENTS = {
    "poseidon": "forge",
    "poseidon-formulai": "forge-quill",
    "poseidon-artel": "forge-artel",
    "archivist": "archivist",
}
NAMES = {
    "Nimbus": "Atlas",
    "Formulai": "Quill",
    "Lighthouse": "Beacon",
    "Longshot": "Kite",
    "Yeet": "Sprout",
    "Phalanx": "Shield",
    "BuildData": "Atlas",
    "poseidon": "forge",
}
SCRUB = (
    (re.compile(r"builddata", re.I), "atlasdata"),
    (re.compile(r"nimbus", re.I), "atlas"),
    (re.compile(r"formulai(?!re)", re.I), "quill"),
    (re.compile(r"poseidon", re.I), "forge"),
    (re.compile(r"canadabuys", re.I), "northlandbuys"),
    (re.compile(r"\bCanad(?:a|ian)\b"), "Northland"),
    (re.compile(r"\bQu[eé]bec\b"), "Eastmark"),
    (re.compile(r"\bOntario\b"), "Lakeland"),
    (re.compile(r"\bBritish Columbia\b"), "Westreach"),
)
LEAKS = re.compile(
    r"nimbus|formulai(?!re)|poseidon|nicolas|primeau|builddata|longshot|lighthouse|phalanx|yeet"
    r"|canad|qu[eé]bec|ontario|british columbia|192\.168\.|@gmail|nprimeau|/home/",
    re.I,
)
RATES = {
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.5, 6.25),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1, 1.25),
    "google/gemini-3.7-flash": (0.75, 3.75, 0.075, 0.0417),
}

SYSTEM = f"""You anonymize notes written by AI coding agents so they can be shown publicly.

Rewrite each item so no real person, company, customer, vendor, government body, product,
domain, URL, email, IP address, hostname, account id, repository or file path can be
identified. Use these codenames consistently: {json.dumps(NAMES)}. Artel is a public
open-source project and keeps its name. Invent plausible generic stand-ins for everything
else (a vendor becomes "the payments provider", a path becomes a similar generic path).

Keep the meaning, the technical substance, the numbers, the tone and roughly the length.
Keep any wording about doing work by hand, manually, every time or repeatedly exactly as
strong as it is. Never add commentary. Return every id you were given."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def scrub(text: str) -> str:
    for pat, repl in SCRUB:
        text = pat.sub(repl, text)
    return text


def _sid(value: str | None) -> str | None:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"artel-sandbox:{value}")) if value else None


def _project(value: str | None) -> str | None:
    return PROJECTS.get(value, "misc") if value else None


def _agent(value: str) -> str:
    return AGENTS.get(value, "forge")


async def _rewrite(client, sem, batch: list[dict]) -> dict[str, str]:
    async with sem:
        async with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
        ) as stream:
            msg = await stream.get_final_message()
    if msg.stop_reason != "end_turn":
        raise RuntimeError(f"rewrite stopped on {msg.stop_reason} ({msg._request_id})")
    text = next(b.text for b in msg.content if b.type == "text")
    got = {i["id"]: i["text"] for i in json.loads(text)["items"]}
    missing = {b["id"] for b in batch} - got.keys()
    if missing:
        raise RuntimeError(f"rewrite dropped {len(missing)} items ({msg._request_id})")
    return got


async def _rewrite_all(texts: dict[str, str]) -> dict[str, str]:
    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(8)
    items = [{"id": k, "text": v} for k, v in texts.items()]
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    out: dict[str, str] = {}
    for part in await asyncio.gather(*(_rewrite(client, sem, b) for b in batches)):
        out.update(part)
    return out


def _toil_ids(db: sqlite3.Connection) -> set[str]:
    ids = set()
    for r in db.execute("SELECT id, content FROM memory WHERE deleted_at IS NULL"):
        for m in _TOIL.finditer(r["content"] or ""):
            snippet = " ".join(m.group(0).split())
            if len(snippet) >= 40 and _is_toil(snippet):
                ids.add(r["id"])
    return ids


def _notes(db: sqlite3.Connection) -> list[sqlite3.Row]:
    live = "deleted_at IS NULL AND scope = 'project'"
    toil = _toil_ids(db)
    rows = {
        r["id"]: r
        for r in db.execute(
            f"SELECT * FROM memory WHERE {live} AND id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(toil)),),
        )
        if r["project"] in PROJECTS or r["project"] is None
    }
    for project in PROJECTS:
        for r in db.execute(
            f"SELECT * FROM memory WHERE {live} AND project=? ORDER BY created_at DESC LIMIT ?",
            (project, NOTES_PER_PROJECT),
        ):
            rows[r["id"]] = r
    for r in db.execute(
        f"SELECT * FROM memory WHERE {live} AND project IS NULL ORDER BY created_at DESC LIMIT ?",
        (UNSCOPED_NOTES,),
    ):
        rows[r["id"]] = r
    return list(rows.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    usage = [
        {
            "agent_id": _agent(r["agent_id"]),
            "project": _project(r["project"]),
            "session_id": _sid(r["session_id"]),
            **{
                k: r[k]
                for k in (
                    "model",
                    "billing_mode",
                    "turns",
                    "input_tokens",
                    "output_tokens",
                    "cache_read",
                    "cache_write",
                    "window_start",
                    "window_end",
                    "created_at",
                )
            },
        }
        for r in db.execute("SELECT * FROM usage_events ORDER BY created_at")
    ]
    decisions = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM decisions WHERE project IS NULL OR project IN (SELECT value FROM json_each(?)) ORDER BY created_at",
            (json.dumps(list(PROJECTS)),),
        )
    ]
    notes = [dict(r) for r in _notes(db)]

    texts: dict[str, str] = {}
    for i, d in enumerate(decisions):
        texts[f"d{i}.decision"] = d["decision"] or ""
        texts[f"d{i}.rationale"] = d["rationale"] or ""
        texts[f"d{i}.alternatives"] = d["alternatives"] or "[]"
    for i, n in enumerate(notes):
        texts[f"m{i}.content"] = n["content"] or ""
        texts[f"m{i}.headline"] = n["headline"] or ""
    texts = {k: v for k, v in texts.items() if v.strip() and v != "[]"}
    print(f"rewriting {len(texts)} texts from {len(decisions)} decisions and {len(notes)} notes")
    clean = asyncio.run(_rewrite_all(texts))

    out_decisions = []
    for i, d in enumerate(decisions):
        alts = clean.get(f"d{i}.alternatives", "[]")
        try:
            alts = json.loads(alts)
        except ValueError:
            alts = [alts]
        out_decisions.append(
            {
                "project": _project(d["project"]),
                "agent_id": _agent(d["agent_id"]),
                "decision": clean.get(f"d{i}.decision", ""),
                "rationale": clean.get(f"d{i}.rationale", ""),
                "alternatives": alts,
                "session_id": _sid(d["session_id"]),
                "created_at": d["created_at"],
            }
        )
    out_notes = [
        {
            "type": n["type"],
            "agent_id": _agent(n["agent_id"]),
            "project": _project(n["project"]),
            "content": clean.get(f"m{i}.content", ""),
            "headline": clean.get(f"m{i}.headline") or None,
            "confidence": n["confidence"],
            "tags": [NAMES.get(t, PROJECTS.get(t, t)) for t in json.loads(n["tags"] or "[]")],
            "read_count": n["read_count"] or 0,
            "created_at": n["created_at"],
            "updated_at": n["updated_at"],
        }
        for i, n in enumerate(notes)
    ]
    seed = {
        "rates": {
            m: {
                "input": i / 1e6,
                "output": o / 1e6,
                "cache_read": cr / 1e6,
                "cache_write": cw / 1e6,
            }
            for m, (i, o, cr, cw) in RATES.items()
        },
        "agents": sorted(set(AGENTS.values())),
        "usage_events": usage,
        "decisions": out_decisions,
        "memory": out_notes,
    }
    body = scrub(json.dumps(seed, ensure_ascii=False, indent=1))
    leaks = sorted({m.group(0).lower() for m in LEAKS.finditer(body)})
    if leaks:
        print(f"refusing to write: identifying strings survived: {leaks}", file=sys.stderr)
        pathlib.Path(args.out).with_suffix(".rejected.json").write_text(body)
        return 1
    pathlib.Path(args.out).write_text(body)
    print(
        f"wrote {args.out}: {len(usage)} usage, {len(out_decisions)} decisions, {len(out_notes)} notes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
