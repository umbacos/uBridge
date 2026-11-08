"""Small adapter around Home Assistant's internal Lovelace dashboard registry."""

import logging

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.exceptions import HomeAssistantError

from .display.model import Page, parse_dashboard

_LOGGER = logging.getLogger(__name__)


def dashboard_registry(hass):
    data = hass.data.get(LOVELACE_DATA)
    registry = (
        data.get("dashboards", {}) if isinstance(data, dict) else getattr(data, "dashboards", {})
    )
    return {str(url or "lovelace"): dashboard for url, dashboard in registry.items()}


def dashboard_choices(hass):
    return sorted(
        [
            {
                "value": url,
                "label": str(
                    (dashboard.config or {}).get("title")
                    or ("Overview" if url == "lovelace" else url)
                ),
            }
            for url, dashboard in dashboard_registry(hass).items()
            if not (dashboard.config or {}).get("require_admin")
        ],
        key=lambda option: (option["label"].casefold(), option["value"]),
    )


async def load_dashboards(
    hass, selected: list[str], *, force=False
) -> tuple[list[Page], list[str]]:
    registry = dashboard_registry(hass)
    matches, warnings = [], []
    for url in dict.fromkeys(selected):
        dashboard = registry.get(url)
        if dashboard is None:
            warnings.append(f"{url}: dashboard no longer available; update Configuration")
            continue
        metadata = dashboard.config or {}
        title = str(metadata.get("title") or ("Overview" if url == "lovelace" else url))
        if metadata.get("require_admin"):
            warnings.append(f"{title}: require_admin dashboards are skipped")
            continue
        matches.append((url, title, dashboard))
    pages = []
    for url, title, dashboard in matches:
        try:
            config = await dashboard.async_load(force)
            parsed, notes = parse_dashboard(
                url, title, config, (dashboard.config or {}).get("icon")
            )
            pages.extend(parsed)
            warnings.extend(notes)
        except (HomeAssistantError, OSError, ValueError, TypeError) as err:
            warnings.append(f"{title}: cannot load dashboard: {err}")
    return pages, warnings
