import asyncio
import logging
import pathlib

from .client import ArtelClient
from .config import settings
from .conflict import check_and_merge
from .llm import is_configured
from .synthesis import (
    capture_metrics,
    decay_confidence,
    on_task_completed,
    on_task_failed,
    run_brief,
    run_deep_synthesis_if_due,
    run_feed_triage,
    run_promotion,
    run_synthesis,
    run_task_triage,
    run_utilization_prune_if_due,
    suggest_task_assignment,
)

log = logging.getLogger(__name__)

_HEARTBEAT = pathlib.Path("/tmp/archivist.heartbeat")


async def _dispatch(event: dict, client: ArtelClient) -> None:
    event_type = event.get("type", "")
    payload = event.get("payload", {})
    agent_id = event.get("agent_id", "")

    if event_type == "memory.written":
        entry_id = payload.get("memory_id")
        if entry_id and agent_id != settings.archivist_id:
            await check_and_merge(entry_id, client)

    elif event_type == "task.completed":
        task_id = payload.get("task_id")
        if task_id:
            await on_task_completed(task_id, agent_id, client)

    elif event_type == "task.created":
        task_id = payload.get("task_id")
        if task_id:
            await suggest_task_assignment(task_id, client)

    elif event_type == "task.failed":
        task_id = payload.get("task_id")
        if task_id and agent_id != settings.archivist_id:
            await on_task_failed(task_id, agent_id, client)


async def _event_watcher(client: ArtelClient) -> None:
    delay = 1.0
    while True:
        try:
            async for event in client.stream_events():
                delay = 1.0
                try:
                    await _dispatch(event, client)
                except Exception as e:
                    log.error("dispatch failed for %s: %s", event.get("type"), e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("event stream disconnected: %r, retrying in %.0fs", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def _scheduler(client: ArtelClient) -> None:
    while True:
        for fn, name in (
            (run_feed_triage, "feed_triage"),
            (run_synthesis, "synthesis"),
            (decay_confidence, "decay"),
            (run_promotion, "promotion"),
            (run_task_triage, "task_triage"),
            (run_brief, "brief"),
            (run_deep_synthesis_if_due, "deep_synthesis"),
            (run_utilization_prune_if_due, "utilization_prune"),
        ):
            try:
                await asyncio.wait_for(fn(client), timeout=300.0)
            except TimeoutError:
                log.error("%s timed out after 300s", name)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("%s failed: %s", name, e)
        try:
            await capture_metrics()
        except Exception as e:
            log.error("capture_metrics failed: %s", e)
        _HEARTBEAT.touch()
        await asyncio.sleep(settings.synthesis_interval)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if is_configured():
        log.info(
            "archivist starting — provider=%s model=%s",
            settings.archivist_provider,
            settings.archivist_model or "default",
        )
    else:
        log.info(
            "archivist starting in passive mode (no LLM configured) — decay and promotion only"
        )
    client = ArtelClient()
    try:
        await asyncio.gather(
            _event_watcher(client),
            _scheduler(client),
        )
    finally:
        await client.aclose()
