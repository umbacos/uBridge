"""Run the packaged executable against isolated HA and UART wire simulators."""

import argparse
import asyncio
import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
END = b"\xff\xff\xff"


async def eventually(predicate):
    async with asyncio.timeout(30):
        while not predicate():  # noqa: ASYNC110 - external process observation
            await asyncio.sleep(0.02)


async def check(executable):
    spec = importlib.util.spec_from_file_location("tjc_wire_fixture", ROOT / "tests/test_bridge.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    commands, writers = [], []

    async def uart(reader, writer):
        writers.append(writer)
        try:
            while raw := await reader.readuntil(END):
                command = raw[:-3].decode("ascii")
                commands.append(command)
                if command == "get sys2":
                    writer.write(b"\x71" + (1414152961).to_bytes(4, "little") + END)
                elif command == "get sys1":
                    from tjc_display.icons import atlas

                    writer.write(b"\x71" + atlas()["signature"].to_bytes(4, "little") + END)
                else:
                    writer.write(b"\x01" + END)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(uart, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    work = ROOT / ".cache/binary-check"
    work.mkdir(parents=True, exist_ok=True)
    config = work / "config.json"
    async with fixture.ha_server() as (ha, url):
        settings = {
            "base_url": url,
            "serial_port": f"socket://127.0.0.1:{port}",
            "baudrate": 115200,
            "dashboards": ["tjc-1", "tjc-2", "tjc-10"],
            "remember_token": False,
            "idle_dim_seconds": 1,
            "idle_off_seconds": 1,
        }
        await asyncio.to_thread(config.write_text, json.dumps(settings), encoding="utf-8")
        environment = {**os.environ, "TJC_HA_TOKEN": "test-token"}
        process = await asyncio.create_subprocess_exec(
            str(executable), "--headless", "--config", str(config), env=environment, cwd=ROOT
        )
        try:
            await eventually(
                lambda: any(
                    '"off"' in command and command.startswith("xstr ") for command in commands
                )
            )
            assert any('"Home"' in command for command in commands)
            await eventually(lambda: any('"Page >"' in command for command in commands))
            await eventually(lambda: any(command.startswith("xpic ") for command in commands))
            await asyncio.sleep(0.1)
            print(
                "PASS: packaged binary authenticates and renders through the UART protocol",
                flush=True,
            )
            writer = next(writer for writer in reversed(writers) if not writer.is_closing())
            action_start = len(commands)
            writer.write(b"\x67\x00\x20\x00\x40\x01" + END + b"\x67\x00\x20\x00\x40\x00" + END)
            await writer.drain()
            await eventually(lambda: any('"on"' in command for command in commands))

            def calls():
                return [call for call in ha.calls if call["type"] == "call_service"]

            assert len(calls()) == 1 and calls()[0]["target"] == {"entity_id": "switch.room"}
            assert not any(command.startswith("cls ") for command in commands[action_start:])
            print("PASS: UART touch -> HA service -> state event -> UART redraw", flush=True)
            await eventually(lambda: "dim=40" in commands[action_start:])
            await eventually(lambda: "dim=0" in commands[action_start:])
            wake_start = len(commands)
            writer.write(b"\x67\x00\x20\x00\x40\x01" + END + b"\x67\x00\x20\x00\x40\x00" + END)
            await writer.drain()
            await eventually(lambda: "dim=80" in commands[wake_start:])
            await asyncio.sleep(0.1)
            assert len(calls()) == 1
            print(
                "PASS: partial tile update; idle 80 -> 40 -> 0 -> touch wake 80 (no service)",
                flush=True,
            )
            start = len(commands)
            await ha.close_clients()
            await eventually(
                lambda: any(
                    "Home Assistant disconnected" in command for command in commands[start:]
                )
            )
            writer.write(b"\x67\x00\x20\x00\x40\x01" + END + b"\x67\x00\x20\x00\x40\x00" + END)
            await writer.drain()
            await eventually(lambda: len(ha.tokens) >= 2)
            await eventually(lambda: any('"Home"' in command for command in commands[start:]))
            assert len(calls()) == 1
            print(
                "PASS: disconnect suppresses touches; reconnect does not replay the service",
                flush=True,
            )
            ha.config = {
                "views": [
                    {
                        "badges": ["switch.room"],
                        "cards": [
                            {"type": "history-graph", "entities": ["sensor.temp"]},
                            {
                                "type": "statistics-graph",
                                "entities": ["sensor.energy"],
                                "chart_type": "bar",
                                "stat_types": ["change"],
                            },
                        ],
                    }
                ]
            }
            await ha.event("panels_updated", {})
            await eventually(
                lambda: any(
                    call["type"] == "recorder/statistics_during_period" for call in ha.calls
                )
            )
            await eventually(lambda: any(command.startswith("line ") for command in commands))
            print(
                "PASS: native preloaded MDI icons, badges, history and statistics rendering",
                flush=True,
            )
            ha.states["weather.home"] = {
                "entity_id": "weather.home",
                "state": "sunny",
                "attributes": {"temperature": 18, "temperature_unit": "C", "humidity": 64},
            }
            ha.forecasts = {
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
            ha.config = {
                "views": [
                    {
                        "cards": [
                            {"type": "clock", "show_seconds": True, "title": "Home time"},
                            {"type": "weather-forecast", "entity": "weather.home"},
                            {"type": "tile", "entity": "switch.room"},
                            {"type": "tile", "entity": "sensor.temp"},
                        ]
                    }
                ]
            }
            await ha.event(
                "state_changed",
                {"entity_id": "weather.home", "new_state": ha.states["weather.home"]},
            )
            mixed_start = len(commands)
            await ha.event("panels_updated", {})
            await eventually(lambda: any(c.endswith(',"19C"') for c in commands[mixed_start:]))
            assert not any("Items 1/1" in c for c in commands[mixed_start:])
            tick_start = len(commands)
            await eventually(
                lambda: any('s"' in c and c.startswith("xstr") for c in commands[tick_start:])
            )
            assert not any(c.startswith("cls") or "Page >" in c for c in commands[tick_start:])
            print(
                "PASS: combined clock/weather/two-entity page; forecast API; clock-only ticks",
                flush=True,
            )
            # Reconnect the executable in dashboard-idle mode with three configured dashboards.
            # The idle flag must suppress navigation even though the configured count stays three.
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
            settings.update(idle_mode="dashboard", idle_dashboard="tjc-2", idle_brightness=25)
            await asyncio.to_thread(config.write_text, json.dumps(settings), encoding="utf-8")
            idle_restart = len(commands)
            process = await asyncio.create_subprocess_exec(
                str(executable), "--headless", "--config", str(config), env=environment, cwd=ROOT
            )
            await eventually(
                lambda: (
                    any(
                        '"Climate"' in c and c.startswith("xstr 38,5,")
                        for c in commands[idle_restart:]
                    )
                    and "dim=25" in commands[idle_restart:]
                )
            )
            await asyncio.sleep(0.15)
            idle_header = next(
                i
                for i in range(idle_restart, len(commands))
                if '"Climate"' in commands[i] and commands[i].startswith("xstr 38,5,")
            )
            idle_frame = max(
                i for i in range(idle_restart, idle_header) if commands[i].startswith("cls")
            )
            assert not any(
                "Page >" in c or "Items " in c or c.startswith("xstr 430,5,42,30,")
                for c in commands[idle_frame:]
            )
            assert any("Page >" in c for c in commands[idle_restart:idle_frame])
            print(
                "PASS: idle dashboard suppresses navigation/counter "
                "with multiple dashboards configured",
                flush=True,
            )
            report = {
                "verified_at_utc": datetime.now(UTC).isoformat(),
                "executable": executable.name,
                "version": "0.8.0",
                "websocket_authentication": True,
                "serial_handshake": True,
                "touch_service_state_redraw": True,
                "reconnect_without_replay": True,
                "incremental_update_without_cls": True,
                "idle_dim_off_touch_wake": True,
                "mdi_badges_history_statistics": True,
                "native_icon_signature_handshake": True,
                "native_xpic_commands": True,
                "combined_clock_weather_two_entities": True,
                "weather_forecast_response_api": True,
                "clock_ticks_without_page_refresh": True,
                "idle_navigation_hidden": True,
                "endpoints": "isolated loopback HA and UART wire simulators",
                "physical_hardware_tested": False,
            }
            await asyncio.to_thread(
                (ROOT / "docs/BRIDGE-BINARY-CHECK.json").write_text,
                json.dumps(report, indent=2) + "\n",
                encoding="utf-8",
            )
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
            server.close()
            await server.wait_closed()
            for writer in writers:
                writer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executable", type=Path, default=ROOT / ".cache/portable/uBridge/uBridge.exe"
    )
    asyncio.run(check(parser.parse_args().executable.resolve()))
