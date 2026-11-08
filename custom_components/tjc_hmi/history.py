"""Optional recorder adapter. All database work stays on HA's recorder executor."""

from datetime import UTC, datetime
from functools import partial

from .display.history import HistoryData, decode_history


async def chart_data(hass, item, end):
    start = end - item.hours * 3600
    if "recorder" not in hass.config.components:
        return HistoryData(start, end, error="Recorder not enabled")
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.components.recorder.statistics import statistics_during_period

    times = datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end, UTC)
    if item.kind == "statistics-graph":
        query = partial(
            statistics_during_period,
            hass,
            *times,
            set(item.series),
            item.period,
            None,
            {item.statistic},
        )
    else:
        query = partial(
            get_significant_states,
            hass,
            *times,
            list(item.series),
            None,
            True,
            False,
            True,
            True,
            True,
        )
    raw = await get_instance(hass).async_add_executor_job(query)
    return decode_history(
        raw, start, end, statistic=item.statistic if item.kind == "statistics-graph" else None
    )
