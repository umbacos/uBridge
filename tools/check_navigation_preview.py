"""Render the single-dashboard and idle layouts in an isolated mock browser preview."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright
from tjc_display.model import Entity, parse_dashboard
from tjc_display.render import render
from tjc_display.weather import Forecast

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = yaml.safe_load(
        (ROOT / "examples/dashboards/clock-weather.yaml").read_text(encoding="utf-8")
    )
    pages, _ = parse_dashboard("clock-weather", "Clock and weather", config)
    entities = {
        "weather.forecast_home": Entity(
            "weather.forecast_home",
            "partlycloudy",
            {"temperature": 18, "temperature_unit": "C", "humidity": 64},
        ),
        "light.living_room": Entity(
            "light.living_room", "on", {"friendly_name": "Living room", "brightness": 128}
        ),
        "sensor.room_temperature": Entity(
            "sensor.room_temperature",
            "21.4",
            {"friendly_name": "Temperature", "unit_of_measurement": "C"},
        ),
    }
    forecasts = {
        ("weather.forecast_home", "daily"): Forecast(
            (
                {
                    "datetime": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "condition": "sunny",
                    "temperature": 19,
                    "templow": 10,
                },
            )
        )
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for idle in (False, True):
            screen = render(
                pages,
                entities,
                dashboard_count=3 if idle else 1,
                idle_dashboard=idle,
                forecasts=forecasts,
                time_zone="Europe/Rome",
            )
            assert not any(r.event in {"previous", "next", "sheet"} for r in screen.regions)
            page = browser.new_page(viewport={"width": 1200, "height": 1080})

            def route(route, request, *, commands=screen.commands):
                if request.url.endswith("/api/screen"):
                    route.fulfill(
                        content_type="application/json",
                        body=json.dumps({"commands": commands, "history": []}),
                    )
                else:
                    route.fulfill(
                        content_type="text/html",
                        body=(ROOT / "tools/preview.html").read_text(encoding="utf-8"),
                    )

            page.route("http://ubridge.preview/**", route)
            page.goto("http://ubridge.preview/")
            page.wait_for_function(
                "document.querySelector('#status').textContent==='Preview connected'"
            )
            page.locator("canvas").screenshot(
                path=str(
                    ROOT
                    / "docs"
                    / ("preview-idle-dashboard.png" if idle else "preview-single-dashboard.png")
                )
            )
            page.close()
        browser.close()
    print("PASS: single-dashboard and idle mock browser previews without navigation/counter")


if __name__ == "__main__":
    main()
