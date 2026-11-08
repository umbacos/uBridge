"""Entity capability based actions. No arbitrary command execution from UART."""

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .model import Entity, Item


@dataclass(frozen=True)
class Action:
    label: str
    domain: str = ""
    service: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    confirmation: str | None = None
    navigation: str | None = None
    detail: bool = False


def service_action(entity: Entity, service: str, label: str, **data: Any) -> Action:
    return Action(label, entity.domain, service, data, {"entity_id": entity.entity_id})


def explicit_action(item: Item) -> Action | None:
    config = item.tap_action
    if not config:
        return None
    kind = config.get("action", "none")
    if not isinstance(kind, str):
        return Action("Invalid action")
    confirm = config.get("confirmation")
    confirmation = None
    if confirm:
        confirmation = (
            confirm.get("text", "Run this action?")
            if isinstance(confirm, dict)
            else "Run this action?"
        )
    if kind in {"perform-action", "call-service"}:
        name = config.get("perform_action", config.get("service", ""))
        if not isinstance(name, str) or not re.fullmatch(r"[a-z_]+\.[a-z_]+", name):
            return Action("Invalid action")
        domain, service = name.split(".")
        data = config.get("data", config.get("service_data", {}))
        target = config.get("target", {})
        if not isinstance(data, dict) or not isinstance(target, dict):
            return Action("Invalid action")
        if not target and "entity_id" not in data:
            target = {"entity_id": item.entity_id}
        return Action("Run", domain, service, dict(data), dict(target), confirmation)
    if kind == "navigate":
        path = config.get("navigation_path")
        return (
            Action("Open", navigation=path) if isinstance(path, str) else Action("Invalid action")
        )
    if kind == "more-info":
        return Action("Controls", detail=True)
    if kind == "toggle":
        return Action(
            "Toggle", "homeassistant", "toggle", {}, {"entity_id": item.entity_id}, confirmation
        )
    return Action("Read only")


def default_action(entity: Entity) -> Action:
    domain = entity.domain
    if domain in {"light", "switch", "input_boolean", "fan", "automation"}:
        return service_action(entity, "toggle", "Turn off" if entity.state == "on" else "Turn on")
    if domain in {"scene", "script"}:
        return service_action(entity, "turn_on", "Run")
    if domain in {"button", "input_button"}:
        return service_action(entity, "press", "Press")
    if domain in {
        "cover",
        "climate",
        "number",
        "input_number",
        "select",
        "input_select",
        "media_player",
    }:
        return Action("Controls", detail=True)
    return Action("Read only", detail=True)


def step_value(entity: Entity, attribute: str | None, delta: int) -> float | None:
    """Use decimal stepping and clamp, including unavailable/malformed attributes."""
    try:
        raw = entity.state if attribute is None else entity.attributes.get(attribute)
        value = Decimal(str(raw))
        step = Decimal(
            str(
                entity.attributes.get(
                    "target_temp_step" if attribute else "step", 0.5 if attribute else 1
                )
            )
        )
        minimum = Decimal(
            str(entity.attributes.get("min_temp" if attribute else "min", 7 if attribute else 0))
        )
        maximum = Decimal(
            str(entity.attributes.get("max_temp" if attribute else "max", 35 if attribute else 100))
        )
        if (
            not all(x.is_finite() for x in (value, step, minimum, maximum))
            or step <= 0
            or maximum < minimum
        ):
            return None
        return float(max(minimum, min(maximum, value + step * delta)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def controls(entity: Entity) -> list[Action]:
    if not entity.available:
        return []
    domain, attrs = entity.domain, entity.attributes
    try:
        features = int(attrs.get("supported_features", 0) or 0)
    except (ValueError, TypeError, OverflowError):
        features = 0
    if domain in {"number", "input_number", "climate"}:
        if domain == "climate" and not features & 1:  # Single target temperature.
            return []
        field = "temperature" if domain == "climate" else "value"
        service = "set_temperature" if domain == "climate" else "set_value"
        result = []
        for delta, label in ((-1, "-"), (1, "+")):
            value = step_value(entity, "temperature" if domain == "climate" else None, delta)
            if value is not None:
                result.append(service_action(entity, service, label, **{field: value}))
        return result
    if domain == "light":
        result = [default_action(entity)]
        modes = set(attrs.get("supported_color_modes", []))
        if modes - {"onoff", "unknown"}:
            result.extend(
                service_action(entity, "turn_on", f"{v}%", brightness_pct=v) for v in (25, 50, 100)
            )
        return result
    if domain == "cover":
        return [
            service_action(entity, service, label)
            for bit, service, label in (
                (1, "open_cover", "Open"),
                (8, "stop_cover", "Stop"),
                (2, "close_cover", "Close"),
            )
            if features & bit
        ]
    if domain in {"select", "input_select"}:
        return [
            service_action(entity, "select_option", str(option), option=option)
            for option in attrs.get("options", [])
        ]
    if domain == "media_player":
        result = []
        if (entity.state == "playing" and features & 1) or (
            entity.state != "playing" and features & 16384
        ):
            result.append(
                service_action(
                    entity, "media_play_pause", "Pause" if entity.state == "playing" else "Play"
                )
            )
        if features & 4:
            try:
                volume = float(attrs.get("volume_level", 0))
            except (ValueError, TypeError):
                volume = 0
            if math.isfinite(volume):
                result.extend(
                    service_action(
                        entity, "volume_set", label, volume_level=max(0, min(1, volume + delta))
                    )
                    for label, delta in (("Vol -", -0.1), ("Vol +", 0.1))
                )
        return result
    action = default_action(entity)
    return [action] if action.service else []
