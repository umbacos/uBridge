"""Application state and release-only touch routing, independent of Home Assistant."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from .actions import Action, controls
from .const import HEIGHT, WIDTH
from .history import requests
from .layout import sheets
from .model import Entity, Page
from .protocol import Frame
from .render import Region, Screen, render
from .transport import PanelError

_LOGGER = logging.getLogger(__name__)


class Controller:
    def __init__(
        self,
        panel,
        service: Callable[[Action], Awaitable[None]],
        state: Callable[[str], Entity | None],
    ) -> None:
        self.panel, self.service, self.state = panel, service, state
        self.pages: list[Page] = []
        self._normal_pages = []
        self.dashboard_count = None
        self.idle_pages = []
        self._idle_active = False
        self._resume = (None, 0)
        self.page = self.sheet = self.option_sheet = 0
        self.badge_sheet = 0
        self.histories = {}
        self.forecasts = {}
        self.time_zone = "UTC"
        self.detail: str | None = None
        self.confirm: Action | None = None
        self._confirm_entity: str | None = None
        self.message = ""
        self._screen = Screen()
        self._pressed: Region | None = None
        self._dirty = asyncio.Event()
        self._generation = 0
        self._drawing = self._busy = False
        self._passive_drawing = False
        self._last_commands: list[str] = []
        self._task: asyncio.Task | None = None
        self._touch_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._action_task: asyncio.Task | None = None

    @property
    def entity_ids(self) -> set[str]:
        return {
            eid
            for page in (*self._normal_pages, *self.idle_pages)
            for item in (*page.items, *page.badges)
            for eid in (item.entity_id, *item.series)
            if eid
        }

    @property
    def forecast_requests(self):
        return {
            (item.entity_id, item.forecast_type)
            for page in (*self._normal_pages, *self.idle_pages)
            for item in page.items
            if item.kind == "weather-forecast" and item.show_forecast
        }

    @property
    def history_requests(self):
        return requests([*self._normal_pages, *self.idle_pages])

    def start(self) -> None:
        self._task = asyncio.create_task(self._render_loop(), name="tjc_hmi_render")
        self._action_task = asyncio.create_task(self._action_loop(), name="tjc_hmi_actions")

    async def stop(self) -> None:
        for task in (self._task, self._action_task):
            if task:
                task.cancel()
        await asyncio.gather(
            *(t for t in (self._task, self._action_task) if t), return_exceptions=True
        )

    def update_pages(self, pages: list[Page]) -> None:
        self._normal_pages = pages
        self.forecasts = {
            key: data for key, data in self.forecasts.items() if key in self.forecast_requests
        }
        self._update_pages(self.idle_pages if self._idle_active else pages)

    def configure_idle_pages(self, pages):
        self.idle_pages = pages
        self.forecasts = {
            key: data for key, data in self.forecasts.items() if key in self.forecast_requests
        }
        if self._idle_active:
            self._update_pages(pages)

    def idle_dashboard(self, active):
        if active == self._idle_active:
            return
        if active:
            self._resume = (self.pages[self.page].key if self.pages else None, self.sheet)
        self._idle_active = active
        self._update_pages(self.idle_pages if active else self._normal_pages)
        if active:
            self.show_page(0)
        else:
            key, sheet = self._resume
            index = next((i for i, p in enumerate(self.pages) if p.key == key), 0)
            self.show_page(index)
            self.sheet = sheet if self.pages and sheet < len(sheets(self.pages[index].items)) else 0
        self.invalidate(force=True)

    def _update_pages(self, pages: list[Page]) -> None:
        key = self.pages[self.page].key if self.pages else None
        if pages == self.pages:
            return
        self.pages = pages
        self.histories = {
            key: data for key, data in self.histories.items() if key in self.history_requests
        }
        self.page = next((i for i, page in enumerate(pages) if page.key == key), 0)
        self.sheet = self.option_sheet = 0
        self.detail = self.confirm = None
        self._generation += 1
        self._pressed = None
        self.invalidate()

    def invalidate(self, *, force=False) -> None:
        if force:
            self._last_commands = []
        self._dirty.set()

    def show_page(self, index: int) -> None:
        self.page = index
        self.sheet = self.option_sheet = 0
        self.detail = self.confirm = None
        self._pressed = None
        self._generation += 1
        self.invalidate(force=True)

    def connection(self, connected: bool) -> None:
        self._pressed = None
        self._generation += 1
        self._last_commands = []
        if connected:
            self.invalidate()

    def frame(self, frame: Frame) -> None:
        if (
            frame.kind not in {0x67, 0x68}
            or self._drawing
            and not self._passive_drawing
            or self._busy
            or not self.panel.connected
        ):
            self._pressed = None
            return
        x, y, pressed = frame.touch
        if frame.kind == 0x68 or not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            self._pressed = None
            return
        region = next((r for r in reversed(self._screen.regions) if r.contains(x, y)), None)
        if pressed:
            self._pressed = region
            return
        start, self._pressed = self._pressed, None
        if start and start == region:
            try:
                self._touch_queue.put_nowait((self._generation, region))
            except asyncio.QueueFull:
                pass  # Ignore bursts; never create an unbounded service-call backlog.

    async def _action_loop(self) -> None:
        while True:
            generation, region = await self._touch_queue.get()
            if generation != self._generation or not self.panel.connected:
                continue
            self._busy = True
            try:
                await self.activate(region)
            except Exception:
                _LOGGER.exception("Display touch action failed")
                self.message = "Action failed - see Home Assistant log"
            finally:
                self._busy = False
                self._generation += 1
                self.invalidate()

    async def activate(self, region: Region) -> None:
        self.message = ""
        event = region.event
        if event in {"next", "previous"} and self.pages:
            self.page = (self.page + (1 if event == "next" else -1)) % len(self.pages)
            self.sheet = 0
        elif event == "sheet" and self.pages:
            count = len(sheets(self.pages[self.page].items))
            self.sheet = (self.sheet + 1) % count
        elif event == "badges":
            self.badge_sheet += 1
        elif event == "detail":
            self.detail, self.option_sheet = region.entity_id, 0
        elif event in {"back", "cancel"}:
            self.detail = self.confirm = None
        elif event == "options" and self.detail:
            entity = self.state(self.detail)
            count = max(1, (len(controls(entity)) + 3) // 4) if entity else 1
            self.option_sheet = (self.option_sheet + 1) % count
        elif event in {"action", "confirmed"} and region.action:
            action = region.action
            entity_id = self._confirm_entity if event == "confirmed" else region.entity_id
            entity = self.state(entity_id) if entity_id else None
            if entity_id and (not entity or not entity.available):
                self.message = "Entity is unavailable"
                self.confirm = None
                return
            if action.detail:
                self.detail, self.option_sheet = region.entity_id, 0
            elif action.navigation:
                path = action.navigation.strip("/")
                index = next(
                    (
                        i
                        for i, p in enumerate(self.pages)
                        if p.key.strip("/") == path or p.key.split("/")[0] == path
                    ),
                    None,
                )
                if index is not None:
                    self.page, self.sheet = index, 0
                else:
                    self.message = "Navigation target is not an HMI page"
            elif action.service:
                if action.confirmation and event != "confirmed":
                    self.confirm = action
                    self._confirm_entity = entity_id
                else:
                    self.confirm = None
                    await self.service(replace(action, confirmation=None))

    async def _render_loop(self) -> None:
        while True:
            clock_visible = bool(
                self.pages
                and not self.detail
                and not self.confirm
                and any(
                    item.kind == "clock"
                    for _, item, *_ in sheets(self.pages[self.page].items)[
                        self.sheet % len(sheets(self.pages[self.page].items))
                    ]
                )
            )
            try:
                await asyncio.wait_for(self._dirty.wait(), 1 if clock_visible else None)
            except TimeoutError:
                pass
            await asyncio.sleep(0.08)  # Coalesce groups of state events.
            self._dirty.clear()
            if not self.panel.connected:
                continue
            entities = {eid: entity for eid in self.entity_ids if (entity := self.state(eid))}
            if self.detail in entities:
                count = max(1, (len(controls(entities[self.detail])) + 3) // 4)
                self.option_sheet %= count
            screen = render(
                self.pages,
                entities,
                self.page,
                self.sheet,
                self.detail,
                self.option_sheet,
                self.message,
                self.confirm,
                self.histories,
                self.badge_sheet,
                getattr(self.panel, "native_icons", False),
                time_zone=self.time_zone,
                forecasts=self.forecasts,
                dashboard_count=self.dashboard_count,
                idle_dashboard=self._idle_active,
            )
            commands = screen.changed_commands(self._screen if self._last_commands else None)
            if not commands:
                # Actions can change (e.g. numeric targets) without changing any pixels.
                if self._screen.regions != screen.regions:
                    self._generation += 1
                    self._pressed = None
                self._screen = screen
                continue
            self._drawing = True
            self._passive_drawing = bool(
                self._last_commands
                and screen.layout == self._screen.layout
                and screen.regions == self._screen.regions
            )
            if not self._passive_drawing:
                self._pressed = None
                self._generation += 1
            try:
                await self.panel.draw(commands)
            except (OSError, TimeoutError, PanelError) as err:
                _LOGGER.debug("Render interrupted: %s", err)
                self._last_commands = []
            else:
                self._screen = screen
                self._last_commands = screen.commands
            finally:
                self._drawing = False
                self._passive_drawing = False
