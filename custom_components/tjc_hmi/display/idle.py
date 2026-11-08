"""Monotonic idle backlight policy. A wake gesture never triggers an action."""

import asyncio
import logging
import time
from dataclasses import dataclass

from .transport import PanelError

_LOGGER = logging.getLogger(__name__)


@dataclass
class BacklightSettings:
    brightness: int = 80
    idle_enabled: bool = True
    idle_dim_seconds: int = 60
    idle_dim_percent: int = 50
    idle_off_seconds: int = 60
    idle_mode: str = "dim"
    idle_dashboard: str = ""
    idle_brightness: int = 50


class IdleBacklight:
    def __init__(self, panel, settings, *, clock=time.monotonic, on_idle=lambda active: None):
        self.panel, self.settings, self.clock = panel, settings, clock
        self.last_touch = clock()
        self.level = None
        self._consume = False
        self._changed = asyncio.Event()
        self._revision = 0
        self.on_idle = on_idle
        self._idle = False

    def is_idle(self):
        return (
            self.settings.idle_enabled
            and self.clock() - self.last_touch >= self.settings.idle_dim_seconds
        )

    def _notify(self, active):
        if active != self._idle:
            self._idle = active
            self.on_idle(active)

    def connected(self):
        self.last_touch = self.clock()
        self.level = None
        self._consume = False
        self._notify(False)
        self._revision += 1
        self._changed.set()

    def target(self):
        elapsed = self.clock() - self.last_touch
        if not self.settings.idle_enabled or elapsed < self.settings.idle_dim_seconds:
            return self.settings.brightness
        if self.settings.idle_mode == "dashboard":
            return self.settings.idle_brightness
        if elapsed >= self.settings.idle_dim_seconds + self.settings.idle_off_seconds:
            return 0
        return max(1, round(self.settings.brightness * self.settings.idle_dim_percent / 100))

    def touch(self, pressed):
        waking = self.is_idle() or self._idle or self.level != self.settings.brightness
        if pressed and waking:
            self._consume = True
        consumed = waking or self._consume
        self.last_touch = self.clock()
        self._notify(False)
        self._revision += 1
        self._changed.set()
        if not pressed:
            self._consume = False
        return consumed

    async def run(self):
        while True:
            self._changed.clear()
            if self.panel.connected:
                self._notify(self.is_idle())
                target, revision = self.target(), self._revision
                if target != self.level:
                    try:
                        # UART commands are acknowledged and serialized with drawing.
                        await self.panel.brightness(target)
                        self.level = target
                    except (PanelError, OSError, TimeoutError) as err:
                        _LOGGER.debug("Idle brightness interrupted: %s", err)
                    if revision != self._revision:
                        continue
            try:
                await asyncio.wait_for(self._changed.wait(), 0.25)
            except TimeoutError:
                pass
