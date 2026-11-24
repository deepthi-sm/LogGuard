r"""
Log replay tool.

Reads a log file and streams its lines into the Redis stream `logs:raw`
using the schema the ingestion consumer expects:

    XADD logs:raw * line "INFO nova-api ..." source "openstack-abnormal"

Every line is tagged with one source name. By default the name is the
input file's stem (lower-cased, underscores replaced with hyphens), so
anomalies on the dashboard can be traced back to the file they came
from. Pass `--source` to override it.

CLI

    # default: replays openstack_abnormal.log at 10 lines/sec
    python -m tools.log_replay

    # custom file + faster rate + loop forever
    python -m tools.log_replay --input training/data/openstack/openstack_normal1.log \
                               --rate 25 --loop

    # one-shot test: emit 100 lines and exit
    python -m tools.log_replay --max-lines 100 --rate 50

Env vars (all optional, fall back to defaults below):
    LOGGUARD_REDIS_URL       default redis://localhost:6379
    LOGGUARD_INGEST_STREAM   default logs:raw
    LOGGUARD_REPLAY_INPUT    default training/data/openstack/openstack_abnormal.log
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redis.asyncio as redis_aio

from ingestion.consumer import DEFAULT_REDIS_URL, DEFAULT_STREAM, FIELD_LINE, FIELD_SOURCE

log = logging.getLogger(__name__)

DEFAULT_RATE = 10.0
DEFAULT_INPUT = "training/data/openstack/openstack_abnormal.log"


@dataclass
class ReplayStats:
    """Counters for the CLI summary on shutdown."""
    lines_emitted: int = 0
    publish_failures: int = 0


def source_from_path(path: Path) -> str:
    """Derive a source name from a file name, e.g. `HDFS_2k.log` -> `hdfs-2k`."""
    return path.stem.lower().replace("_", "-") or "replay"


# -- file iteration --------------------------------------------------------


async def _iter_lines(path: Path, *, loop: bool, max_lines: int | None) -> AsyncIterator[str]:
    """Async generator over log lines from `path`. Empty / whitespace-only
    lines are skipped (the consumer's parser would reject them anyway).
    Loops forever if `loop=True`."""
    emitted = 0
    while True:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n").rstrip("\r")
                if not line.strip():
                    continue
                yield line
                emitted += 1
                if max_lines is not None and emitted >= max_lines:
                    return
        if not loop:
            return


# -- core replay loop ------------------------------------------------------


async def replay(
    redis_client: redis_aio.Redis,
    *,
    input_path: Path,
    stream: str,
    rate: float,
    loop: bool,
    max_lines: int | None,
    source: str | None = None,
) -> ReplayStats:
    """Replay `input_path` into Redis stream `stream`, one XADD per line."""
    if rate <= 0:
        raise ValueError("rate must be positive")
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    tag = source or source_from_path(input_path)
    inter_line_delay = 1.0 / rate
    stats = ReplayStats()

    async for line in _iter_lines(input_path, loop=loop, max_lines=max_lines):
        try:
            await redis_client.xadd(stream, {FIELD_LINE: line, FIELD_SOURCE: tag})
            stats.lines_emitted += 1
        except Exception:  # noqa: BLE001
            log.exception("xadd failed for line %d", stats.lines_emitted + 1)
            stats.publish_failures += 1
        await asyncio.sleep(inter_line_delay)

    return stats


# -- CLI -------------------------------------------------------------------


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Replay a log file into the LogGuard Redis stream.",
    )
    p.add_argument(
        "--input", type=Path,
        default=Path(os.environ.get("LOGGUARD_REPLAY_INPUT", DEFAULT_INPUT)),
        help="Log file to replay (default: %(default)s)",
    )
    p.add_argument(
        "--redis-url", type=str,
        default=os.environ.get("LOGGUARD_REDIS_URL", DEFAULT_REDIS_URL),
        help="Redis connection URL (default: %(default)s)",
    )
    p.add_argument(
        "--stream", type=str,
        default=os.environ.get("LOGGUARD_INGEST_STREAM", DEFAULT_STREAM),
        help="Redis stream name (default: %(default)s)",
    )
    p.add_argument(
        "--rate", type=float, default=DEFAULT_RATE,
        help="Lines per second (default: %(default)s)",
    )
    p.add_argument(
        "--max-lines", type=int, default=None,
        help="Stop after this many lines (default: replay the whole file)",
    )
    p.add_argument(
        "--loop", action="store_true",
        help="When the file ends, restart from the top forever.",
    )
    p.add_argument(
        "--source", type=str, default=None,
        help="Source name attached to every line (default: derived from the file name)",
    )
    return p


async def _run_cli(argv: list[str] | None = None) -> int:  # pragma: no cover
    args = _arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info(
        "starting replay: input=%s stream=%s rate=%s/s loop=%s",
        args.input, args.stream, args.rate, args.loop,
    )

    client: Any = redis_aio.from_url(args.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception as e:  # noqa: BLE001
        log.error("cannot reach Redis at %s: %s", args.redis_url, e)
        await client.aclose()
        return 1

    try:
        stats = await replay(
            client,
            input_path=args.input,
            stream=args.stream,
            rate=args.rate,
            loop=args.loop,
            max_lines=args.max_lines,
            source=args.source,
        )
    except asyncio.CancelledError:
        log.info("replay cancelled")
        return 0
    finally:
        await client.aclose()

    log.info("done. emitted=%d failures=%d", stats.lines_emitted, stats.publish_failures)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_run_cli()))
