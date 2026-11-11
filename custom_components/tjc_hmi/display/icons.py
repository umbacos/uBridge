"""Offline MDI masks rendered with standard serial drawing instructions."""

import gzip
import json
from functools import cache
from pathlib import Path

from .model import Entity


@cache
def masks():
    return json.loads(
        gzip.decompress((Path(__file__).parent / "assets/mdi24.json.gz").read_bytes())
    )


@cache
def atlas():
    data = json.loads((Path(__file__).parent / "assets/icon-atlas.json").read_text())
    data["positions"] = {name: index for index, name in enumerate(data["names"])}
    return data


def icon_commands(
    name: str, x: int, y: int, color: int, *, background=6371, native=False, size=24
) -> list[str]:
    name = str(name).removeprefix("mdi:")
    if name not in masks():
        name = "help-circle-outline"
    if (
        size == 24
        and native
        and name in atlas()["positions"]
        and [color, background] in atlas()["palettes"]
    ):
        pack = atlas()
        index = pack["positions"][name]
        sheet, slot = divmod(index, pack["per_sheet"])
        palette = pack["palettes"].index([color, background])
        picture_id = palette * pack["sheets_per_palette"] + sheet
        sx, sy = slot % pack["columns"] * 24, slot // pack["columns"] * 24
        return [f"xpic {x},{y},24,24,{sx},{sy},{picture_id}"]
    shapes = masks()[name]
    if size == 24:
        return [f"fill {x + dx},{y + dy},{w},{h},{color}" for dx, dy, w, h in shapes]
    commands = []
    for dx, dy, w, h in shapes:
        left, top = dx * size // 24, dy * size // 24
        right, bottom = (dx + w) * size // 24, (dy + h) * size // 24
        commands.append(f"fill {x + left},{y + top},{right - left},{bottom - top},{color}")
    return commands


def entity_icon(entity: Entity, override: str | None = None) -> str:
    if override or entity.attributes.get("icon"):
        return override or entity.attributes["icon"]
    device = entity.attributes.get("device_class")
    if device in {"temperature", "humidity", "battery", "power", "energy", "illuminance"}:
        return {
            "temperature": "thermometer",
            "humidity": "water-percent",
            "battery": "battery",
            "power": "flash",
            "energy": "lightning-bolt",
            "illuminance": "brightness-6",
        }[device]
    if entity.domain == "light":
        return "lightbulb" if entity.state == "on" else "lightbulb-outline"
    return {
        "switch": "power-socket-eu",
        "sensor": "eye",
        "binary_sensor": "checkbox-marked-circle",
        "climate": "thermostat",
        "cover": "blinds",
        "fan": "fan",
        "lock": "lock",
        "media_player": "speaker",
        "scene": "palette",
        "script": "script-text",
        "input_boolean": "toggle-switch",
        "input_number": "tune",
        "input_select": "format-list-bulleted",
        "button": "gesture-tap-button",
        "input_button": "gesture-tap-button",
        "person": "account",
        "weather": "weather-partly-cloudy",
        "sun": "white-balance-sunny",
    }.get(entity.domain, "help-circle-outline")
