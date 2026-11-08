"""Compare serial work for the same representative six-tile screen, without a device."""

import json

from core_loader import load_core

ROOT = load_core()

from tjc_hmi.icons import atlas, icon_commands  # noqa: E402
from tjc_hmi.model import Entity, Item, Page  # noqa: E402
from tjc_hmi.protocol import encode  # noqa: E402
from tjc_hmi.render import render  # noqa: E402


def main():
    names = [
        "weather-partly-cloudy",
        "lightbulb-outline",
        "thermometer",
        "water-percent",
        "home",
        "power-socket-eu",
    ]
    items = tuple(
        Item(f"sensor.example{index}", name=f"Example {index + 1}", icon="mdi:" + name)
        for index, name in enumerate(names)
    )
    pages = [Page("tjc/home", "Home", items)]
    entities = {item.entity_id: Entity(item.entity_id, "20") for item in items}
    report = {"native_icon_count": len(atlas()["names"]), "physical_timing_measured": False}
    for native in (False, True):
        commands = render(pages, entities, native_icons=native).commands
        chunks, size, count = 0, 0, 0
        for command in commands:
            length = len(encode(command))
            if count and (count == 8 or size + length > 256):
                chunks, size, count = chunks + 1, 0, 0
            size, count = size + length, count + 1
        report["native" if native else "vector"] = {
            "commands": len(commands),
            "serial_bytes": sum(len(encode(command)) for command in commands),
            "stop_and_wait_round_trips": len(commands),
            "bounded_batch_round_trips": chunks + bool(count),
        }
    report["per_icon_commands"] = {
        name: {
            "vector": len(icon_commands(name, 0, 0, 65535)),
            "native": len(icon_commands(name, 0, 0, 65535, native=True)),
        }
        for name in names
    }
    (ROOT / "docs/ICON-PERFORMANCE.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
