import argparse
import asyncio
import logging

from .agent import run
from .client import ArtelClient
from .synthesis import run_synthesis


async def _backfill(hours: int, window_hours: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("archivist.backfill")
    client = ArtelClient()
    try:
        passes = max(1, hours // window_hours)
        log.info("backfill dedup: total=%dh, window=%dh, passes=%d", hours, window_hours, passes)
        for i in range(passes):
            since = (i + 1) * window_hours
            log.info("pass %d/%d: lookback=%dh", i + 1, passes, since)
            await run_synthesis(client, since_hours=since)
        log.info("backfill complete")
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(prog="artel.archivist")
    parser.add_argument(
        "--backfill", action="store_true", help="Run a one-shot full-history dedup pass and exit"
    )
    parser.add_argument(
        "--hours", type=int, default=24 * 90, help="Total lookback for --backfill (default 90 days)"
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24 * 7,
        help="Per-pass window for --backfill (default 7 days)",
    )
    args = parser.parse_args()
    if args.backfill:
        asyncio.run(_backfill(args.hours, args.window_hours))
    else:
        asyncio.run(run())


if __name__ == "__main__":
    main()
