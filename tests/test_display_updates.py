"""Regressions for incremental drawing, rich cards, and monotonic wake behavior."""

import asyncio
from dataclasses import replace

from tjc_display.history import Sample, compact, decode_history
from tjc_display.icons import atlas, icon_commands, masks
from tjc_display.layout import FULL_BODY, sheets
from tjc_display.model import Entity, Item, Page, parse_dashboard
from tjc_display.render import render
from tjc_hmi.controller import Controller
from tjc_hmi.model import Entity as ControllerEntity
from tjc_hmi.model import Item as ControllerItem
from tjc_hmi.model import Page as ControllerPage

from tjc_bridge.config import Settings
from tjc_bridge.idle import IdleBacklight


def test_rich_cards_have_dedicated_full_body_sheets_in_dashboard_order():
    items = tuple(
        Item(f"sensor.item{i}", kind=kind)
        for i, kind in enumerate(
            ["tile", "gauge", "bar", "history-graph", "statistics-graph", "tile"]
        )
    )
    packed = sheets(items)
    assert [[entry[0] for entry in page] for page in packed] == [[i] for i in range(6)]
    for index in range(1, 5):
        assert packed[index][0][2:] == FULL_BODY
        screen = render(
            [Page("tjc/home", "TJC1", items)],
            {item.entity_id: Entity(item.entity_id, "50") for item in items},
            sheet=index,
            dashboard_count=2,
        )
        assert "fill 0,39,480,189,6371" in screen.commands
        assert any(region.event == "sheet" and region.y == 228 for region in screen.regions)
    assert [len(page) for page in sheets(tuple(Item(f"sensor.t{i}") for i in range(7)))] == [6, 1]


def test_sensor_status_is_one_command_and_toggle_only_changes_its_tile():
    entities = {
        "sensor.temp": Entity("sensor.temp", "21"),
        "switch.desk": Entity("switch.desk", "off"),
    }
    pages = [Page("tjc/home", "TJC1", tuple(Item(eid) for eid in entities))]
    before = render(pages, entities)
    entities["sensor.temp"].state = "22"
    after = render(pages, entities)
    delta = after.changed_commands(before)
    assert len(delta) == 1 and delta[0].startswith("xstr 8,70,228,26,")
    assert '"22"' in delta[0]
    entities["switch.desk"].state = "on"
    updated = render(pages, entities)
    delta = updated.changed_commands(after)
    assert delta and not any(command.startswith("cls") for command in delta)
    assert not any("sensor.temp" in command or "TJC1" in command for command in delta)
    assert (
        render(pages, entities, detail="switch.desk").changed_commands(updated)[0].startswith("cls")
    )


def test_icons_badges_charts_and_visibility():
    pages, warnings = parse_dashboard(
        "tjc",
        "TJC1",
        {
            "views": [
                {
                    "badges": [
                        "sensor.temp",
                        {"type": "entity", "entity": "switch.desk", "icon": "mdi:fan"},
                        {"entity": "lock.secret", "visibility": []},
                    ],
                    "cards": [
                        {"type": "gauge", "entity": "sensor.temp", "min": -10, "max": 40},
                        {"type": "custom:bar-card", "entities": [None, "sensor.level"], "max": 100},
                        {
                            "type": "history-graph",
                            "entities": ["sensor.temp", {"entity": "sensor.outside"}],
                        },
                        {
                            "type": "statistics-graph",
                            "entities": ["sensor.power"],
                            "chart_type": "bar",
                            "stat_types": ["max"],
                        },
                    ],
                }
            ]
        },
        "mdi:home",
    )
    page = pages[0]
    assert page.icon == "mdi:home" and len(page.badges) == 2
    assert len(warnings) == 1 and "visibility" in warnings[0]
    assert [item.kind for item in page.items] == [
        "gauge",
        "bar",
        "history-graph",
        "statistics-graph",
    ]
    assert page.items[2].series == ("sensor.temp", "sensor.outside")
    assert page.items[3].statistic == "max" and page.items[3].chart_type == "bar"
    entities = {eid: Entity(eid, "20") for eid in ("sensor.temp", "sensor.level", "switch.desk")}
    first = render(pages, entities)
    assert len(masks()) == 7448 and masks()["fan"]
    entities["sensor.temp"].state = "23"
    second = render(pages, entities)
    delta = second.changed_commands(first)
    assert delta and not any(command.startswith("cls") for command in delta)
    assert sum(command.startswith("fill ") for command in first.commands) > 80  # thick dial


def test_downsampling_extrema_gaps_and_compressed_history():
    points = [
        Sample(float(index), None if index == 500 else (999 if index == 200 else index % 12))
        for index in range(1000)
    ]
    reduced = compact(points)
    assert len(reduced) <= 96 and reduced[0] == points[0] and reduced[-1] == points[-1]
    assert any(point.value == 999 for point in reduced)
    assert any(point.value is None for point in reduced)
    history = decode_history(
        {
            "sensor.temp": [
                {"s": "21", "lu": 10},
                {"s": "unavailable", "lu": 15},
                {"s": "nan", "lu": 17},
                {"s": "22", "lu": 20},
            ]
        },
        0,
        30,
    )
    assert history.series["sensor.temp"][-1] == Sample(30, 22)
    assert history.series["sensor.temp"][1].value is None
    stats = decode_history({"sensor.power": [{"start": 10, "max": 9}]}, 0, 30, statistic="max")
    assert stats.series["sensor.power"] == (Sample(10, 9),)


def test_native_icons_one_command_with_palette_and_legacy_fallback():
    pack = atlas()
    assert len(pack["names"]) == 7448 and len(set(pack["names"])) == 7448
    for name in ("lightbulb", "weather-partly-cloudy", "thermometer", "view-dashboard"):
        legacy = icon_commands(name, 13, 44, 65535)
        native = icon_commands(name, 13, 44, 65535, native=True)
        assert len(native) == 1 and native[0].startswith("xpic 13,44,24,24,")
        assert len(legacy) > len(native)
        assert native != icon_commands(name, 13, 44, 65535, background=14827, native=True)
    assert icon_commands("mdi:no-such-icon", 0, 0, 1407, background=2145, native=True)[
        0
    ].startswith("xpic")
    # Unsupported colors are still drawn correctly using masks.
    assert icon_commands("home", 0, 0, 1234, native=True)[0].startswith("fill")


class Panel:
    connected = True

    def __init__(self):
        self.draws = []
        self.levels = []

    async def draw(self, commands):
        self.draws.append(commands)

    async def brightness(self, value):
        self.levels.append(value)


async def test_idle_defaults_wake_gesture_and_reconnect():
    now = [0.0]
    panel = Panel()
    settings = Settings(brightness=80)
    idle = IdleBacklight(panel, settings, clock=lambda: now[0])
    task = asyncio.create_task(idle.run())
    try:
        await asyncio.sleep(0)
        assert panel.levels == [80]
        now[0] = 60
        idle._changed.set()
        await asyncio.sleep(0.01)
        assert panel.levels[-1] == 40
        now[0] = 120
        idle._changed.set()
        await asyncio.sleep(0.01)
        assert panel.levels[-1] == 0
        assert idle.touch(True)
        await asyncio.sleep(0.01)
        assert panel.levels[-1] == 80 and idle.touch(False)
        assert not idle.touch(True) and not idle.touch(False)
        now[0] += 120
        idle.connected()
        await asyncio.sleep(0.01)
        assert idle.target() == 80
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    disabled = IdleBacklight(panel, replace(settings, idle_enabled=False), clock=lambda: now[0])
    now[0] += 100000
    assert disabled.target() == 80


async def test_unchanged_control_pixels_still_update_action_targets():
    # The same '+' label needs a new service target after a HA limit change.
    entity = ControllerEntity("number.level", "5", {"min": 0, "max": 10, "step": 1})
    panel = Panel()

    async def service(action):
        pass

    controller = Controller(panel, service, lambda eid: entity)
    controller.update_pages(
        [ControllerPage("tjc/home", "TJC1", (ControllerItem(entity.entity_id),))]
    )
    controller.detail = entity.entity_id
    controller.start()
    try:
        await asyncio.sleep(0.12)
        before = list(panel.draws)
        entity.attributes["step"] = 2
        controller.invalidate()
        await asyncio.sleep(0.12)
        values = [
            region.action.data.get("value")
            for region in controller._screen.regions
            if region.action
        ]
        assert 7 in values and 3 in values
        assert panel.draws == before  # The +/- labels did not change.
    finally:
        await controller.stop()


def test_ha_gauge_default_fill_and_optional_segments():
    config = {"views": [{"cards": [{"type": "gauge", "entity": "sensor.cpu"}]}]}
    pages, _ = parse_dashboard("tjc", "TJC1", config)
    assert not pages[0].items[0].needle
    entities = {"sensor.cpu": Entity("sensor.cpu", "60", {"unit_of_measurement": "%"})}
    filled = render(pages, entities)
    assert not any(c.startswith("line ") for c in filled.commands)
    config["views"][0]["cards"][0].update(
        needle=True,
        attribute="load",
        unit="W",
        segments=[
            {"from": 0, "color": "#00ff00", "label": "Normal"},
            {"from": 50, "color": "#ff0000", "label": "High"},
        ],
    )
    entities["sensor.cpu"].attributes["load"] = 75
    pages, _ = parse_dashboard("tjc", "TJC1", config)
    needle = render(pages, entities)
    assert any(c.startswith("line ") for c in needle.commands)
    assert any('"High"' in c for c in needle.commands)
    assert any(c.endswith(",63488") for c in needle.commands)  # RGB565 red segment
