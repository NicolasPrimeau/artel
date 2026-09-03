"""OpenRouter call for the prose half of an answer.

Dry-run is the default. Every answer this thing produces about money is computed by
facts.py and passed in already rendered; the model's only job is to write around
numbers it did not invent and to cite entry ids. Running with ASK_DRY_RUN=1 returns
the exact prompt instead of sending it, which is how this was developed without
spending anything.
"""

from __future__ import annotations

import os

import httpx

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("ASK_MODEL", "google/gemini-3.7-flash")


def dry_run() -> bool:
    return os.environ.get("ASK_DRY_RUN", "1") not in ("0", "false", "no")


SYSTEM = (
    "You answer questions about an engineering fleet from its own records.\n"
    "RULES:\n"
    "1. Numbers are given to you already computed. Never calculate, estimate, adjust or "
    "invent a figure. If a number you need is not in the FACTS block, say it is not "
    "measured rather than guessing.\n"
    "2. Cite the entry id in [brackets] for every claim drawn from a RECORD passage.\n"
    "3. If the records do not answer the question, say so plainly and say what would "
    "need to be recorded for it to be answerable next time.\n"
    "4. Be concise. Lead with the answer."
)


def build_prompt(question: str, facts_block: str, passages: list[dict]) -> dict:
    records = (
        "\n\n".join(
            f"[{p['id'][:8]}] project={p.get('project')} type={p.get('type')} "
            f"agent={p.get('agent')} updated={str(p.get('updated_at'))[:10]}\n{p['content']}"
            for p in passages
        )
        or "(no matching records)"
    )
    user = (
        f"QUESTION\n{question}\n\n"
        f"FACTS (computed, authoritative — use verbatim)\n{facts_block}\n\n"
        f"RECORDS (retrieved from fleet memory; cite by id)\n{records}"
    )
    return {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    }


async def answer(question: str, facts_block: str, passages: list[dict]) -> dict:
    payload = build_prompt(question, facts_block, passages)
    if dry_run():
        return {
            "dry_run": True,
            "model": MODEL,
            "would_send": payload,
            "text": "[dry run] No LLM call was made. Set ASK_DRY_RUN=0 to answer for real.",
        }
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"dry_run": False, "text": "OPENROUTER_API_KEY is not set.", "model": MODEL}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(BASE_URL, headers={"Authorization": f"Bearer {key}"}, json=payload)
        r.raise_for_status()
        data = r.json()
    usage = data.get("usage", {})
    return {
        "dry_run": False,
        "model": MODEL,
        "text": data["choices"][0]["message"]["content"],
        "usage": usage,
    }
