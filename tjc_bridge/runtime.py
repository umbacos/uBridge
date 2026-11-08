"""Bridge remote HA state/actions to the shared display controller and local UART."""

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, replace

import aiohttp
from tjc_display.controller import Controller
from tjc_display.history import HistoryData
from tjc_display.model import Entity, parse_dashboard
from tjc_display.transport import Panel
from tjc_display.weather import forecast_loop

from .api import APIError, AuthenticationError, HomeAssistantAPI
from .config import Settings
from .idle import IdleBacklight

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Status:
    home_assistant: str = "Disconnected"
    display: str = "Disconnected"
    pages: int = 0
    detail: str = "Configure the bridge to connect."


def dashboard_panels(panels: dict, selected=None):
    matches = []
    for url, panel in panels.items():
        if panel.get("component_name") != "lovelace" or panel.get("require_admin"):
            continue
        title = str(panel.get("title") or ("Overview" if url == "lovelace" else url))
        matches.append((url, title))
    if selected is None:
        return sorted(matches, key=lambda row: (row[1].casefold(), row[0]))
    by_url = dict(matches)
    return [(url, by_url[url]) for url in dict.fromkeys(selected) if url in by_url]


async def available_dashboards(api):
    panels = dict(await api.request("get_panels"))
    try:
        dashboards = await api.request("lovelace/dashboards/list")
    except APIError:
        dashboards = []  # Older servers may only expose registered panels.
    for dashboard in dashboards:
        url = dashboard["url_path"]
        panels[url] = {**panels.get(url, {}), **dashboard, "component_name": "lovelace"}
    return panels


class Bridge:
    def __init__(
        self,
        settings: Settings,
        token: str,
        on_status=lambda status: None,
        *,
        panel_factory=Panel,
        api_factory=HomeAssistantAPI,
    ):
        self.settings = settings.validated()
        self._token = token
        self.on_status = on_status
        self.api_factory = api_factory
        self.api = None
        self.ready = False
        self.entities = {}
        self.status = Status()
        self._refresh = asyncio.Event()
        self._force = False
        self._brightness_task = None
        self._warnings = []
        self.panel = panel_factory(
            self.settings.serial_port,
            self.settings.baudrate,
            self._frame,
            self._connection,
        )
        self.controller = Controller(self.panel, self._service, self.entities.get)
        self.controller.dashboard_count = len(set(self.settings.dashboards))
        self.idle = IdleBacklight(self.panel, self.settings, on_idle=self._idle_changed)

    def _idle_changed(self, active):
        self.controller.idle_dashboard(active and self.settings.idle_mode == "dashboard")

    def _status(self, **changes):
        self.status = replace(self.status, **changes)
        self.on_status(asdict(self.status))

    def _connection(self, connected):
        self.controller.connection(connected)
        mode = "native icons" if getattr(self.panel, "native_icons", False) else "vector icons"
        self._status(display=f"Connected ({mode})" if connected else "Disconnected")
        if connected:
            self.idle.connected()

    def _frame(self, frame):
        if frame.kind in {0x67, 0x68} and self.idle.touch(bool(frame.touch[2])):
            self.controller._pressed = None
            return
        if self.ready:
            self.controller.frame(frame)

    async def _service(self, action):
        if not self.ready or self.api is None:
            raise APIError("Home Assistant is disconnected.")
        await self.api.call_service(action)

    def reload(self):
        self._force = True
        self._refresh.set()

    def _offline(self, message):
        self.ready = False
        self.entities.clear()
        self.controller.update_pages([])
        self.controller.configure_idle_pages([])
        self.controller.message = message
        self.controller.invalidate(force=True)
        self._status(home_assistant="Disconnected", pages=0, detail=message)

    def _snapshot(self, states):
        self.entities.clear()
        for state in states:
            self._update_entity(state)
        self.controller.invalidate()

    def _update_entity(self, state):
        if state and isinstance(state.get("entity_id"), str):
            self.entities[state["entity_id"]] = Entity(
                state["entity_id"],
                str(state.get("state", "unknown")),
                dict(state.get("attributes") or {}),
            )

    def _state_event(self, event):
        data = event.get("data", {})
        entity_id = data.get("entity_id")
        if data.get("new_state"):
            self._update_entity(data["new_state"])
        else:
            self.entities.pop(entity_id, None)
        if entity_id in self.controller.entity_ids:
            self.controller.invalidate()

    async def _load_pages(self):
        panels = await available_dashboards(self.api)
        pages, warnings = [], []
        force, self._force = self._force, False
        selected = self.settings.dashboards
        idle_url = self.settings.idle_dashboard if self.settings.idle_mode == "dashboard" else ""
        loaded = {}
        for url, title in dashboard_panels(panels, [*selected, *([idle_url] if idle_url else [])]):
            try:
                config = await self.api.request(
                    "lovelace/config", url_path=None if url == "lovelace" else url, force=force
                )
                parsed, notes = parse_dashboard(url, title, config, panels[url].get("icon"))
                loaded[url] = parsed
                warnings.extend(notes)
            except (APIError, ValueError, TypeError) as err:
                warnings.append(f"{title}: {str(err).replace(self._token, '[token]')}")
        for url in selected:
            pages.extend(loaded.get(url, []))
        for url in dict.fromkeys([*selected, *([idle_url] if idle_url else [])]):
            if url not in loaded:
                warnings.append(f"{url}: dashboard unavailable; update Configuration.")
        self.controller.configure_idle_pages(loaded.get(idle_url, []))
        self.controller.update_pages(pages)
        self.controller.message = ""
        self.controller.invalidate(force=force)
        if warnings != self._warnings:
            for warning in warnings:
                _LOGGER.warning("%s", warning)
            self._warnings = warnings
        self.ready = True
        message = (
            warnings[0]
            if warnings
            else (f"{len(pages)} pages loaded." if pages else "Select dashboards in Configuration.")
        )
        self._status(home_assistant="Connected", pages=len(pages), detail=message)

    async def run(self):
        self.controller.start()
        self._brightness_task = asyncio.create_task(self.idle.run(), name="tjc_idle")
        self._offline("Connecting to Home Assistant...")
        self.panel.start()
        backoff = 1
        try:
            while True:
                self._status(home_assistant="Connecting")
                try:
                    async with self.api_factory(
                        self.settings.base_url,
                        self._token,
                        self.settings.ca_file,
                    ) as api:
                        self.api = api
                        await api.subscribe("state_changed", self._state_event)
                        await api.request("get_states", commit=self._snapshot)
                        try:
                            config = await api.request("get_config")
                            self.controller.time_zone = str(config.get("time_zone") or "UTC")
                        except APIError:
                            self.controller.time_zone = "UTC"
                        for event in ("lovelace_updated", "panels_updated"):
                            try:
                                await api.subscribe(event, lambda event: self._refresh.set())
                            except APIError:
                                pass  # Polling still discovers dashboard changes.
                        await self._load_pages()
                        history_task = asyncio.create_task(self._history_loop(api))
                        weather_task = asyncio.create_task(
                            forecast_loop(self.controller, api.weather_forecast)
                        )
                        backoff = 1
                        try:
                            await self._connected_loop(api)
                        finally:
                            history_task.cancel()
                            weather_task.cancel()
                            await asyncio.gather(history_task, weather_task, return_exceptions=True)
                except AuthenticationError:
                    self._offline("Access token rejected. Update it in Configuration.")
                    raise
                except (APIError, aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
                    message = str(err).replace(self._token, "[token]")
                    _LOGGER.warning("Home Assistant connection: %s", message)
                    self._offline(f"Home Assistant offline; retry in {backoff}s")
                    self._status(detail=f"{message} (retry in {backoff}s)")
                finally:
                    self.api = None
                if self.ready:
                    self._offline("Home Assistant disconnected")
                await asyncio.sleep(backoff)
                backoff = min(30, backoff * 2)
        finally:
            self.ready = False
            if self._brightness_task:
                self._brightness_task.cancel()
                await asyncio.gather(self._brightness_task, return_exceptions=True)
            await self.controller.stop()
            await self.panel.stop()

    async def _history_loop(self, api):
        fetched = {}
        while not api.closed.is_set():
            current = self.controller.history_requests
            fetched = {key: when for key, when in fetched.items() if key in current}
            for key, item in current.items():
                if time.monotonic() - fetched.get(key, -1000) < 60:
                    continue
                end = time.time()
                try:
                    data = await api.chart_data(item, end)
                except (APIError, aiohttp.ClientError, TimeoutError, ValueError, TypeError):
                    data = HistoryData(end - item.hours * 3600, end, error="History unavailable")
                if key in self.controller.history_requests:
                    self.controller.histories[key] = data
                    self.controller.invalidate()
                fetched[key] = time.monotonic()
            await asyncio.sleep(1)

    async def _connected_loop(self, api):
        while not api.closed.is_set():
            refresh = asyncio.create_task(self._refresh.wait())
            closed = asyncio.create_task(api.closed.wait())
            try:
                await asyncio.wait(
                    {refresh, closed},
                    timeout=30,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                refresh.cancel()
                closed.cancel()
                await asyncio.gather(refresh, closed, return_exceptions=True)
            if api.closed.is_set():
                break
            self._refresh.clear()
            await self._load_pages()


async def test_connection(settings: Settings, token: str):
    """Network-only test: never opens the display port or changes HA entities."""
    async with HomeAssistantAPI(settings.base_url, token, settings.ca_file) as api:
        panels = await available_dashboards(api)
        return dashboard_panels(panels)
