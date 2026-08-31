"""The three numbers that answer most questions: how many, how long, how many failed.

Kept deliberately small. This emits the Prometheus text exposition format, which any
scraper or OpenTelemetry collector understands, without taking a client library
dependency for what is a hundred lines of formatting.

Cardinality is the thing that makes metrics expensive, so labels are restricted to a
known set of low-cardinality values. A customer identifier must never become a label.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final

#: Latency buckets in seconds, chosen around the 10 second tool budget.
DEFAULT_BUCKETS: Final[tuple[float, ...]] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

#: Labels permitted on any metric. Anything else is a cardinality incident.
ALLOWED_LABELS: Final[frozenset[str]] = frozenset({"tool", "outcome", "code", "backend", "stage"})

_MAX_SERIES: Final = 2000


@dataclass(slots=True)
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    observations: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self.total += value
        self.observations += 1
        for index, edge in enumerate(self.buckets):
            if value <= edge:
                self.counts[index] += 1
                return
        self.counts[-1] += 1


LabelKey = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class HistogramSummary:
    """An immutable read of one histogram series."""

    buckets: tuple[float, ...]
    #: One count per bucket, plus a final count for everything above the last edge.
    counts: tuple[int, ...]
    total: float
    observations: int

    @property
    def mean(self) -> float:
        return self.total / self.observations if self.observations else 0.0

    def quantile(self, phi: float) -> float:
        """Interpolate a quantile from the bucket counts.

        Histogram quantiles are approximations and this one says so: the answer is the
        upper edge of the bucket the target observation falls in, which is the same
        thing a Prometheus `histogram_quantile` gives on coarse buckets. It is good
        enough to alert on and not good enough to put in a contract, and the buckets
        are chosen around the ten second tool budget so the interesting range is the
        one with resolution.
        """
        if self.observations == 0:
            return 0.0
        target = phi * self.observations
        cumulative = 0
        for index, edge in enumerate(self.buckets):
            cumulative += self.counts[index]
            if cumulative >= target:
                return edge
        # Everything above the last edge: report the last edge rather than infinity,
        # and let the +Inf bucket count tell the story of how far above it went.
        return self.buckets[-1] if self.buckets else 0.0

    @property
    def above_last_bucket(self) -> int:
        """Observations past the last edge. The number that matters in an incident."""
        return self.counts[-1] if self.counts else 0


class Metrics:
    """A tiny, thread-safe metrics registry."""

    def __init__(self, buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._buckets = buckets
        self._counters: dict[str, dict[LabelKey, float]] = defaultdict(dict)
        self._gauges: dict[str, dict[LabelKey, float]] = defaultdict(dict)
        self._histograms: dict[str, dict[LabelKey, _Histogram]] = defaultdict(dict)

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = _label_key(labels)
        with self._lock:
            series = self._counters[name]
            if key not in series and len(series) >= _MAX_SERIES:
                return  # refuse to grow without bound rather than exhaust memory
            series[key] = series.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = _label_key(labels)
        with self._lock:
            self._gauges[name][key] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = _label_key(labels)
        with self._lock:
            series = self._histograms[name]
            if key not in series:
                if len(series) >= _MAX_SERIES:
                    return
                series[key] = _Histogram(self._buckets)
            series[key].observe(value)

    def snapshot(self) -> dict[str, dict[LabelKey, float]]:
        """Counter and gauge values, for assertions and for debugging."""
        with self._lock:
            merged = {name: dict(series) for name, series in self._counters.items()}
            merged.update({name: dict(series) for name, series in self._gauges.items()})
            return merged

    def histogram_snapshot(self) -> dict[str, dict[LabelKey, HistogramSummary]]:
        """Bucket counts, sum and observation count for every histogram series.

        The scrape path renders the same data as text. This exists because the KPI
        endpoint has to compute a latency quantile in process, and parsing our own
        exposition format back into numbers to do it would be an unusually silly way
        to introduce a bug.
        """
        with self._lock:
            return {
                name: {
                    key: HistogramSummary(
                        buckets=hist.buckets,
                        counts=tuple(hist.counts),
                        total=hist.total,
                        observations=hist.observations,
                    )
                    for key, hist in series.items()
                }
                for name, series in self._histograms.items()
            }

    def render_prometheus(self) -> str:
        """Render the exposition format a scraper reads."""
        lines: list[str] = []
        with self._lock:
            for name, series in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines += [f"{name}{_render_labels(k)} {v:g}" for k, v in sorted(series.items())]
            for name, series in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines += [f"{name}{_render_labels(k)} {v:g}" for k, v in sorted(series.items())]
            for name, hist_series in sorted(self._histograms.items()):
                lines.append(f"# TYPE {name} histogram")
                for key, hist in sorted(hist_series.items()):
                    cumulative = 0
                    for index, edge in enumerate(hist.buckets):
                        cumulative += hist.counts[index]
                        bucket = _render_labels(key, extra=("le", _format_float(edge)))
                        lines.append(f"{name}_bucket{bucket} {cumulative}")
                    total = cumulative + hist.counts[-1]
                    lines.append(
                        f"{name}_bucket{_render_labels(key, extra=('le', '+Inf'))} {total}"
                    )
                    lines.append(f"{name}_sum{_render_labels(key)} {hist.total:g}")
                    lines.append(f"{name}_count{_render_labels(key)} {hist.observations}")
        return "\n".join(lines) + "\n"


def _label_key(labels: dict[str, str]) -> LabelKey:
    unknown = set(labels) - ALLOWED_LABELS
    if unknown:
        raise ValueError(
            f"metric labels {sorted(unknown)} are not allowed; permitted labels are "
            f"{sorted(ALLOWED_LABELS)}. High-cardinality values belong in logs, not metrics."
        )
    return tuple(sorted((key, str(value)) for key, value in labels.items()))


def _render_labels(key: LabelKey, extra: tuple[str, str] | None = None) -> str:
    pairs = list(key) + ([extra] if extra else [])
    if not pairs:
        return ""
    rendered = ",".join(f'{name}="{_escape(value)}"' for name, value in pairs)
    return "{" + rendered + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_float(value: float) -> str:
    return f"{value:g}"
