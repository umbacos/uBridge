"""Check the direct integration's chart adapter with an isolated HA SQLite recorder."""

import asyncio
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader  # noqa: E402
from homeassistant.components.recorder import get_instance  # noqa: E402
from homeassistant.components.recorder.models import StatisticMeanType  # noqa: E402
from homeassistant.components.recorder.statistics import async_add_external_statistics  # noqa: E402
from homeassistant.config_entries import ConfigEntries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers.recorder import DATA_RECORDER, RecorderData  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

from custom_components.tjc_hmi.display.model import Item  # noqa: E402
from custom_components.tjc_hmi.history import chart_data  # noqa: E402


async def main():
    with tempfile.TemporaryDirectory(prefix="tjc-recorder-check-") as directory:
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        hass.config.time_zone = "Europe/Rome"
        # Normally created by HA bootstrap before integration setup.
        hass.data[DATA_RECORDER] = RecorderData()
        try:
            assert await async_setup_component(
                hass,
                "recorder",
                {"recorder": {"db_url": f"sqlite:///{directory}/history.db", "commit_interval": 0}},
            )
            await hass.async_start()
            recorder = get_instance(hass)
            hass.states.async_set("sensor.tjc_test", "21", {"unit_of_measurement": "°C"})
            await hass.async_block_till_done()
            await recorder.async_block_till_done()
            hass.states.async_set("sensor.tjc_test", "22", {"unit_of_measurement": "°C"})
            await hass.async_block_till_done()
            await recorder.async_block_till_done()
            item = Item(
                "sensor.tjc_test", kind="history-graph", series=("sensor.tjc_test",), hours=1
            )
            data = await chart_data(hass, item, time.time())
            values = [point.value for point in data.series["sensor.tjc_test"]]
            assert 21 in values and 22 in values, values
            start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
                hours=2
            )
            async_add_external_statistics(
                hass,
                {
                    "has_sum": False,
                    "name": "TJC chart test",
                    "source": "tjc_hmi",
                    "statistic_id": "tjc_hmi:test_temperature",
                    "unit_of_measurement": "°C",
                    "unit_class": "temperature",
                    "mean_type": StatisticMeanType.ARITHMETIC,
                },
                [{"start": start, "mean": 20.5, "min": 19.0, "max": 22.0}],
            )
            await recorder.async_block_till_done()
            await recorder.async_add_executor_job(recorder.block_till_done)
            statistics = Item(
                "sensor.tjc_test",
                kind="statistics-graph",
                series=("tjc_hmi:test_temperature",),
                hours=24,
                statistic="mean",
            )
            stats = await chart_data(hass, statistics, time.time())
            assert any(point.value == 20.5 for point in stats.series["tjc_hmi:test_temperature"]), (
                stats
            )
            print("PASS: HA 2026.10.4 SQLite recorder -> compressed history -> chart samples")
            print("PASS: HA 2026.10.4 statistics -> real hourly mean -> chart samples")
        finally:
            await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
