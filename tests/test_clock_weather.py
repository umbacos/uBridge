"""Clock timing, forecast rendering and the combined clock/weather/two-tile layout."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from tjc_display.cards import local_time
from tjc_display.controller import Controller
from tjc_display.layout import FULL_BODY, sheets
from tjc_display.model import Entity, parse_dashboard
from tjc_display.protocol import Frame
from tjc_display.render import render
from tjc_display.weather import Forecast, decode_forecast


def dashboard(cards):
    pages, warnings = parse_dashboard("home-panel", "Home", {"views": [{"cards": cards}]})
    assert not warnings
    return pages


WEATHER = Entity(
    "weather.home",
    "sunny",
    {"friendly_name": "Home weather", "temperature": 18.5, "temperature_unit": "C", "humidity": 64},
)
FORECAST = Forecast(
    tuple(
        {
            "datetime": f"2026-11-{8 + i:02d}T12:00:00+00:00",
            "condition": "rainy",
            "temperature": 19 + i,
            "templow": 10 + i,
        }
        for i in range(5)
    )
)
NOW = datetime(2026, 11, 8, 10, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize("analog", [False, True])
def test_four_card_view_fits_one_sheet_and_keeps_entity_hit_regions(analog):
    pages = dashboard(
        [
            {
                "type": "clock",
                "clock_size": "large",
                "clock_style": "analog" if analog else "digital",
                "show_seconds": True,
            },
            {"type": "weather-forecast", "entity": "weather.home", "forecast_type": "daily"},
            {"type": "tile", "entity": "switch.room"},
            {"type": "tile", "entity": "sensor.temp"},
        ]
    )
    assert len(sheets(pages[0].items)) == 1
    entities = {
        "weather.home": WEATHER,
        "switch.room": Entity("switch.room", "off"),
        "sensor.temp": Entity("sensor.temp", "21", {"unit_of_measurement": "C"}),
    }
    screen = render(pages, entities, now=NOW, forecasts={("weather.home", "daily"): FORECAST})
    assert not any(region.event in {"previous", "next", "sheet"} for region in screen.regions)
    assert any(
        region.entity_id == "switch.room" and region.event == "action" for region in screen.regions
    )
    assert any(
        region.entity_id == "sensor.temp" and region.event == "detail" for region in screen.regions
    )
    assert any("Next 19/10C" in command for command in screen.commands)
    for key, commands in screen.blocks.items():
        if not key.startswith("item"):
            continue
        for command in commands:
            op, args = command.split(" ", 1)
            if op in {"fill", "xstr"}:
                x, y, w, h = map(int, args.split(",")[:4])
                assert 0 <= x < x + w <= 480
                assert 39 <= y < y + h <= 272
            elif op in {"cir", "cirs"}:
                x, y, radius, _ = map(int, args.split(","))
                assert 39 <= y - radius <= y + radius < 272


@pytest.mark.parametrize("analog", [False, True])
def test_clock_tick_only_updates_clock_blocks(analog):
    pages = dashboard(
        [{"type": "clock", "clock_style": "analog" if analog else "digital", "show_seconds": True}]
    )
    first = render(pages, {}, now=NOW)
    next_screen = render(pages, {}, now=NOW + timedelta(seconds=1))
    delta = next_screen.changed_commands(first)
    assert delta and len(delta) <= 6
    assert not any(
        command.startswith("cls") or "Page >" in command or "Items" in command for command in delta
    )
    assert sheets(pages[0].items)[0][0][2:] == FULL_BODY


def test_clock_without_seconds_does_not_send_commands_until_minute_changes():
    pages = dashboard([{"type": "clock", "time_format": "12", "time_zone": "Europe/Rome"}])
    first = render(pages, {}, now=NOW)
    assert render(pages, {}, now=NOW + timedelta(seconds=1)).changed_commands(first) == []
    assert render(pages, {}, now=NOW + timedelta(minutes=1)).changed_commands(first)
    before, invalid = local_time(datetime(2026, 3, 29, 0, 30, tzinfo=UTC), "Europe/Rome")
    after, _ = local_time(datetime(2026, 3, 29, 1, 30, tzinfo=UTC), "Europe/Rome")
    assert (before.hour, after.hour) == (1, 3) and not invalid
    assert local_time(NOW, "Missing/Zone")[1]
    assert any(
        "showing UTC" in command
        for command in render(
            dashboard([{"type": "clock", "time_zone": "Missing/Zone"}]), {}, now=NOW
        ).commands
    )


def test_weather_forecast_options_missing_data_and_hidden_cards():
    current_pages = dashboard([{"type": "weather-forecast", "entity": "weather.home"}])
    first = render(
        current_pages, {"weather.home": WEATHER}, forecasts={("weather.home", "daily"): FORECAST}
    )
    rainy = Entity("weather.home", "rainy", WEATHER.attributes)
    changed = render(
        current_pages, {"weather.home": rainy}, forecasts={("weather.home", "daily"): FORECAST}
    )
    delta = changed.changed_commands(first)
    assert delta and not any(c.startswith("cls") or c == "fill 0,39,480,189,6371" for c in delta)
    assert not any(
        "-digit" in key and first.blocks[key] != changed.blocks[key] for key in first.blocks
    )
    pages = dashboard(
        [
            {
                "type": "weather-forecast",
                "entity": "weather.home",
                "show_current": False,
                "forecast_type": "hourly",
            }
        ]
    )
    screen = render(
        pages,
        {"weather.home": WEATHER},
        forecasts={("weather.home", "hourly"): FORECAST},
        time_zone="Europe/Rome",
    )
    assert any('"13:00"' in command for command in screen.commands)
    assert not any("Sunny" in command for command in screen.commands)
    assert any("19/10" in command for command in screen.commands)
    error = render(
        pages,
        {"weather.home": WEATHER},
        forecasts={("weather.home", "hourly"): Forecast(error="Forecast unavailable")},
    )
    assert any("Forecast unavailable" in command for command in error.commands)
    unavailable = render(pages, {})
    assert any("Weather unavailable" in command for command in unavailable.commands)
    hidden, warnings = parse_dashboard(
        "a",
        "A",
        {
            "views": [
                {
                    "cards": [
                        {"type": "clock", "visibility": []},
                        {"type": "weather-forecast", "entity": "sensor.temp"},
                    ]
                }
            ]
        },
    )
    assert not hidden[0].items and len(warnings) == 2
    assert decode_forecast(
        {"weather.home": {"forecast": [None, FORECAST.rows[0]]}}, "weather.home"
    ).rows == (FORECAST.rows[0],)
    assert decode_forecast(None, "weather.home").error


async def test_visible_clock_ticks_without_entity_state_events():
    class Panel:
        connected = True

        def __init__(self):
            self.draws = []

        async def draw(self, commands):
            self.draws.append(commands)

    panel = Panel()

    async def service(action):
        raise AssertionError("Clock must not call services")

    controller = Controller(panel, service, lambda eid: None)
    controller.update_pages(dashboard([{"type": "clock", "show_seconds": True}]))
    assert controller.entity_ids == set()
    controller.start()
    try:
        async with asyncio.timeout(3):
            while len(panel.draws) < 2:  # noqa: ASYNC110 - observing autonomous clock ticks
                await asyncio.sleep(0.02)
        assert not any(command.startswith("cls") for command in panel.draws[1])
    finally:
        await controller.stop()


async def test_clock_tick_does_not_cancel_an_entity_touch_in_progress():
    class Panel:
        connected = True

        def __init__(self):
            self.draws = 0
            self.first = asyncio.Event()
            self.tick = asyncio.Event()
            self.release = asyncio.Event()

        async def draw(self, commands):
            self.draws += 1
            if self.draws == 1:
                self.first.set()
            else:
                self.tick.set()
                await self.release.wait()

    panel = Panel()
    calls = []

    async def service(action):
        calls.append(action)

    entities = {"weather.home": WEATHER, "switch.room": Entity("switch.room", "off")}
    controller = Controller(panel, service, entities.get)
    controller.update_pages(
        dashboard(
            [
                {"type": "clock", "show_seconds": True},
                {"type": "weather-forecast", "entity": "weather.home", "show_forecast": False},
                {"type": "tile", "entity": "switch.room"},
            ]
        )
    )
    controller.start()
    try:
        await asyncio.wait_for(panel.first.wait(), 1)
        controller.frame(Frame(b"\x67\x00\x20\x00\xeb\x01"))
        assert controller._pressed
        await asyncio.wait_for(panel.tick.wait(), 2)
        assert controller._passive_drawing and controller._pressed
        controller.frame(Frame(b"\x67\x00\x20\x00\xeb\x00"))
        await asyncio.sleep(0.05)
        assert len(calls) == 1 and calls[0].service == "toggle"
    finally:
        panel.release.set()
        await controller.stop()
