"""Exercise the integration with real HA classes and mock serial in an isolated directory.

Run on Linux with Home Assistant 2026.10.4 installed. Does not connect to a
personal HA instance, open a physical serial port, or operate real devices.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader  # noqa: E402
from homeassistant.components.lovelace import LovelaceData  # noqa: E402
from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: E402
from homeassistant.components.lovelace.dashboard import LovelaceYAML  # noqa: E402
from homeassistant.config_entries import ConfigEntries  # noqa: E402
from homeassistant.core import HomeAssistant, SupportsResponse  # noqa: E402

from custom_components.tjc_hmi import CONFIG_SCHEMA, Runtime  # noqa: E402
from custom_components.tjc_hmi.config_flow import ConfigFlow, schema  # noqa: E402
from custom_components.tjc_hmi.dashboards import dashboard_choices  # noqa: E402
from custom_components.tjc_hmi.display.model import Entity  # noqa: E402
from custom_components.tjc_hmi.display.protocol import Frame  # noqa: E402


class MockPanel:
    connected = True

    def __init__(self):
        self.frames = []
        self.levels = []

    def start(self):
        self.connected = True

    async def brightness(self, value):
        self.levels.append(value)

    async def stop(self):
        self.connected = False

    async def draw(self, commands):
        self.frames.append(commands)


async def main():
    with tempfile.TemporaryDirectory(prefix="tjc-ha-check-") as config_dir:
        hass = HomeAssistant(config_dir)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        hass.config.time_zone = "Europe/Rome"
        settings = CONFIG_SCHEMA({"tjc_hmi": {"port": "mock://panel"}})["tjc_hmi"]
        dashboards = {}
        for number in (10, 2, 1):
            filename = f"tjc{number}.yaml"
            await asyncio.to_thread(
                Path(config_dir, filename).write_text,
                "views:\n  - path: home\n    cards:\n      - type: tile\n"
                f"        entity: switch.room{number}\n",
            )
            dashboards[f"tjc-{number}"] = LovelaceYAML(
                hass,
                f"tjc-{number}",
                {
                    "title": {1: "Home", 2: "Climate", 10: "Outside"}[number],
                    "mode": "yaml",
                    "filename": filename,
                },
            )
        hass.data[LOVELACE_DATA] = LovelaceData("storage", dashboards, None, {})
        settings["dashboards"] = ["tjc-1", "tjc-2", "tjc-10"]
        assert schema(settings, dashboard_choices(hass))(settings)["baudrate"] == 115200
        flow = ConfigFlow()
        flow.hass = hass
        flow.handler = "tjc_hmi"
        flow.flow_id = "isolated-check"
        flow.context = {"source": "user"}
        invalid = await flow.async_step_user({**settings, "port": " "})
        assert invalid["errors"] == {"base": "invalid_settings"}
        result = await flow.async_step_user({**settings, "port": " mock://panel "})
        assert result["type"] == "create_entry" and result["data"]["port"] == "mock://panel"
        assert (
            schema(
                {**settings, "idle_mode": "dashboard", "idle_dashboard": "tjc-10"},
                dashboard_choices(hass),
            )({**settings, "idle_mode": "dashboard", "idle_dashboard": "tjc-10"})["idle_dashboard"]
            == "tjc-10"
        )
        from custom_components.tjc_hmi.config_flow import valid_input

        assert not valid_input(
            {**settings, "idle_mode": "dashboard", "idle_dashboard": "missing"},
            dashboard_choices(hass),
        )
        from types import SimpleNamespace
        from unittest.mock import patch

        from custom_components.tjc_hmi import async_migrate_entry

        legacy = SimpleNamespace(
            version=2, data={"port": "old", "dashboard_prefix": "Panel"}, options={}
        )
        original = dashboards["tjc-1"].config["title"]
        dashboards["tjc-1"].config["title"] = "Panel7"
        with patch.object(hass.config_entries, "async_update_entry") as update:
            assert await async_migrate_entry(hass, legacy)
            assert update.call_args.kwargs["data"]["dashboards"] == ["tjc-1"]
            assert "dashboard_prefix" not in update.call_args.kwargs["data"]
            assert update.call_args.kwargs["version"] == 3
        dashboards["tjc-1"].config["title"] = original
        # Native HA storage/dashboard config and event bus are used; serial is mocked.
        runtime = Runtime(hass, settings)
        panel = MockPanel()
        runtime.panel = runtime.controller.panel = runtime.idle.panel = panel
        calls = []

        async def toggle(call):
            calls.append(dict(call.data))
            entity_id = call.data["entity_id"]
            if isinstance(entity_id, list):
                entity_id = entity_id[0]
            hass.states.async_set(entity_id, "on")

        hass.services.async_register("switch", "toggle", toggle)
        for number in (1, 2, 10):
            hass.states.async_set(f"switch.room{number}", "off")
        try:
            await runtime._started_callback(hass)
            await asyncio.sleep(0.15)
            assert [p.title for p in runtime.controller.pages] == ["Home", "Climate", "Outside"]
            assert panel.frames and "Home" in " ".join(panel.frames[-1])
            runtime.controller.frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            runtime.controller.frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await asyncio.sleep(0.2)
            assert calls and calls[0]["entity_id"] == "switch.room1"
            assert runtime._state("switch.room1") == Entity("switch.room1", "on", {})
            hass.states.async_set("switch.room1", "unavailable")
            await asyncio.sleep(0.15)
            assert "unavailable" in " ".join(panel.frames[-1])
            dashboards.pop("tjc-2")
            await runtime.reload(force=True)
            assert [p.title for p in runtime.controller.pages] == ["Home", "Outside"]
            assert runtime._idle_task and panel.levels == [80]
            now = [0.0]
            runtime.idle.clock = lambda: now[0]
            runtime.idle.connected()
            await asyncio.sleep(0.02)
            now[0] = 60
            runtime.idle._changed.set()
            await asyncio.sleep(0.02)
            assert panel.levels[-1] == 40
            now[0] = 120
            runtime.idle._changed.set()
            await asyncio.sleep(0.02)
            assert panel.levels[-1] == 0
            before = len(calls)
            runtime._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            runtime._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await asyncio.sleep(0.05)
            assert panel.levels[-1] == 80 and len(calls) == before
            # Same dashboard idle policy in direct HA mode, at absolute brightness.
            runtime.settings.update(idle_mode="dashboard", idle_dashboard="tjc-10")
            runtime.idle.settings.idle_mode = "dashboard"
            runtime.idle.settings.idle_dashboard = "tjc-10"
            runtime.idle.settings.idle_brightness = 25
            await runtime.reload()
            now[0] += 60
            runtime.idle._changed.set()
            await asyncio.sleep(0.15)
            assert [p.title for p in runtime.controller.pages] == ["Outside"]
            assert panel.levels[-1] == 25
            assert not any(
                r.event in {"previous", "next", "sheet"} for r in runtime.controller._screen.regions
            )
            assert not any(
                c.startswith("xstr 430,5,42,30,")
                for c in runtime.controller._screen.blocks["header"]
            )
            await runtime.reload()
            assert [p.title for p in runtime.controller.pages] == ["Outside"]
            runtime._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            runtime._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await asyncio.sleep(0.15)
            assert runtime.controller.pages[runtime.controller.page].title == "Home"
            assert panel.levels[-1] == 80 and len(calls) == before
            # Forecast service responses are independent of weather state attributes.
            forecast_calls = []

            async def forecast_service(call):
                forecast_calls.append(dict(call.data))
                return {
                    "weather.home": {
                        "forecast": [
                            {
                                "datetime": "2026-11-08T12:00:00+00:00",
                                "condition": "sunny",
                                "temperature": 19,
                                "templow": 10,
                            }
                        ]
                    }
                }

            hass.services.async_register(
                "weather",
                "get_forecasts",
                forecast_service,
                supports_response=SupportsResponse.ONLY,
            )
            hass.states.async_set(
                "weather.home",
                "sunny",
                {"temperature": 18, "temperature_unit": "C", "humidity": 64},
            )
            await asyncio.to_thread(
                Path(config_dir, "tjc1.yaml").write_text,
                "views:\n  - path: home\n    cards:\n"
                "      - type: clock\n        show_seconds: true\n"
                "      - type: weather-forecast\n        entity: weather.home\n"
                "      - type: tile\n        entity: switch.room1\n"
                "      - type: tile\n        entity: sensor.temp\n",
            )
            runtime.settings.update(dashboards=["tjc-1"], idle_mode="dim")
            await runtime.reload(force=True)
            async with asyncio.timeout(3):
                while not forecast_calls or not runtime.controller.forecasts:  # noqa: ASYNC110
                    await asyncio.sleep(0.02)
            await asyncio.sleep(0.15)
            assert forecast_calls[0]["type"] == "daily"
            assert runtime.controller.time_zone == "Europe/Rome"
            assert any("Next 19/10C" in c for draw in panel.frames for c in draw)
            assert runtime.controller.dashboard_count == 1
            assert not any(
                r.event in {"previous", "next", "sheet"} for r in runtime.controller._screen.regions
            )
            print("PASS: direct HA clock/weather/two-tile page and forecast response service")
            from unittest.mock import patch

            def fake_upload(*args):
                assert not panel.connected
                raise RuntimeError("simulated upload failure")

            with patch("custom_components.tjc_hmi.upload", fake_upload):
                try:
                    await runtime.flash("/config/test.tft")
                except Exception as err:
                    assert "simulated upload failure" in str(err)
                else:
                    raise AssertionError("Expected upload failure")
            assert panel.connected and not runtime._flash_task
            print("PASS: direct idle/wake; failed upload releases/reconnects UART")
            print(
                "Home Assistant 2026.10.4 passed: config flow, schemas, YAML dashboards, ordering, "
                "real service dispatch, state events, dashboard removal, cleanup"
            )
        finally:
            await runtime.stop()
            await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
