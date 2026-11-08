"""Local serial frontend for selected Home Assistant dashboards."""

import asyncio
import logging
import time
from datetime import timedelta

try:
    import probatio as vol
except ImportError:  # Home Assistant before the schema-library migration.
    import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    EVENT_LOVELACE_UPDATED,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.start import async_at_started

from .dashboards import dashboard_registry, load_dashboards
from .display.const import BAUD_RATES, DEFAULT_BAUD, DOMAIN
from .display.controller import Controller
from .display.firmware import upload
from .display.history import HistoryData
from .display.idle import BacklightSettings, IdleBacklight
from .display.model import Entity
from .display.transport import Panel, PanelError
from .display.weather import forecast_loop
from .history import chart_data

_LOGGER = logging.getLogger(__name__)
ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required("port"): cv.string,
        vol.Optional("brightness", default=80): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Optional("idle_enabled", default=True): cv.boolean,
        vol.Optional("idle_dim_seconds", default=60): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=86400)
        ),
        vol.Optional("idle_dim_percent", default=50): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional("idle_off_seconds", default=60): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=86400)
        ),
        vol.Optional("baudrate", default=DEFAULT_BAUD): vol.In(BAUD_RATES),
        vol.Optional("dashboards", default=list): [cv.string],
        vol.Optional("idle_mode", default="dim"): vol.In(["dim", "dashboard"]),
        vol.Optional("idle_dashboard", default=""): cv.string,
        vol.Optional("idle_brightness", default=50): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
    }
)
CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): ENTRY_SCHEMA}, extra=vol.ALLOW_EXTRA)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version > 3:
        return False
    if entry.version < 3:
        from .display.model import dashboard_number

        data, options = dict(entry.data), dict(entry.options)
        effective = {**data, **options}
        prefix = effective.get("dashboard_prefix", "TJC")
        if entry.version == 1 and prefix == "HMI":
            prefix = "TJC"
        matches = []
        for url, dashboard in dashboard_registry(hass).items():
            metadata = dashboard.config or {}
            number = dashboard_number(str(metadata.get("title", "")), prefix)
            if number is not None and not metadata.get("require_admin"):
                matches.append((number, url))
        data["dashboards"] = [url for _, url in sorted(matches)]
        for values in (data, options):
            values.pop("dashboard_prefix", None)
        hass.config_entries.async_update_entry(entry, data=data, options=options, version=3)
    return True


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(config[DOMAIN])
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    settings = {**entry.data, **entry.options}
    runtime = Runtime(hass, settings)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_reload_entry))
    await runtime.start()
    if not hass.services.has_service(DOMAIN, "reload"):
        hass.services.async_register(
            DOMAIN, "reload", _service_handler(hass, "reload"), schema=vol.Schema({})
        )
        hass.services.async_register(
            DOMAIN,
            "show_page",
            _service_handler(hass, "show_page"),
            schema=vol.Schema({vol.Required("page"): cv.string}),
        )
        hass.services.async_register(
            DOMAIN,
            "set_brightness",
            _service_handler(hass, "set_brightness"),
            schema=vol.Schema(
                {vol.Required("brightness"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100))}
            ),
        )
    if not hass.services.has_service(DOMAIN, "flash_firmware"):

        async def flash(call):
            runtime = hass.data[DOMAIN].get(call.data["entry_id"])
            if runtime is None:
                raise ServiceValidationError("Unknown TJC integration entry_id")
            await runtime.flash(call.data["firmware_path"])

        async_register_admin_service(
            hass,
            DOMAIN,
            "flash_firmware",
            flash,
            schema=vol.Schema(
                {vol.Required("entry_id"): cv.string, vol.Required("firmware_path"): cv.string}
            ),
        )
    return True


async def _reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    port = entry.options.get("port", entry.data["port"])
    if entry.unique_id != port:
        hass.config_entries.async_update_entry(entry, unique_id=port)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime.stop()
    if not hass.data[DOMAIN]:
        for service in ("reload", "show_page", "set_brightness", "flash_firmware"):
            hass.services.async_remove(DOMAIN, service)
    return True


def _service_handler(hass: HomeAssistant, operation: str):
    async def handle(call: ServiceCall) -> None:
        for runtime in list(hass.data[DOMAIN].values()):
            if operation == "reload":
                await runtime.reload(force=True)
            elif operation == "show_page":
                page = call.data["page"]
                index = next(
                    (
                        i
                        for i, p in enumerate(runtime.controller.pages)
                        if p.title.casefold() == page.casefold() or p.key == page
                    ),
                    None,
                )
                if index is None:
                    raise ServiceValidationError(f"HMI page {page!r} not found")
                runtime.controller.show_page(index)
            else:
                try:
                    runtime.idle.settings.brightness = call.data["brightness"]
                    runtime.idle.connected()
                    await runtime.panel.brightness(call.data["brightness"])
                except (PanelError, TimeoutError, OSError) as err:
                    raise HomeAssistantError(str(err)) from err

    return handle


class Runtime:
    def __init__(self, hass: HomeAssistant, settings: dict) -> None:
        self.hass, self.settings = hass, settings
        self._unsubscribers = []
        self._reload_lock = asyncio.Lock()
        self._last_warnings = []
        self._started = self._stopped = False
        self._history_task = None
        self._weather_task = None
        self._idle_task = None
        self._flash_lock = asyncio.Lock()
        self._flash_task = None
        self.panel = Panel(settings["port"], settings["baudrate"], self._frame, self._connection)
        self.controller = Controller(self.panel, self._service, self._state)
        self.controller.dashboard_count = len(set(settings.get("dashboards", [])))
        self.controller.time_zone = hass.config.time_zone
        self.idle = IdleBacklight(
            self.panel,
            BacklightSettings(
                **{k: settings[k] for k in BacklightSettings.__dataclass_fields__ if k in settings}
            ),
            on_idle=lambda active: self.controller.idle_dashboard(
                active and settings.get("idle_mode", "dim") == "dashboard"
            ),
        )

    async def start(self) -> None:
        self._unsubscribers.append(async_at_started(self.hass, self._started_callback))
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._stop_event)
        )

    async def _started_callback(self, hass) -> None:
        if self._stopped or self._started:
            return
        self._started = True
        await self.reload()
        self.controller.start()
        self.panel.start()
        self._idle_task = self.hass.async_create_background_task(self.idle.run(), "tjc_idle")
        self._history_task = self.hass.async_create_background_task(
            self._history_loop(), "tjc_history"
        )
        self._weather_task = self.hass.async_create_background_task(
            forecast_loop(self.controller, self._forecast), "tjc_weather"
        )
        self._unsubscribers.extend(
            [
                self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_changed),
                self.hass.bus.async_listen(EVENT_LOVELACE_UPDATED, self._dashboard_changed),
                async_track_time_interval(self.hass, self._poll, timedelta(seconds=30)),
            ]
        )

    async def _stop_event(self, event) -> None:
        await self.stop()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._flash_task:
            await asyncio.gather(self._flash_task, return_exceptions=True)
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._idle_task:
            self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
        if self._history_task:
            self._history_task.cancel()
            await asyncio.gather(self._history_task, return_exceptions=True)
        if self._weather_task:
            self._weather_task.cancel()
            await asyncio.gather(self._weather_task, return_exceptions=True)
        await self.controller.stop()
        await self.panel.stop()

    async def flash(self, firmware_path):
        from pathlib import Path

        path = Path(firmware_path)
        if not path.is_absolute() or path.suffix.lower() != ".tft":
            raise ServiceValidationError("Use an absolute local .tft path on the HA host")
        async with self._flash_lock:
            if self._stopped:
                raise HomeAssistantError("Panel is stopping")
            await self.controller.stop()
            await self.panel.stop()
            self._flash_task = asyncio.ensure_future(
                self.hass.async_add_executor_job(
                    upload, path, self.settings["port"], self.settings["baudrate"]
                )
            )
            try:
                await asyncio.shield(self._flash_task)
            except asyncio.CancelledError:
                await self._flash_task
                raise
            except (OSError, ValueError, RuntimeError) as err:
                raise HomeAssistantError(str(err)) from err
            finally:
                self._flash_task = None
                if not self._stopped:
                    self.controller.start()
                    self.controller.invalidate(force=True)
                    self.panel.start()

    async def reload(self, *, force=False) -> None:
        async with self._reload_lock:
            self.controller.dashboard_count = len(set(self.settings.get("dashboards", [])))
            pages, warnings = await load_dashboards(
                self.hass, self.settings.get("dashboards", []), force=force
            )
            if self._stopped:
                return
            idle_pages = []
            if self.settings.get("idle_mode") == "dashboard" and self.settings.get(
                "idle_dashboard"
            ):
                idle_pages, idle_warnings = await load_dashboards(
                    self.hass, [self.settings["idle_dashboard"]], force=force
                )
                warnings.extend(idle_warnings)
            self.controller.configure_idle_pages(idle_pages)
            self.controller.update_pages(pages)
            self.controller.invalidate(force=force)
            if warnings != self._last_warnings:
                for warning in warnings:
                    _LOGGER.warning("%s", warning)
                self._last_warnings = warnings

    async def _history_loop(self):
        fetched = {}
        while True:
            requests = self.controller.history_requests
            fetched = {key: when for key, when in fetched.items() if key in requests}
            for key, item in requests.items():
                if time.monotonic() - fetched.get(key, -1000) < 60:
                    continue
                end = time.time()
                try:
                    data = await chart_data(self.hass, item, end)
                except Exception:
                    _LOGGER.debug("Recorder chart query failed", exc_info=True)
                    data = HistoryData(end - item.hours * 3600, end, error="History unavailable")
                if key in self.controller.history_requests:
                    self.controller.histories[key] = data
                    self.controller.invalidate()
                fetched[key] = time.monotonic()
            await asyncio.sleep(1)

    async def _forecast(self, entity_id, forecast_type):
        return await self.hass.services.async_call(
            "weather",
            "get_forecasts",
            {"type": forecast_type},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )

    async def _poll(self, now) -> None:
        # Also discovers newly-created/deleted or renamed dashboard registrations.
        try:
            await self.reload()
        except (HomeAssistantError, OSError, ValueError):
            _LOGGER.exception("Cannot refresh HMI dashboards")

    async def _dashboard_changed(self, event) -> None:
        # Do not force: YAML async_load(force=True) itself emits this event.
        await self._poll(None)

    @callback
    def _state_changed(self, event) -> None:
        if event.data.get("entity_id") in self.controller.entity_ids:
            self.controller.invalidate()

    @callback
    def _frame(self, frame) -> None:
        if frame.kind in {0x67, 0x68} and self.idle.touch(bool(frame.touch[2])):
            self.controller._pressed = None
            return
        self.controller.frame(frame)

    @callback
    def _connection(self, connected) -> None:
        self.controller.connection(connected)
        if connected:
            self.idle.connected()

    def _state(self, entity_id: str) -> Entity | None:
        state = self.hass.states.get(entity_id)
        return Entity(entity_id, state.state, dict(state.attributes)) if state else None

    async def _service(self, action) -> None:
        # Actions are selected from configured dashboard entities, never supplied by UART.
        # HA's own service registry validates all service data and target selectors.
        await self.hass.services.async_call(
            action.domain, action.service, action.data, target=action.target, blocking=True
        )
