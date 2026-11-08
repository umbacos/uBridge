"""Translate the supported Lovelace subset into display entities."""

import math
import re
from dataclasses import dataclass, field
from typing import Any

ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
LEAF_CARDS = {"button", "tile", "entity", "sensor", "gauge", "thermostat"}
CONTAINERS = {"grid", "horizontal-stack", "vertical-stack"}


@dataclass(frozen=True)
class Item:
    entity_id: str
    name: str | None = None
    tap_action: dict[str, Any] | None = None
    icon: str | None = None
    kind: str = "tile"
    minimum: float = 0
    maximum: float = 100
    severity: tuple[tuple[float, str], ...] = ()
    series: tuple[str, ...] = ()
    hours: float = 24
    period: str = "hour"
    statistic: str = "mean"
    chart_type: str = "line"
    needle: bool = False
    segments: tuple = ()
    unit: str | None = None
    attribute: str | None = None
    clock_style: str = "digital"
    clock_size: str = "small"
    time_format: str = "24"
    time_zone: str = ""
    show_seconds: bool = False
    no_background: bool = False
    clock_border: bool = False
    clock_ticks: str = "hour"
    show_current: bool = True
    show_forecast: bool = True
    forecast_type: str = "daily"
    secondary_info: str = ""
    round_temperature: bool = False

    @property
    def history_key(self):
        return (self.kind, self.series, self.hours, self.period, self.statistic)


@dataclass(frozen=True)
class Page:
    key: str
    title: str
    items: tuple[Item, ...]
    icon: str = "mdi:view-dashboard"
    badges: tuple[Item, ...] = ()


@dataclass
class Entity:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def available(self) -> bool:
        # Stateless entities have an unknown last-activation timestamp until
        # first used. They still accept a press/run in Home Assistant.
        return self.state != "unavailable" and (
            self.state != "unknown" or self.domain in {"scene", "button", "input_button"}
        )

    @property
    def label(self) -> str:
        return str(self.attributes.get("friendly_name", self.entity_id))

    @property
    def value(self) -> str:
        if self.state == "unknown" and self.available:
            return "Ready"
        if self.domain == "climate" and self.available:
            current = self.attributes.get("current_temperature", "?")
            target = self.attributes.get("temperature", "?")
            return f"{current} / {target} {self.state}"
        unit = self.attributes.get("unit_of_measurement", "")
        return f"{self.state} {unit}".strip()


def dashboard_number(title: str, prefix: str = "TJC") -> int | None:
    match = re.fullmatch(re.escape(prefix) + r"([1-9][0-9]*)", title, re.IGNORECASE)
    return int(match[1]) if match else None


def parse_dashboard(key: str, title: str, config: dict, icon=None) -> tuple[list[Page], list[str]]:
    """One dashboard is a logical page; additional views have their own pages."""
    warnings: list[str] = []
    pages = []

    def number(value, default):
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) else default
        except (ValueError, TypeError):
            return default

    def item(value: Any, parent: dict | None = None) -> list[Item]:
        if isinstance(value, str):
            value = {"entity": value}
        if not isinstance(value, dict):
            warnings.append(f"{title}: unsupported entity row")
            return []
        if value.get("type") in {"section", "divider"}:
            return []
        if (
            str(value.get("type", "")).startswith("custom:")
            and value.get("type") != "custom:bar-card"
        ) or "visibility" in value:
            warnings.append(f"{title}: custom/visibility entity row skipped")
            return []
        entity_id = value.get("entity")
        if not isinstance(entity_id, str) or not ENTITY_RE.fullmatch(entity_id):
            warnings.append(f"{title}: entity row has no valid entity_id")
            return []
        action = value.get("tap_action", (parent or {}).get("tap_action"))
        if action is not None and not isinstance(action, dict):
            warnings.append(f"{title}: invalid tap_action; row skipped")
            return []
        kind = value.get("type", "tile")
        if kind == "custom:bar-card":
            kind = "bar"
        elif kind == "sensor" and value.get("graph") == "line":
            kind = "history-graph"
        elif kind not in {"gauge", "bar", "history-graph"}:
            kind = "tile"
        low = number(value.get("min"), 0)
        high = max(low + 0.001, number(value.get("max"), 100))
        severity = value.get("severity") or {}
        if not isinstance(severity, dict):
            severity = {}
        return [
            Item(
                entity_id,
                value.get("name"),
                action,
                value.get("icon"),
                kind,
                low,
                high,
                tuple(
                    sorted(
                        (number(v, 0), k)
                        for k, v in severity.items()
                        if k in {"green", "yellow", "red"}
                    )
                ),
                (entity_id,) if kind == "history-graph" else (),
                max(0.05, min(168, number(value.get("hours_to_show"), 24))),
                needle=bool(value.get("needle", False)),
                segments=tuple(
                    sorted(
                        (number(v.get("from"), 0), str(v.get("color", "")), str(v.get("label", "")))
                        for v in (value.get("segments") or [])
                        if isinstance(v, dict)
                    )
                ),
                unit=value.get("unit"),
                attribute=value.get("attribute"),
            )
        ]

    def card(value: Any) -> list[Item]:
        if not isinstance(value, dict):
            return []
        kind = value.get("type", "")
        # Never expose hidden/conditional controls unconditionally on the panel.
        if "visibility" in value or kind == "conditional":
            warnings.append(f"{title}: conditional/visibility card skipped")
            return []
        if kind in CONTAINERS:
            return [entry for child in value.get("cards", []) for entry in card(child)]
        if kind in {"entities", "glance"}:
            return [entry for row in value.get("entities", []) for entry in item(row)]
        if kind == "clock":
            analog = value.get("analog_options") or {}
            if not isinstance(analog, dict):
                analog = {}
            return [
                Item(
                    "",
                    value.get("title"),
                    kind="clock",
                    clock_style="analog" if value.get("clock_style") == "analog" else "digital",
                    clock_size=str(value.get("clock_size", "small")),
                    time_format="12" if str(value.get("time_format")) == "12" else "24",
                    time_zone=str(value.get("time_zone") or ""),
                    show_seconds=bool(value.get("show_seconds", False)),
                    no_background=bool(value.get("no_background", False)),
                    clock_border=bool(analog.get("border", False)),
                    clock_ticks=str(analog.get("ticks", "hour")),
                )
            ]
        if kind == "weather-forecast":
            eid = value.get("entity")
            if (
                not isinstance(eid, str)
                or not ENTITY_RE.fullmatch(eid)
                or not eid.startswith("weather.")
            ):
                warnings.append(f"{title}: weather card needs a weather entity")
                return []
            forecast_type = value.get("forecast_type", "daily")
            return [
                Item(
                    eid,
                    value.get("name") if isinstance(value.get("name"), str) else None,
                    value.get("tap_action") if isinstance(value.get("tap_action"), dict) else None,
                    kind="weather-forecast",
                    show_current=bool(value.get("show_current", True)),
                    show_forecast=bool(value.get("show_forecast", True)),
                    forecast_type=forecast_type
                    if forecast_type in {"daily", "hourly", "twice_daily"}
                    else "daily",
                    secondary_info=str(value.get("secondary_info_attribute") or ""),
                    round_temperature=bool(value.get("round_temperature", False)),
                )
            ]
        if kind in {"history-graph", "statistics-graph"}:
            series = tuple(
                row if isinstance(row, str) else row.get("entity", "")
                for row in value.get("entities", [])
                if isinstance(row, (str, dict))
            )
            series = tuple(
                eid for eid in series if isinstance(eid, str) and ENTITY_RE.fullmatch(eid)
            )
            if not series:
                warnings.append(f"{title}: chart has no valid entities")
                return []
            if len(series) > 3:
                warnings.append(f"{title}: charts show the first three series")
            period = value.get("period", "hour")
            if period not in {"5minute", "hour", "day", "week", "month", "year"}:
                period = "hour"
            stat = next(iter(value.get("stat_types") or ["mean"]))
            if stat not in {"mean", "min", "max", "sum", "state", "change"}:
                stat = "mean"
            return [
                Item(
                    series[0],
                    value.get("title"),
                    icon=value.get("icon"),
                    kind=kind,
                    series=series[:3],
                    hours=max(0.05, min(168, number(value.get("hours_to_show"), 24))),
                    period=period,
                    statistic=stat,
                    chart_type="bar" if value.get("chart_type") == "bar" else "line",
                )
            ]
        if kind == "custom:bar-card":
            if "entity" in value:
                return item(value)
            return [
                entry
                for row in value.get("entities", [])
                if isinstance(row, (str, dict))
                for entry in item(
                    {**value, **({"entity": row} if isinstance(row, str) else row), "entities": []}
                )
            ]
        if kind in LEAF_CARDS:
            return item(value)
        if kind in {"heading", "markdown"}:
            warnings.append(f"{title}: {kind} is not rendered")
            return []
        warnings.append(f"{title}: unsupported card {kind!r} skipped")
        return []

    for index, view in enumerate(config.get("views", [])):
        if not isinstance(view, dict):
            continue
        if any(k in view for k in ("visible", "visibility")):
            warnings.append(f"{title}: view with visibility rules skipped")
            continue
        items = [entry for value in view.get("cards", []) for entry in card(value)]
        for section in view.get("sections", []):
            if not isinstance(section, dict):
                continue
            if any(k in section for k in ("visibility", "visible", "conditional")):
                warnings.append(f"{title}: section with visibility rules skipped")
                continue
            items.extend(entry for value in section.get("cards", []) for entry in card(value))
        label = title if index == 0 else f"{title} / {view.get('title', index + 1)}"
        badges = []
        for badge in view.get("badges", []):
            if isinstance(badge, dict) and (
                badge.get("type") == "conditional" or "visibility" in badge
            ):
                warnings.append(f"{title}: conditional/visibility badge skipped")
                continue
            badges.extend(item(badge))
        pages.append(
            Page(
                f"{key}/{view.get('path', index)}",
                label,
                tuple(items),
                view.get("icon") or icon or "mdi:view-dashboard",
                tuple(badges),
            )
        )
    if not pages:
        warnings.append(f"{title}: no explicit visible views; take control of generated dashboards")
    return pages, warnings
