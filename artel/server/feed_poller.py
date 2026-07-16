import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

from ..store import graph, merkle, vclock
from ..store.db import fts_index, get_db, instance_id
from ..store.embeddings import embed
from .broadcast import broadcast
from .models import EventEntry, new_id

log = logging.getLogger(__name__)

_POLL_INTERVAL = 60


def _utcnow() -> str:
    dt = datetime.now(UTC)
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{dt.microsecond // 1000:03d}Z")


def _item_guid(entry: feedparser.FeedParserDict) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def _item_content(feed_name: str, entry: feedparser.FeedParserDict) -> str:
    title = entry.get("title", "(no title)")
    summary = entry.get("summary", entry.get("description", ""))
    link = entry.get("link", "")
    published = entry.get("published", "")
    parts = [f"## [{feed_name}] {title}"]
    if published:
        parts.append(f"Published: {published}")
    if summary:
        parts.append(f"\n{summary[:1000]}")
    if link:
        parts.append(f"\nSource: {link}")
    return "\n".join(parts)


def _write_memory(agent_id: str, project: str, content: str, tags: list[str]) -> None:
    db = get_db()
    entry_id = new_id()
    event_id = new_id()
    vec = embed(content)
    with db:
        now = _utcnow()
        db.execute(
            """INSERT INTO memory (id, type, agent_id, project, scope, content,
               confidence, parents, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                "memory",
                agent_id,
                project,
                "project",
                content,
                0.5,
                "[]",
                json.dumps(tags),
                now,
                now,
            ),
        )
        if vec is not None:
            db.execute(
                "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
                (entry_id, json.dumps(vec)),
            )
        fts_index(db, entry_id, content)
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (event_id, "memory.written", agent_id, json.dumps({"memory_id": entry_id})),
        )
    broadcast(
        EventEntry(
            id=event_id,
            type="memory.written",
            agent_id=agent_id,
            payload={"memory_id": entry_id},
            created_at=_utcnow(),
        )
    )


def _parse_json_feed(resp_text: str, feed_name: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(resp_text)
    except Exception:
        return []
    if not isinstance(data.get("items"), list):
        return []
    results = []
    for item in data["items"]:
        guid = item.get("id") or item.get("url", "")
        if not guid:
            continue
        title = item.get("title", "(no title)")
        body = item.get("content_text") or item.get("content_html") or item.get("summary", "")
        published = item.get("date_published", "")
        link = item.get("url", "")
        parts = [f"## [{feed_name}] {title}"]
        if published:
            parts.append(f"Published: {published}")
        if body:
            parts.append(f"\n{body[:1000]}")
        if link:
            parts.append(f"\nSource: {link}")
        results.append((guid, "\n".join(parts)))
    return results


def _order_key(version: int, updated_at: str, content: str) -> tuple:
    return (version, updated_at, hashlib.sha256(content.encode("utf-8")).hexdigest())


def conflict_sibling_id(gid: str, content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"artel:conflict:{gid}:{digest}"))


def _emit(db, pending: list, event_type: str, agent_id: str, payload: dict) -> None:
    eid = new_id()
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (eid, event_type, agent_id, json.dumps(payload)),
    )
    pending.append(
        EventEntry(
            id=eid, type=event_type, agent_id=agent_id, payload=payload, created_at=_utcnow()
        )
    )


def _insert_conflict_sibling(db, gid: str, project: str | None, loser: dict) -> str | None:
    sib_id = conflict_sibling_id(gid, loser["content"])
    exists = db.execute("SELECT 1 FROM memory WHERE id=?", (sib_id,)).fetchone()
    if not exists:
        sib_tags = [t for t in loser["tags"] if t != "sync-conflict"] + ["sync-conflict"]
        db.execute(
            """INSERT INTO memory (id, type, agent_id, project, scope, content,
               confidence, parents, tags, created_at, updated_at, version, origin)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sib_id,
                loser["type"],
                loser["agent_id"],
                project,
                "project",
                loser["content"],
                loser["confidence"],
                json.dumps([gid]),
                json.dumps(sib_tags),
                loser["stamp"],
                loser["stamp"],
                1,
                loser["origin"],
            ),
        )
        vec = embed(loser["content"])
        if vec is not None:
            db.execute(
                "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (sib_id, json.dumps(vec))
            )
        fts_index(db, sib_id, loser["content"])
    graph.add_edge(db, project, sib_id, gid, graph.CONTRADICTS, note="sync conflict")
    return sib_id if not exists else None


def _replicate_entry(db, feed: dict, meta: dict, content: str, tags: list, self_id: str) -> bool:
    gid = meta.get("memory_id")
    origin = meta.get("origin")
    if not gid or not origin or origin == self_id:
        return False
    incoming_ver = int(meta.get("version") or 1)
    incoming_upd = meta.get("updated_at") or _utcnow()
    incoming_del = meta.get("deleted_at")
    incoming_vc = vclock.parse(meta.get("vclock"))
    etype = meta.get("type") or "memory"
    agent_id = meta.get("agent_id") or feed["agent_id"]
    conf = meta.get("confidence")
    conf = 1.0 if conf is None else float(conf)
    parents = json.dumps(meta.get("parents") or [])
    tags_json = json.dumps(tags or [])
    created_at = meta.get("created_at") or incoming_upd

    row = db.execute(
        """SELECT version, updated_at, vclock, type, agent_id, content, confidence,
           tags, project, origin, deleted_at FROM memory WHERE id=?""",
        (gid,),
    ).fetchone()

    pending: list[EventEntry] = []

    if row is None:
        if incoming_del:
            return False
        vec = embed(content)
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, scope, content,
                   confidence, parents, tags, created_at, updated_at, version, origin, vclock)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    gid,
                    etype,
                    agent_id,
                    feed["project"],
                    "project",
                    content,
                    conf,
                    parents,
                    tags_json,
                    created_at,
                    incoming_upd,
                    incoming_ver,
                    origin,
                    vclock.dump(incoming_vc),
                ),
            )
            if vec is not None:
                db.execute(
                    "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (gid, json.dumps(vec))
                )
            fts_index(db, gid, content)
            _emit(db, pending, "memory.written", agent_id, {"memory_id": gid})
        for ev in pending:
            broadcast(ev)
        return True

    local_vc = vclock.parse(row["vclock"])
    apply_incoming = False
    merge_only = False
    sibling: dict | None = None
    store_vc = vclock.dump(incoming_vc)

    if incoming_vc and local_vc:
        rel = vclock.compare(incoming_vc, local_vc)
        if rel in (vclock.EQUAL, vclock.DOMINATED):
            return False
        store_vc = vclock.dump(vclock.merge(incoming_vc, local_vc))
        if rel == vclock.DOMINATES:
            apply_incoming = True
        else:
            inc_key = _order_key(incoming_ver, incoming_upd, content)
            loc_key = _order_key(int(row["version"]), row["updated_at"] or "", row["content"])
            apply_incoming = inc_key > loc_key
            merge_only = not apply_incoming
            if apply_incoming and not row["deleted_at"]:
                sibling = {
                    "content": row["content"],
                    "type": row["type"],
                    "agent_id": row["agent_id"],
                    "confidence": row["confidence"],
                    "tags": json.loads(row["tags"]),
                    "origin": row["origin"] or self_id,
                    "stamp": row["updated_at"] or incoming_upd,
                }
            elif merge_only and not incoming_del:
                sibling = {
                    "content": content,
                    "type": etype,
                    "agent_id": agent_id,
                    "confidence": conf,
                    "tags": list(tags or []),
                    "origin": origin,
                    "stamp": incoming_upd,
                }
    else:
        local_ver = int(row["version"])
        local_upd = row["updated_at"] or ""
        newer = incoming_ver > local_ver or (incoming_ver == local_ver and incoming_upd > local_upd)
        if not newer:
            return False
        apply_incoming = True

    project = row["project"] or feed["project"]
    with db:
        if apply_incoming:
            if incoming_del:
                db.execute(
                    "UPDATE memory SET deleted_at=?, version=?, updated_at=?, vclock=? WHERE id=?",
                    (incoming_del, incoming_ver, incoming_upd, store_vc, gid),
                )
                _emit(db, pending, "memory.deleted", agent_id, {"memory_id": gid})
            else:
                db.execute(
                    """UPDATE memory SET type=?, content=?, confidence=?, parents=?, tags=?,
                       updated_at=?, version=?, deleted_at=NULL, vclock=? WHERE id=?""",
                    (
                        etype,
                        content,
                        conf,
                        parents,
                        tags_json,
                        incoming_upd,
                        incoming_ver,
                        store_vc,
                        gid,
                    ),
                )
                db.execute("DELETE FROM memory_vec WHERE id=?", (gid,))
                new_vec = embed(content)
                if new_vec is not None:
                    db.execute(
                        "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
                        (gid, json.dumps(new_vec)),
                    )
                fts_index(db, gid, content)
                _emit(db, pending, "memory.written", agent_id, {"memory_id": gid})
        elif merge_only:
            db.execute("UPDATE memory SET vclock=? WHERE id=?", (store_vc, gid))
        if sibling is not None:
            sib_id = _insert_conflict_sibling(db, gid, project, sibling)
            if sib_id:
                _emit(
                    db,
                    pending,
                    "memory.conflict",
                    agent_id,
                    {
                        "memory_id": gid,
                        "sibling_id": sib_id,
                        "winner": "incoming" if apply_incoming else "local",
                    },
                )
    for ev in pending:
        broadcast(ev)
    return apply_incoming or merge_only


def _with_params(url: str, extra: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{extra}"


async def _merkle_sync(feed: dict) -> bool:
    if "/memory/feed.json" not in feed["url"]:
        return False
    merkle_url = feed["url"].replace("/memory/feed.json", "/memory/merkle")
    db = get_db()
    count = 0
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(merkle_url)
            if resp.status_code != 200:
                return False
            peer = resp.json()
            if not isinstance(peer, dict) or "root" not in peer or "buckets" not in peer:
                return False
            local = merkle.tree(db, feed["project"])
            want: list[str] = []
            if peer["root"] != local["root"]:
                for b, bh in peer["buckets"].items():
                    if local["buckets"].get(b) == bh:
                        continue
                    bresp = await client.get(_with_params(merkle_url, f"bucket={b}"))
                    bresp.raise_for_status()
                    remote_entries = (bresp.json() or {}).get("entries") or {}
                    local_entries = merkle.bucket_entries(db, feed["project"], b)
                    want += [i for i, h in remote_entries.items() if local_entries.get(i) != h]
            if want:
                self_id = instance_id()
                want = want[: feed["max_per_poll"]]
                for start in range(0, len(want), 100):
                    chunk = want[start : start + 100]
                    fresp = await client.get(
                        _with_params(feed["url"], "include_deleted=true&ids=" + ",".join(chunk))
                    )
                    fresp.raise_for_status()
                    for it in fresp.json().get("items") or []:
                        meta = it.get("_artel") or {}
                        if _replicate_entry(
                            db, feed, meta, it.get("content_text", ""), it.get("tags", []), self_id
                        ):
                            count += 1
    except Exception as e:
        log.warning("feed %s merkle sync failed, falling back to full feed: %s", feed["name"], e)
        return False
    _finish_poll(db, feed, count)
    return True


async def _poll_feed(feed: dict) -> None:
    feed_id = feed["id"]
    if await _merkle_sync(feed):
        return
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(feed["url"])
            resp.raise_for_status()
    except Exception as e:
        log.warning("feed %s (%s) fetch failed: %s", feed["name"], feed["url"], e)
        lid = new_id()
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO archivist_logs (id, level, source, action, message, details) VALUES (?,?,?,?,?,?)",
                (
                    lid,
                    "warning",
                    "poller",
                    "feed_poll",
                    f'feed "{feed["name"]}" fetch failed: {e}',
                    json.dumps({"feed_id": feed_id, "feed_name": feed["name"], "url": feed["url"]}),
                ),
            )
            db.execute(
                "DELETE FROM archivist_logs WHERE id IN (SELECT id FROM archivist_logs ORDER BY created_at DESC LIMIT -1 OFFSET 10000)"
            )
        return

    content_type = resp.headers.get("content-type", "")
    is_json_feed = "feed+json" in content_type or (
        "json" in content_type and '"version"' in resp.text and "jsonfeed.org" in resp.text
    )

    db = get_db()
    seen = {
        r["item_guid"]
        for r in db.execute(
            "SELECT item_guid FROM feed_items_seen WHERE feed_id=?", (feed_id,)
        ).fetchall()
    }

    tags = json.loads(feed["tags"]) + ["feed-item", "unprocessed"]
    count = 0
    new_guids = []

    if is_json_feed:
        try:
            data = json.loads(resp.text)
            json_items = data["items"] if isinstance(data.get("items"), list) else []
        except Exception:
            json_items = []
        is_artel_peer = any(
            isinstance(it.get("_artel"), dict)
            and it["_artel"].get("memory_id")
            and it["_artel"].get("origin")
            for it in json_items
        )
        if is_artel_peer:
            self_id = instance_id()
            for it in json_items:
                if count >= feed["max_per_poll"]:
                    break
                meta = it.get("_artel") or {}
                if _replicate_entry(
                    db, feed, meta, it.get("content_text", ""), it.get("tags", []), self_id
                ):
                    count += 1
        else:
            for guid, content in _parse_json_feed(resp.text, feed["name"]):
                if count >= feed["max_per_poll"]:
                    break
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                _write_memory(feed["agent_id"], feed["project"], content, tags)
                new_guids.append(guid)
                count += 1
    else:
        parsed = feedparser.parse(resp.text)
        for entry in parsed.entries:
            if count >= feed["max_per_poll"]:
                break
            guid = _item_guid(entry)
            if not guid or guid in seen:
                continue
            seen.add(guid)
            content = _item_content(feed["name"], entry)
            _write_memory(feed["agent_id"], feed["project"], content, tags)
            new_guids.append(guid)
            count += 1

    if new_guids:
        now = _utcnow()
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO feed_items_seen (feed_id, item_guid, seen_at) VALUES (?,?,?)",
                [(feed_id, g, now) for g in new_guids],
            )

    _finish_poll(db, feed, count)


def _finish_poll(db, feed: dict, count: int) -> None:
    feed_id = feed["id"]
    with db:
        db.execute(
            "UPDATE feed_subscriptions SET last_fetched_at=? WHERE id=?",
            (_utcnow(), feed_id),
        )

    if count:
        log.info(
            "feed %s: ingested %d new items into project %s", feed["name"], count, feed["project"]
        )
        lid = new_id()
        with db:
            db.execute(
                "INSERT INTO archivist_logs (id, level, source, action, message, details) VALUES (?,?,?,?,?,?)",
                (
                    lid,
                    "info",
                    "poller",
                    "feed_poll",
                    f'feed "{feed["name"]}": ingested {count} new item{"s" if count != 1 else ""} into project {feed["project"]}',
                    json.dumps(
                        {
                            "feed_id": feed_id,
                            "feed_name": feed["name"],
                            "project": feed["project"],
                            "count": count,
                        }
                    ),
                ),
            )
            db.execute(
                "DELETE FROM archivist_logs WHERE id IN (SELECT id FROM archivist_logs ORDER BY created_at DESC LIMIT -1 OFFSET 10000)"
            )


async def run_poller() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        db = get_db()
        now = datetime.now(UTC)
        rows = db.execute("SELECT * FROM feed_subscriptions").fetchall()
        for row in rows:
            feed = dict(row)
            if feed["last_fetched_at"] is None:
                due = True
            else:
                try:
                    last = datetime.fromisoformat(feed["last_fetched_at"].replace("Z", "+00:00"))
                    due = now - last >= timedelta(minutes=feed["interval_min"])
                except ValueError:
                    due = True
            if due:
                try:
                    await _poll_feed(feed)
                except Exception as e:
                    log.error("poll_feed %s failed: %s", feed["name"], e)
