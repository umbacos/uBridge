"""Shared forecast polling; forecasts are separate from weather entity state in HA."""

import asyncio
import logging
import time
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass
class Forecast:
    rows: tuple = ()
    error: str = ""


def decode_forecast(response, entity_id):
    payload = response.get(entity_id, {}) if isinstance(response, dict) else {}
    rows = payload.get("forecast") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return Forecast(error="Forecast unavailable")
    return Forecast(tuple(dict(row) for row in rows[:50] if isinstance(row, dict)))


async def forecast_loop(controller, fetch):
    fetched = {}
    while True:
        current = controller.forecast_requests
        fetched = {key: when for key, when in fetched.items() if key in current}
        for key in current:
            if time.monotonic() - fetched.get(key, -1000) < 600:
                continue
            try:
                data = decode_forecast(await fetch(*key), key[0])
            except Exception as err:
                # Provider/service failures must not take down the display connection.
                _LOGGER.debug("Weather forecast unavailable (%s)", type(err).__name__)
                data = Forecast(error="Forecast unavailable")
            if key in controller.forecast_requests:
                controller.forecasts[key] = data
                controller.invalidate()
            fetched[key] = time.monotonic()
        await asyncio.sleep(1)
