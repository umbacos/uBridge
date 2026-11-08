"""Bounded chart data shared by the local integration and desktop bridge."""

import math
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Sample:
    time: float
    value: float | None


@dataclass
class HistoryData:
    start: float
    end: float
    series: dict[str, tuple[Sample, ...]] = field(default_factory=dict)
    error: str = ""


def numeric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, AttributeError):
        return None


def compact(samples, limit=96):
    """Keep ordered extrema and unavailable gaps, with a strict UART budget."""
    samples = sorted(samples, key=lambda point: point.time)
    if len(samples) <= limit:
        return tuple(samples)
    result = [samples[0]]
    buckets = max(1, (limit - 2) // 3)
    for index in range(buckets):
        group = samples[
            1 + (len(samples) - 2) * index // buckets : 1
            + (len(samples) - 2) * (index + 1) // buckets
        ]
        valid = [point for point in group if point.value is not None]
        selected = []
        if valid:
            selected.extend((min(valid, key=lambda p: p.value), max(valid, key=lambda p: p.value)))
        gap = next((point for point in group if point.value is None), None)
        if gap:
            selected.append(gap)
        result.extend(sorted(set(selected), key=lambda p: p.time))
    result.append(samples[-1])
    return tuple(result)


def decode_history(raw, start, end, *, statistic=None):
    result = {}
    for entity_id, rows in (raw or {}).items():
        samples = []
        for row in rows:
            when = timestamp(
                row.get("start")
                if statistic
                else row.get("lu", row.get("last_updated", row.get("last_changed")))
            )
            if when is None:
                continue
            value = numeric(row.get(statistic) if statistic else row.get("s", row.get("state")))
            samples.append(Sample(max(start, when), value))
        if not statistic and samples and samples[-1].time < end:
            samples.append(Sample(end, samples[-1].value))
        result[entity_id] = compact(samples)
    return HistoryData(start, end, result)


def requests(pages):
    return {item.history_key: item for page in pages for item in page.items if item.series}
