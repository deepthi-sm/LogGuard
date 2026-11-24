"""
Decision layer between the raw detector output and the canonical Anomaly
object the API + websocket emit.

Three things happen here:

  1. Severity scoring — three rules, applied in order:
       (a) `ensemble_score > 0.95` → critical
       (b) `ensemble_score > 0.75` → warning
       (c) else                    → info
     All three tiers are cut from the same signal (ensemble_score), so
     severity depends only on what the model scored, never on the
     host name or the file the log came from. The
     thresholds are intentionally separate from the binary anomaly gate
     (`Detector.is_anomaly`): the detector decides IF an anomaly fires;
     this module decides HOW LOUDLY. Info fires for any window that
     passed the detection gate but didn't reach the warning floor —
     these surface in the dashboard timeline without paging.

  2. Deduplication — multiple windows from the same template + source
     within a 60-second window collapse into one cluster. Same
     `cluster_id`, `cluster_size` increments. Stops a single bad event
     from creating 500 separate alerts.

  3. Building the canonical `Anomaly` object — assembles all the fields
     the frontend expects, including `top_contributing_lines` (the
     attention-ranked subset of the window) and `predicted_failure_window_min`
     (set only when the failure regressor returns a positive value).

This module is in-memory only — Step 4b-ii adds Postgres persistence.
For now the dedup state lives in process memory, which is enough for a
single-worker demo.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from api.schemas import Anomaly, ContributingLine, ExplanationStatus, Severity
from ingestion.sequence_builder import Window
from ml.detector import DetectionResult

# Severity thresholds on the ensemble score. The model tends to score
# windows bimodally (close to 1 or close to 0), so the critical cut-off
# sits high to keep that tier small.
CRITICAL_ENSEMBLE_SCORE = 0.95
WARNING_ENSEMBLE_SCORE = 0.75

# Dedup window — same (template, source) within this many seconds is the
# same cluster. 60s mirrors the spec's design note.
DEFAULT_DEDUP_WINDOW_S = 60

# How many of the highest-attention events to surface as `top_contributing_lines`.
DEFAULT_TOP_CONTRIBUTING = 4

# -- Severity scoring -------------------------------------------------------


def decide_severity(detection: DetectionResult) -> Severity:
    """Map detector output → one of "critical" / "warning" / "info"."""
    if detection.ensemble_score > CRITICAL_ENSEMBLE_SCORE:
        return "critical"
    if detection.ensemble_score > WARNING_ENSEMBLE_SCORE:
        return "warning"
    return "info"


# -- Deduplication ----------------------------------------------------------


@dataclass
class _ClusterRecord:
    cluster_id: str
    last_seen: datetime
    count: int


class Deduplicator:
    """In-memory `(template, source)` → cluster tracker with TTL eviction.

    Single-process — Step 4b-ii will replace this with a Postgres-backed
    lookup so multiple workers see the same cluster ids. For the demo's
    single-worker flow, in-memory is enough and avoids a DB round-trip
    per window.
    """

    def __init__(self, *, window_s: int = DEFAULT_DEDUP_WINDOW_S) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self._window_s = window_s
        self._clusters: dict[tuple[str, str], _ClusterRecord] = {}

    def assign(
        self,
        template: str,
        source: str,
        detected_at: datetime,
    ) -> tuple[str, int]:
        """Return the (cluster_id, cluster_size) for this anomaly."""
        cutoff = detected_at - timedelta(seconds=self._window_s)
        key = (template, source)
        rec = self._clusters.get(key)
        if rec is not None and rec.last_seen >= cutoff:
            rec.count += 1
            rec.last_seen = detected_at
            return rec.cluster_id, rec.count

        # Either no record or it's stale — start a new cluster.
        cluster_id = _new_cluster_id()
        self._clusters[key] = _ClusterRecord(
            cluster_id=cluster_id,
            last_seen=detected_at,
            count=1,
        )
        # Opportunistic cleanup — keep the dict from growing unboundedly
        # without paying for it on every call.
        if len(self._clusters) % 256 == 0:
            self._evict_expired(cutoff)
        return cluster_id, 1

    def _evict_expired(self, cutoff: datetime) -> None:
        stale = [k for k, v in self._clusters.items() if v.last_seen < cutoff]
        for k in stale:
            del self._clusters[k]


def _new_cluster_id() -> str:
    """Short, opaque cluster identifier — same shape as the mock fixtures."""
    return f"clu_{uuid.uuid4().hex[:8]}"


# -- Anomaly construction --------------------------------------------------


def new_anomaly_id(detected_at: datetime) -> str:
    """Generate `anom_<iso8601>_<4hex>` per project conventions naming convention."""
    iso = detected_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"anom_{iso}_{uuid.uuid4().hex[:4]}"


def _top_contributing_lines(
    window: Window,
    attention: tuple[float, ...],
    *,
    n: int = DEFAULT_TOP_CONTRIBUTING,
) -> list[ContributingLine]:
    """Rank events in the window by attention and return the top N as
    `ContributingLine` records."""
    pairs = list(zip(window.raw_lines, attention, strict=True))
    pairs.sort(key=lambda p: p[1], reverse=True)
    return [
        ContributingLine(line=line, attention=float(score))
        for line, score in pairs[:n]
    ]


@dataclass(frozen=True)
class AnomalyContext:
    """All the inputs needed to build a canonical Anomaly object.

    Wraps everything to keep `build_anomaly()` ergonomic (otherwise it
    grows to ~10 positional args)."""
    window: Window
    detection: DetectionResult
    severity: Severity
    cluster_id: str
    cluster_size: int
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    explanation_status: ExplanationStatus = "pending"


def build_anomaly(ctx: AnomalyContext) -> Anomaly:
    """Assemble a canonical `Anomaly` Pydantic from a detection result.

    The frontend's `top_contributing_lines` rendering reads `attention`
    so we surface the highest-attention events first. The
    `predicted_failure_window_min` carries the failure regressor's
    output when it is positive, and is null otherwise.
    """
    return Anomaly(
        id=new_anomaly_id(ctx.detected_at),
        detected_at=ctx.detected_at,
        severity=ctx.severity,
        source=ctx.window.source,
        # Window.origin is a plain str; Anomaly.origin is a Literal enum.
        # Pydantic validates the literal at construction — invalid values
        # raise ValidationError, which is the desired loud-fail.
        origin=ctx.window.origin,  # type: ignore[arg-type]
        ensemble_score=_clamp01(ctx.detection.ensemble_score),
        confidence=_clamp01(ctx.detection.confidence),
        failure_probability=_clamp01(ctx.detection.transformer_prob),
        predicted_failure_window_min=_failure_window_min(
            ctx.detection.predicted_failure_minutes,
        ),
        log_template=ctx.window.templates[-1],
        sequence_preview=list(ctx.window.raw_lines),
        top_contributing_lines=_top_contributing_lines(
            ctx.window, ctx.detection.attention
        ),
        explanation_status=ctx.explanation_status,
        cluster_id=ctx.cluster_id,
        cluster_size=ctx.cluster_size,
    )


def _clamp01(x: float) -> float:
    """Clamp into [0, 1]. The schema has Field(ge=0, le=1) so out-of-range
    values would 422 the response — better to clip silently than crash on
    a model that briefly produces a spurious 1.0001."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _failure_window_min(model_pred: int) -> int | None:
    """Minutes until predicted failure, taken from the transformer's
    failure-regression head. Returns None when the head gives no
    positive estimate, so the dashboard shows a dash instead of a value
    the model did not produce."""
    return model_pred if model_pred > 0 else None
