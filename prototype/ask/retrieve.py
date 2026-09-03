"""Retrieval over the fleet's own record, via the running Artel server.

Artel already does semantic search with embeddings, so this asks it rather than
building a second index. Every passage carries its entry id back, because an answer
about what a team decided is worthless if it cannot be checked.
"""

from __future__ import annotations

import os

import httpx

ARTEL_URL = os.environ.get("ASK_ARTEL_URL", "http://localhost:8000")
AGENT = os.environ.get("ASK_AGENT_ID", "archivist")
KEY = os.environ.get("ASK_API_KEY", "")


def _headers() -> dict[str, str]:
    return {"x-agent-id": AGENT, "x-api-key": KEY}


async def search(question: str, limit: int = 8, project: str | None = None) -> list[dict]:
    params: dict[str, str | int] = {"q": question, "limit": limit}
    if project:
        params["project"] = project
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{ARTEL_URL}/memory/search", params=params, headers=_headers())
        r.raise_for_status()
        data = r.json()
    items = data if isinstance(data, list) else data.get("results", data.get("items", []))
    return [
        {
            "id": e.get("id", ""),
            "project": e.get("project"),
            "type": e.get("type"),
            "agent": e.get("agent_id"),
            "confidence": e.get("confidence"),
            "updated_at": e.get("updated_at"),
            "content": (e.get("content") or "")[:1200],
        }
        for e in items
    ]


async def decisions(limit: int = 20) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{ARTEL_URL}/decisions", params={"limit": limit}, headers=_headers())
        if r.status_code != 200:
            return []
        data = r.json()
    items = data if isinstance(data, list) else data.get("items", [])
    return items
