"""Exercise the real network client/controller against an isolated HA wire fixture."""

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import replace

import pytest
from aiohttp import web
from tjc_display.actions import Action
from tjc_display.protocol import Frame

from tjc_bridge.api import APIError, AuthenticationError, HomeAssistantAPI
from tjc_bridge.config import CredentialStore, Settings, load_settings, normalize_url, save_settings
from tjc_bridge.runtime import Bridge, dashboard_panels


def test_removed_batch_setting_is_ignored_when_loading_old_configuration(tmp_path):
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"serial_batch_size": 1, "brightness": 75}))
    settings = load_settings(path)
    assert settings.brightness == 75
    assert not hasattr(settings, "serial_batch_size")
    save_settings(path, settings)
    assert "serial_batch_size" not in json.loads(path.read_text())


class FakeHA:
    def __init__(self):
        self.forecasts = {}
        self.calls = []
        self.connections = {}
        self.drop_service = False
        self.states = {
            "switch.room": {
                "entity_id": "switch.room",
                "state": "off",
                "attributes": {"friendly_name": "Room"},
            }
        }
        self.panels = {
            f"tjc-{number}": {
                "component_name": "lovelace",
                "title": {1: "Home", 2: "Climate", 10: "Outside"}[number],
                "require_admin": False,
            }
            for number in (10, 2, 1)
        }
        self.panels["admin"] = {
            "component_name": "lovelace",
            "title": "Controls",
            "require_admin": True,
        }
        self.panels["legacy"] = {"component_name": "lovelace", "title": "HMI1"}
        self.tokens = []
        self.config = None
        self.history = {"sensor.temp": [{"s": "21", "lu": 100}, {"s": "24", "lu": 200}]}
        self.statistics = {"sensor.energy": [{"start": 100, "change": 3}]}

    async def handler(self, request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_json({"type": "auth_required", "ha_version": "2026.10.4"})
        auth = await socket.receive_json()
        self.tokens.append(auth.get("access_token"))
        if auth != {"type": "auth", "access_token": "test-token"}:
            await socket.send_json({"type": "auth_invalid", "message": "Invalid token"})
            await socket.close()
            return socket
        await socket.send_json({"type": "auth_ok", "ha_version": "2026.10.4"})
        subscriptions = self.connections[socket] = {}
        try:
            async for packet in socket:
                message = packet.json()
                kind, identifier = message["type"], message["id"]
                self.calls.append(message)
                result = None
                if kind == "subscribe_events":
                    subscriptions[message["event_type"]] = identifier
                elif kind == "get_states":
                    # Initial events preceding the snapshot must not win over it.
                    await self.event(
                        "state_changed",
                        {
                            "entity_id": "switch.room",
                            "new_state": {
                                "entity_id": "switch.room",
                                "state": "old",
                                "attributes": {},
                            },
                        },
                    )
                    result = list(self.states.values())
                elif kind == "get_config":
                    result = {"time_zone": "Europe/Rome"}
                elif kind == "get_panels":
                    result = self.panels
                elif kind == "lovelace/config":
                    result = self.config or {
                        "views": [
                            {"path": "home", "cards": [{"type": "tile", "entity": "switch.room"}]}
                        ]
                    }
                elif kind == "history/history_during_period":
                    result = self.history
                elif kind == "recorder/statistics_during_period":
                    result = self.statistics
                elif kind == "call_service" and message.get("domain") == "weather":
                    result = {"response": self.forecasts}
                elif kind == "call_service":
                    if self.drop_service:
                        await socket.close()
                        break
                    entity_id = message["target"]["entity_id"]
                    await self.set_state(entity_id, "on")
                else:
                    await socket.send_json(
                        {
                            "id": identifier,
                            "type": "result",
                            "success": False,
                            "error": {"code": "unknown_command", "message": "Unknown"},
                        }
                    )
                    continue
                await socket.send_json(
                    {"id": identifier, "type": "result", "success": True, "result": result}
                )
        finally:
            self.connections.pop(socket, None)
        return socket

    async def event(self, event_type, data):
        for socket, subscriptions in list(self.connections.items()):
            identifier = subscriptions.get(event_type)
            if identifier and not socket.closed:
                await socket.send_json(
                    {
                        "id": identifier,
                        "type": "event",
                        "event": {"event_type": event_type, "data": data},
                    }
                )

    async def set_state(self, entity_id, state):
        entity = {"entity_id": entity_id, "state": state, "attributes": {"friendly_name": "Room"}}
        self.states[entity_id] = entity
        await self.event("state_changed", {"entity_id": entity_id, "new_state": entity})

    async def close_clients(self):
        for socket in list(self.connections):
            await socket.close()


@asynccontextmanager
async def ha_server():
    fake = FakeHA()
    application = web.Application()
    application.router.add_get("/api/websocket", fake.handler)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield fake, f"http://127.0.0.1:{port}"
    finally:
        await fake.close_clients()
        await runner.cleanup()


class MockPanel:
    def __init__(self, port, baudrate, on_frame, on_connection):
        self.connected = False
        self.on_frame, self.on_connection = on_frame, on_connection
        self.draws = []
        self.brightness_value = None

    def start(self):
        self.connected = True
        self.on_connection(True)

    async def stop(self):
        self.connected = False
        self.on_connection(False)

    async def draw(self, commands):
        self.draws.append(commands)

    async def brightness(self, value):
        self.brightness_value = value


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 - polling observable external state in a test
            await asyncio.sleep(0.02)


async def test_authentication_and_pending_service_disconnect():
    async with ha_server() as (server, url):
        with pytest.raises(AuthenticationError):
            async with HomeAssistantAPI(url, "bad-token"):
                pass
        async with HomeAssistantAPI(url, "test-token") as api:
            with pytest.raises(APIError, match="unknown_command"):
                await api.request("unsupported")
            server.drop_service = True
            with pytest.raises(APIError, match="not retried"):
                await api.call_service(
                    Action("Toggle", "switch", "toggle", {}, {"entity_id": "switch.room"})
                )
            assert api.closed.is_set()
        assert len([call for call in server.calls if call["type"] == "call_service"]) == 1


async def test_chart_queries_and_badge_only_state_updates_over_real_websocket():
    async with ha_server() as (server, url):
        server.config = {
            "views": [
                {
                    "badges": ["switch.room"],
                    "cards": [
                        {"type": "history-graph", "entities": ["sensor.temp"], "hours_to_show": 1},
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
        bridge = Bridge(
            Settings(base_url=url, dashboards=["tjc-1", "tjc-2", "tjc-10"], serial_port="mock"),
            "test-token",
            panel_factory=MockPanel,
        )
        task = asyncio.create_task(bridge.run())
        try:
            await eventually(
                lambda: (
                    bridge.ready and len(bridge.controller.histories) == 2 and bridge.panel.draws
                )
            )
            history_call = next(
                call for call in server.calls if call["type"] == "history/history_during_period"
            )
            assert (
                history_call["entity_ids"] == ["sensor.temp"] and history_call["minimal_response"]
            )
            stat_call = next(
                call for call in server.calls if call["type"] == "recorder/statistics_during_period"
            )
            assert stat_call["statistic_ids"] == ["sensor.energy"] and stat_call["types"] == [
                "change"
            ]
            await asyncio.sleep(0.15)
            count = len(bridge.panel.draws)
            await server.set_state("switch.room", "on")
            await eventually(lambda: len(bridge.panel.draws) > count)
            commands = bridge.panel.draws[-1]
            assert not any(
                command.startswith("cls") or command.startswith("line") for command in commands
            )
            assert any('"on"' in command for command in commands)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def test_idle_wake_touch_is_not_forwarded_as_service():
    async with ha_server() as (server, url):
        bridge = Bridge(
            Settings(base_url=url, dashboards=["tjc-1", "tjc-2", "tjc-10"], serial_port="mock"),
            "test-token",
            panel_factory=MockPanel,
        )
        now = [0.0]
        bridge.idle.clock = lambda: now[0]
        task = asyncio.create_task(bridge.run())
        try:
            await eventually(lambda: bridge.ready and bridge.panel.draws)
            now[0] = 120
            bridge.idle._changed.set()
            await eventually(lambda: bridge.panel.brightness_value == 0)
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            await eventually(lambda: bridge.panel.brightness_value == 80)
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await asyncio.sleep(0.12)
            assert not any(call["type"] == "call_service" for call in server.calls)
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await eventually(lambda: any(call["type"] == "call_service" for call in server.calls))
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def test_remote_bridge_states_touch_pages_and_reconnect_without_replay():
    async with ha_server() as (server, url):
        bridge = Bridge(
            Settings(
                base_url=url,
                dashboards=["tjc-1", "tjc-2", "tjc-10"],
                serial_port="mock",
                brightness=67,
            ),
            "test-token",
            panel_factory=MockPanel,
        )
        task = asyncio.create_task(bridge.run())
        try:
            await eventually(lambda: bridge.ready and bridge.panel.draws)
            assert [page.title for page in bridge.controller.pages] == [
                "Home",
                "Climate",
                "Outside",
            ]
            assert bridge.entities["switch.room"].state == "off"
            assert bridge.panel.brightness_value == 67
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await eventually(lambda: bridge.entities["switch.room"].state == "on")
            call = next(call for call in server.calls if call["type"] == "call_service")
            assert call["domain"] == "switch" and call["service"] == "toggle"
            assert call["target"] == {"entity_id": "switch.room"} and call["service_data"] == {}
            server.panels.pop("tjc-2")
            await server.event("panels_updated", {})
            await eventually(lambda: len(bridge.controller.pages) == 2)
            bridge.reload()
            await eventually(lambda: any(call.get("force") for call in server.calls))
            await server.close_clients()
            await eventually(lambda: not bridge.ready)
            assert not bridge.entities
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await eventually(lambda: bridge.ready)
            assert len([call for call in server.calls if call["type"] == "call_service"]) == 1
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        assert not bridge.panel.connected


def test_url_validation_and_non_secret_configuration(tmp_path):
    assert normalize_url(" https://example.test/ha/api/websocket/ ") == "https://example.test/ha"
    for value in (
        "example.test",
        "ftp://example.test",
        "http://token@example.test",
        "http://example.test?token=secret",
        "http://example.test/#secret",
        "http://example.test:bad",
        "http://exa mple.test",
    ):
        with pytest.raises(ValueError):
            normalize_url(value)
    settings = Settings(base_url="http://example.test/", serial_port=" COM4 ").validated()
    path = tmp_path / "config.json"
    save_settings(path, settings)
    assert load_settings(path) == settings
    assert '"access_token"' not in path.read_text() and '"api_key"' not in path.read_text()
    assert settings.serial_port == "COM4" and settings.base_url == "http://example.test"
    with pytest.raises(ValueError):
        replace(settings, brightness=0).validated()


def test_dashboard_matching_includes_yaml_and_filters_admin():
    panels = {
        "tjc-2": {"title": "Climate", "component_name": "lovelace", "config": {"mode": "yaml"}},
        "tjc-1": {"title": "Home", "component_name": "lovelace", "config": {"mode": "storage"}},
        "tjc-3": {"title": "Controls", "component_name": "lovelace", "require_admin": True},
        "map": {"title": "Charts", "component_name": "map"},
    }
    assert [entry[0] for entry in dashboard_panels(panels, ["tjc-1", "tjc-2", "tjc-3", "map"])] == [
        "tjc-1",
        "tjc-2",
    ]


def test_token_storage_is_separate_and_session_only_mode_removes_saved_token(tmp_path, monkeypatch):
    class MemoryVault:
        def __init__(self):
            self.values = {}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, password):
            self.values[service, account] = password

        def delete_password(self, service, account):
            self.values.pop((service, account), None)

    store, vault = CredentialStore(), MemoryVault()
    monkeypatch.setattr(store, "_backend", lambda: vault)
    settings = Settings(base_url="http://example.test", serial_port="COM4")
    token = "private-test-token-never-in-config"
    store.set(settings, token)
    assert store.get(settings) == token
    path = tmp_path / "config.json"
    save_settings(path, settings)
    assert token not in path.read_text()
    session = replace(settings, remember_token=False)
    store.set(session, token)
    assert not store.get(session) and not vault.values


def test_invalid_config_types_and_active_token_log_redaction(tmp_path):
    import logging

    from tjc_bridge.app import RedactToken

    path = tmp_path / "config.json"
    path.write_text('{"brightness": "bad", "base_url": null}')
    with pytest.raises(ValueError, match="invalid types"):
        load_settings(path)
    redact = RedactToken()
    redact.token = "never-log-this"
    record = logging.LogRecord("test", logging.ERROR, "", 1, "Error %s", (redact.token,), None)
    assert redact.filter(record)
    assert record.getMessage() == "Error [token]"


def test_linux_autostart_quotes_paths_and_is_removable(tmp_path, monkeypatch):
    from tjc_bridge import autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        autostart,
        "launch_command",
        lambda config: ["/path with space/python", "-m", "tjc_bridge", "--config", str(config)],
    )
    config = tmp_path / "percent%file.json"
    autostart.set_enabled(True, config)
    desktop = tmp_path / "autostart/ubridge.desktop"
    content = desktop.read_text()
    assert '"/path with space/python"' in content and "percent%%file.json" in content
    autostart.set_enabled(False, config)
    assert not desktop.exists()


async def test_selected_dashboard_idle_is_separate_live_and_restores_previous_page():
    async with ha_server() as (server, url):
        server.panels["idle-clock"] = {"component_name": "lovelace", "title": "Night clock"}
        settings = Settings(
            base_url=url,
            serial_port="mock",
            dashboards=["tjc-2", "tjc-1"],
            idle_mode="dashboard",
            idle_dashboard="idle-clock",
            idle_brightness=80,
        )
        bridge = Bridge(settings, "test-token", panel_factory=MockPanel)
        now = [0.0]
        bridge.idle.clock = lambda: now[0]
        task = asyncio.create_task(bridge.run())
        try:
            await eventually(lambda: bridge.ready and bridge.panel.draws)
            assert [p.title for p in bridge.controller.pages] == ["Climate", "Home"]
            bridge.controller.show_page(1)
            await asyncio.sleep(0.12)
            now[0] = 60
            bridge.idle._changed.set()
            await eventually(lambda: bridge.controller.pages[0].title == "Night clock")
            # Even equal active/idle brightness must require a wake-only gesture.
            assert bridge.panel.brightness_value == 80
            now[0] = 500
            bridge.idle._changed.set()
            await asyncio.sleep(0.05)
            assert bridge.panel.brightness_value == 80
            await bridge._load_pages()
            assert bridge.controller.pages[0].title == "Night clock"
            count = len(bridge.panel.draws)
            await server.set_state("switch.room", "on")
            await eventually(lambda: len(bridge.panel.draws) > count)
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x01"))
            bridge._frame(Frame(b"\x67\x00\x20\x00\x40\x00"))
            await eventually(
                lambda: bridge.controller.pages[bridge.controller.page].title == "Home"
            )
            assert not any(call["type"] == "call_service" for call in server.calls)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def test_dashboard_selection_preserves_order_and_titles_can_change():
    panels = {
        "a": {"component_name": "lovelace", "title": "Arbitrary title"},
        "b": {"component_name": "lovelace", "title": "Kitchen"},
    }
    assert dashboard_panels(panels, ["b", "a", "b"]) == [("b", "Kitchen"), ("a", "Arbitrary title")]
    panels["b"]["title"] = "Renamed"
    assert dashboard_panels(panels, ["b"]) == [("b", "Renamed")]
    assert dashboard_panels(panels, []) == []


def test_source_autostart_command_runs_outside_checkout(tmp_path):
    import subprocess

    from tjc_bridge.autostart import launch_command

    command = launch_command(tmp_path / "config.json")
    import sys

    command[0] = sys.executable
    result = subprocess.run(
        [*command, "--version"], cwd=tmp_path, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0
    assert "uBridge 0.8.0" in result.stdout


async def test_dashboard_discovery_includes_hidden_and_default_dashboard():
    from tjc_bridge.runtime import available_dashboards

    class API:
        async def request(self, kind):
            if kind == "get_panels":
                return {"lovelace": {"component_name": "lovelace", "title": None}}
            return [
                {"url_path": "hidden", "title": "Quiet room", "show_in_sidebar": False},
                {"url_path": "private", "title": "Admin", "require_admin": True},
            ]

    panels = await available_dashboards(API())
    assert dashboard_panels(panels, ["hidden", "lovelace", "private"]) == [
        ("hidden", "Quiet room"),
        ("lovelace", "Overview"),
    ]


def test_windows_autostart_replaces_legacy_entry_and_removes_new_entry(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from tjc_bridge import autostart

    values = {"TJCBridge": "old invalid location"}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def delete(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        REG_SZ=1,
        CreateKey=lambda *args: Key(),
        DeleteValue=delete,
        SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(
        autostart,
        "launch_command",
        lambda config: [
            r"C:\Program Files\uBridge\uBridge.exe",
            "--background",
            "--config",
            str(config),
        ],
    )
    path = tmp_path / "config.json"
    autostart.set_enabled(True, path)
    assert "TJCBridge" not in values and "uBridge" in values
    assert values["uBridge"].startswith('"C:\\Program Files\\uBridge\\uBridge.exe"')
    autostart.set_enabled(False, path)
    assert not values


async def test_weather_response_api_and_live_combined_clock_weather_view():
    async with ha_server() as (server, url):
        server.states["weather.home"] = {
            "entity_id": "weather.home",
            "state": "sunny",
            "attributes": {
                "friendly_name": "Home weather",
                "temperature": 18.5,
                "temperature_unit": "C",
                "humidity": 64,
            },
        }
        server.forecasts = {
            "weather.home": {
                "forecast": [
                    {
                        "datetime": "2026-11-08T12:00:00+00:00",
                        "condition": "rainy",
                        "temperature": 19,
                        "templow": 10,
                    }
                ]
            }
        }
        server.config = {
            "views": [
                {
                    "cards": [
                        {"type": "clock", "show_seconds": True},
                        {"type": "weather-forecast", "entity": "weather.home"},
                        {"type": "tile", "entity": "switch.room"},
                        {"type": "tile", "entity": "sensor.temp"},
                    ]
                }
            ]
        }
        bridge = Bridge(
            Settings(base_url=url, serial_port="mock", dashboards=["tjc-1"]),
            "test-token",
            panel_factory=MockPanel,
        )
        task = asyncio.create_task(bridge.run())
        try:
            await eventually(
                lambda: (
                    ("weather.home", "daily") in bridge.controller.forecasts
                    and any("Next 19/10C" in c for draw in bridge.panel.draws for c in draw)
                )
            )
            assert bridge.controller.time_zone == "Europe/Rome"
            forecast_call = next(call for call in server.calls if call["type"] == "call_service")
            assert (
                forecast_call["domain"] == "weather" and forecast_call["service"] == "get_forecasts"
            )
            assert forecast_call["return_response"] and forecast_call["service_data"] == {
                "type": "daily"
            }
            assert not any(
                "Items " in c or "Page >" in c for draw in bridge.panel.draws for c in draw
            )
            assert bridge.controller.dashboard_count == 1
            count = len(bridge.panel.draws)
            state = server.states["weather.home"]
            state["state"] = "rainy"
            await server.event("state_changed", {"entity_id": "weather.home", "new_state": state})
            await eventually(
                lambda: any("Rainy" in c for draw in bridge.panel.draws[count:] for c in draw)
            )
            assert not any(
                "Page >" in c or c.startswith("cls")
                for draw in bridge.panel.draws[count:]
                for c in draw
            )
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
