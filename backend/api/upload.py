"""
User-driven log upload.

The frontend POSTs a `.log` / `.txt` file to `/api/v1/upload`. The file
is streamed line-by-line into the same Redis stream the live runner
consumes, tagged with `source="user-upload"` so anomalies derived from
this file are distinguishable from replayed demo data and from real
production traffic.

The endpoint returns immediately after queuing the job (synchronously
counts lines + spawns an asyncio task for the streaming). Progress is
polled via `GET /upload/{job_id}/status` — that's simpler than reusing
the websocket and it's good enough for the demo.

Limits:
  - 50 MB file cap. Rejected EARLY via the Content-Length header before
    the body is even read, so a 5 GB upload fails fast instead of
    filling the request worker's memory first.
  - Rate cap of 1000 lines/sec. Default is 50 lines/sec (matches
    `tools/log_replay`'s default). The frontend hides the rate knob;
    operators or the demo can pass `?rate=500` from the URL bar to
    accelerate ingestion during a presentation.

State storage: an in-memory dict keyed by job_id. Single-process,
single-instance — deliberately not Redis-backed because the demo only
runs one API container and survival across API restarts isn't a goal.
A real deployment would key the state in Redis or Postgres.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import redis.asyncio as redis_aio
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from api.schemas import UploadJobResponse, UploadStatusResponse

# Tunables.
DEFAULT_RATE = 50
MIN_RATE = 10
MAX_RATE = 100
MAX_BYTES = 50 * 1024 * 1024  # 50 MB
# Hard cap on lines per upload / per /connect fetch.
MAX_LINES = 20_000
# Origin tag applied to every anomaly derived from a user upload — used by
# the frontend's `/anomalies?origin=user-upload` filter to scope the list
# to just-uploaded data. Distinct from the per-line `source` (parsed from
# the filename) so the UI can display a meaningful host/service name.
ORIGIN_TAG = "user-upload"
LOGS_RAW_STREAM = "logs:raw"
FIELD_LINE = "line"
FIELD_SOURCE = "source"
FIELD_ORIGIN = "origin"
ALLOWED_SUFFIXES = (".log", ".txt")


def _derive_source_from_filename(filename: str) -> str:
    """Sanitise a filename into a `source` identifier for the anomaly.

    Examples:
      `BGL.log`        → `bgl`
      `Thunderbolt.log`→ `thunderbolt`
      `My Logs.txt`    → `my-logs`

    Used as the anomaly's `source` so the dashboard groups all anomalies
    from one upload coherently. The origin tag (FIELD_ORIGIN, set to
    ORIGIN_TAG) is stored separately for filtering.
    """
    stem = Path(filename).stem.lower()
    sanitised = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return sanitised or "user-upload"

router = APIRouter(prefix="/api/v1")


# -- in-memory job registry ------------------------------------------------


@dataclass
class _UploadJob:
    """Per-job state. `task` is the spawned asyncio.Task; we keep a ref
    so it doesn't get garbage-collected mid-flight."""
    job_id: str
    status: str  # see UploadStatus literal
    rate: int
    total_lines: int = 0
    lines_streamed: int = 0
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


_jobs: dict[str, _UploadJob] = {}


def _redis_url() -> str:
    return os.environ.get("LOGGUARD_REDIS_URL", "redis://localhost:6379")


# -- background streamer ----------------------------------------------------


async def _stream_to_redis(
    job: _UploadJob, lines: list[tuple[str, str]],
) -> None:
    """Background worker: xadd one event at a time at the configured rate.

    `lines` is a list of `(raw_text, source)` tuples. For uploads the
    source is uniform (filename-derived) across every tuple. For
    `/connect` with a real URL fetch, each tuple may carry a different
    source extracted from the JSONL response — that's how a single
    Connect Save produces a multi-source mix on the dashboard.

    The origin tag (`ORIGIN_TAG = "user-upload"`) is uniform: both flows
    are user-driven, both go through this code path. Source is the
    display identifier; origin is the filter tag.
    """
    if job.rate <= 0:
        job.status = "failed"
        job.error = "invalid rate"
        return
    inter_line_delay = 1.0 / job.rate
    redis_client: redis_aio.Redis | None = None
    try:
        redis_client = redis_aio.from_url(_redis_url(), decode_responses=True)
        await redis_client.ping()
        job.status = "running"
        job.started_at = time.monotonic()
        for raw, source in lines:
            await redis_client.xadd(
                LOGS_RAW_STREAM,
                {FIELD_LINE: raw, FIELD_SOURCE: source, FIELD_ORIGIN: ORIGIN_TAG},
            )
            job.lines_streamed += 1
            await asyncio.sleep(inter_line_delay)
        job.status = "completed"
        job.completed_at = time.monotonic()
    except asyncio.CancelledError:
        job.status = "failed"
        job.error = "cancelled"
        raise
    except Exception as e:  # noqa: BLE001
        job.status = "failed"
        # Truncate so a giant traceback doesn't get stored in process memory.
        job.error = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001
                pass


# -- routes ----------------------------------------------------------------


@router.post("/upload", response_model=UploadJobResponse, status_code=202)
async def upload_file(
    request: Request,
    file: Annotated[UploadFile, File(description="Log file (.log or .txt), <= 50 MB")],
    rate: Annotated[
        int,
        Query(
            ge=MIN_RATE, le=MAX_RATE,
            description=(
                f"Lines per second to stream into Redis. "
                f"Range {MIN_RATE}-{MAX_RATE}, default {DEFAULT_RATE}."
            ),
        ),
    ] = DEFAULT_RATE,
) -> UploadJobResponse:
    # Reject early via Content-Length so a 5 GB upload doesn't get
    # buffered before we say no. Some clients omit Content-Length on
    # multipart; in that case we fall back to checking the materialised
    # body size below.
    cl_header = request.headers.get("content-length")
    if cl_header is not None:
        try:
            if int(cl_header) > MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File too large; max {MAX_BYTES // (1024 * 1024)} MB "
                        f"per upload."
                    ),
                )
        except ValueError:
            pass  # malformed header — let the body-size check catch it

    name = file.filename or ""
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(ALLOWED_SUFFIXES)} files are accepted.",
        )

    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large; max {MAX_BYTES // (1024 * 1024)} MB per upload."
            ),
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Decode + filter blank lines once, up front. Streamer iterates the
    # already-cleaned list so progress reporting stays accurate.
    text = raw.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file has no non-blank lines.",
        )
    if len(lines) > MAX_LINES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File has {len(lines):,} non-blank lines; max {MAX_LINES:,} "
                "per upload. Split the file or lower the line count."
            ),
        )

    source = _derive_source_from_filename(name)
    typed_lines: list[tuple[str, str]] = [(ln, source) for ln in lines]

    job_id = uuid.uuid4().hex[:12]
    job = _UploadJob(
        job_id=job_id,
        status="queued",
        rate=int(rate),
        total_lines=len(lines),
    )
    _jobs[job_id] = job
    job.task = asyncio.create_task(_stream_to_redis(job, typed_lines))

    return UploadJobResponse(
        job_id=job_id,
        total_lines=job.total_lines,
        rate=job.rate,
        status=job.status,
    )


# -- /connect: fetch logs from a URL ---------------------------------------
#
# The Connect page sends an HTTP(S) URL. The server fetches it and streams
# the lines into Redis through the same `_stream_to_redis` path /upload
# uses. Two body formats are accepted:
#
#   * NDJSON, one `{"line": ..., "source": ...}` object per line, which
#     keeps a per-line source name.
#   * Plain text, one log line per line. The source name is derived from
#     the last path segment of the URL (or its host name).


class ConnectRequest(BaseModel):
    url: str
    # Optional rate override; same range/default as /upload.
    rate: int | None = None


class ConnectResponse(BaseModel):
    job_id: str
    status: str
    source: str
    total_lines: int
    rate: int


def _is_http_url(value: str) -> bool:
    s = value.strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def _source_from_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return _derive_source_from_filename(last or parsed.hostname or "url-fetch")


def _parse_ndjson(lines: list[str]) -> list[tuple[str, str]] | None:
    """Parse `{line, source}` objects. Returns None if any line is not one."""
    out: list[tuple[str, str]] = []
    for s in lines:
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        line, source = obj.get("line"), obj.get("source")
        if not isinstance(line, str) or not isinstance(source, str):
            return None
        out.append((line, source))
    return out


async def _fetch_log_lines(url: str) -> list[tuple[str, str]]:
    """Fetch `url` and return `(line, source)` tuples, capped at MAX_LINES."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch URL: {e}") from e

    if len(resp.content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Fetched file is larger than 50 MB.")

    raw = [ln.strip() for ln in resp.text.splitlines() if ln.strip()][:MAX_LINES]
    if not raw:
        raise HTTPException(status_code=422, detail="The URL returned no log lines.")

    parsed = _parse_ndjson(raw)
    if parsed is not None:
        return parsed
    source = _source_from_url(url)
    return [(ln, source) for ln in raw]


@router.post("/connect", response_model=ConnectResponse, status_code=202)
async def connect_datasource(body: ConnectRequest) -> ConnectResponse:
    """Fetch a log file from an HTTP(S) URL and stream it into the pipeline."""
    if not _is_http_url(body.url):
        raise HTTPException(status_code=400, detail="Enter an http:// or https:// URL.")

    rate = int(body.rate) if body.rate is not None else DEFAULT_RATE
    if not (MIN_RATE <= rate <= MAX_RATE):
        raise HTTPException(
            status_code=400,
            detail=f"rate must be {MIN_RATE}-{MAX_RATE} lines/sec.",
        )

    typed_lines = await _fetch_log_lines(body.url.strip())
    sources = {src for _, src in typed_lines}
    source_label = sources.pop() if len(sources) == 1 else "mixed"

    job_id = uuid.uuid4().hex[:12]
    job = _UploadJob(
        job_id=job_id,
        status="queued",
        rate=rate,
        total_lines=len(typed_lines),
    )
    _jobs[job_id] = job
    job.task = asyncio.create_task(_stream_to_redis(job, typed_lines))

    return ConnectResponse(
        job_id=job_id,
        status="queued",
        source=source_label,
        total_lines=len(typed_lines),
        rate=rate,
    )


@router.get("/upload/{job_id}/status", response_model=UploadStatusResponse)
async def upload_status(job_id: str) -> UploadStatusResponse:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Upload job not found")

    eta_seconds: int | None = None
    if (
        job.status == "running"
        and job.started_at is not None
        and job.lines_streamed > 0
    ):
        elapsed = time.monotonic() - job.started_at
        if elapsed > 0:
            achieved_rate = job.lines_streamed / elapsed
            remaining = job.total_lines - job.lines_streamed
            if achieved_rate > 0 and remaining > 0:
                eta_seconds = int(remaining / achieved_rate)

    return UploadStatusResponse(
        job_id=job_id,
        status=job.status,  # type: ignore[arg-type]
        lines_streamed=job.lines_streamed,
        total_lines=job.total_lines,
        eta_seconds=eta_seconds,
        error=job.error,
    )


# Test-only helper. Lets a test reset the registry between assertions
# without yanking module-level globals via monkeypatch.
def _reset_jobs() -> None:
    _jobs.clear()
