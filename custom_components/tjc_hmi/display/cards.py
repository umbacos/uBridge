"""Native full-body clock and weather cards, without additional TFT resources."""

import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .history import numeric

WEATHER_ICONS = {
    "clear-night": "weather-night",
    "cloudy": "weather-cloudy",
    "fog": "weather-fog",
    "hail": "weather-hail",
    "lightning": "weather-lightning",
    "lightning-rainy": "weather-lightning-rainy",
    "partlycloudy": "weather-partly-cloudy",
    "pouring": "weather-pouring",
    "rainy": "weather-rainy",
    "snowy": "weather-snowy",
    "snowy-rainy": "weather-snowy-rainy",
    "sunny": "weather-sunny",
    "windy": "weather-windy",
    "windy-variant": "weather-windy-variant",
    "exceptional": "alert-circle-outline",
}
SEGMENTS = {
    "0": "abcedf",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abfgcd",
    "-": "g",
}
BOXES = {
    "a": (1, 0, 4, 1),
    "b": (5, 1, 1, 4),
    "c": (5, 6, 1, 4),
    "d": (1, 10, 4, 1),
    "e": (0, 6, 1, 4),
    "f": (0, 1, 1, 4),
    "g": (1, 5, 4, 1),
}


def local_time(now, zone):
    try:
        return now.astimezone(ZoneInfo(zone or "UTC")), False
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return now.astimezone(UTC), True


def digit_width(text, scale):
    return sum(4 if c in ".:" else 8 for c in text) * scale


def digits(screen, text, x, y, scale, fg, bg, key):
    for index, char in enumerate(text):
        width = (4 if char in ".:" else 8) * scale
        screen.begin(f"{key}-digit{index}")
        screen.fill(x, y, width, 11 * scale, bg)
        if char in ".:":
            screen.fill(x + scale, y + 8 * scale, scale, scale, fg)
            if char == ":":
                screen.fill(x + scale, y + 3 * scale, scale, scale, fg)
        else:
            for segment in SEGMENTS.get(char, ""):
                dx, dy, w, h = BOXES[segment]
                screen.fill(x + dx * scale, y + dy * scale, w * scale, h * scale, fg)
        x += width


def clock_card(screen, item, x, y, w, h, now, zone, key):
    from .render import ACCENT, BG, ERROR, MUTED, PANEL, TEXT

    now, invalid_zone = local_time(now, item.time_zone or zone)
    bg = BG if item.no_background else PANEL
    screen.fill(x, y, w, h, bg)
    screen.text(x + 10, y + 2, w - 20, 26, item.name or "Clock", ACCENT, bg)
    compact = w < 400
    if item.clock_style == "analog":
        radius = min(77, (h - 30) // 2 - 2)
        cx, cy = x + w // 2, y + 28 + (h - 28) // 2
        if item.clock_border:
            screen.commands.append(f"cir {cx},{cy},{radius},{MUTED}")
        count = {"none": 0, "quarter": 4, "hour": 12, "minute": 60}.get(item.clock_ticks, 12)
        for tick in range(count):
            angle = tick * 2 * math.pi / count
            length = 9 if (tick * 60 // count) % 5 == 0 else 4
            screen.line(
                cx + math.sin(angle) * (radius - length),
                cy - math.cos(angle) * (radius - length),
                cx + math.sin(angle) * (radius - 2),
                cy - math.cos(angle) * (radius - 2),
                TEXT,
            )
        if not compact:
            screen.begin(f"{key}-date")
            screen.text(x + 10, y + 145, 135, 22, now.strftime("%a %d %b"), MUTED, bg)
        screen.begin(f"{key}-hands")
        screen.commands.append(f"cirs {cx},{cy},{radius - 12},{bg}")
        seconds = now.second if item.show_seconds else 0
        for angle, length, color in [
            ((now.hour % 12 + now.minute / 60) * math.pi / 6, radius * 0.52, TEXT),
            ((now.minute + seconds / 60) * math.pi / 30, radius - 18, ACCENT),
            *(([(seconds * math.pi / 30, radius - 16, ERROR)]) if item.show_seconds else []),
        ]:
            screen.line(cx, cy, cx + math.sin(angle) * length, cy - math.cos(angle) * length, color)
        screen.commands.append(f"cirs {cx},{cy},3,{TEXT}")
    else:
        hour = now.hour % 12 or 12 if item.time_format == "12" else now.hour
        text = f"{hour:02d}:{now.minute:02d}"
        scale = (
            {"small": 4, "medium": 4, "large": 5}
            if compact
            else {"small": 5, "medium": 7, "large": 9}
        ).get(item.clock_size, 4 if compact else 5)
        digits(screen, text, x + (w - digit_width(text, scale)) // 2, y + 35, scale, TEXT, bg, key)
        screen.begin(f"{key}-seconds")
        suffix = now.strftime("%p") if item.time_format == "12" else ""
        if compact:
            text = (
                now.strftime("%a %d %b")
                + (f" {now.second:02d}s" if item.show_seconds else "")
                + (f" {suffix}" if suffix else "")
            )
            screen.text(x + 4, y + h - 27, w - 8, 24, text, MUTED, bg, 1)
        else:
            screen.text(
                x + 20,
                y + 137,
                w - 40,
                24,
                f"{now.second:02d} sec {suffix}" if item.show_seconds else suffix,
                ACCENT,
                bg,
                1,
            )
            screen.begin(f"{key}-date")
            screen.text(x + 10, y + h - 27, w - 20, 24, now.strftime("%A %d %B %Y"), MUTED, bg, 1)
    screen.begin(f"{key}-zone")
    if invalid_zone:
        screen.text(x + 5, y + 28, w - 10, 22, "Unknown time zone; showing UTC", ERROR, bg, 1)


def temperature(value, rounded=False):
    value = numeric(value)
    return "--" if value is None else str(round(value)) if rounded else f"{value:g}"


def condition_label(state):
    return "Partly cloudy" if state == "partlycloudy" else state.replace("-", " ").capitalize()


def weather_card(screen, item, entity, forecast, x, y, w, h, zone, key):
    from .render import ACCENT, ERROR, MUTED, PANEL, TEXT

    screen.fill(x, y, w, h)
    screen.begin(f"{key}-title")
    screen.text(x + 10, y + 2, w - 20, 26, item.name or entity.label, ACCENT)
    if not entity.available:
        screen.text(x + 12, y + 70, w - 24, 30, "Weather unavailable", ERROR, align=1)
        return
    a = entity.attributes
    unit = str(a.get("temperature_unit", ""))
    rows = forecast.rows if forecast and not forecast.error else ()
    if w < 400 and item.show_current:
        screen.begin(f"{key}-condition")
        screen.fill(x + 8, y + 30, 24, 24)
        screen.icon(WEATHER_ICONS.get(entity.state, "weather-cloudy"), x + 8, y + 30, ACCENT)
        screen.text(x + 38, y + 30, w - 44, 24, condition_label(entity.state))
        value = temperature(a.get("temperature"), item.round_temperature)
        digits(screen, value if len(value) <= 5 else "--", x + 12, y + 61, 3, TEXT, PANEL, key)
        screen.begin(f"{key}-compact-info")
        screen.text(x + 150, y + 52, w - 156, 22, unit, MUTED)
        screen.text(x + 150, y + 75, w - 156, 22, f"{a.get('humidity', '--')}%", MUTED)
        if item.show_forecast:
            if rows:
                row = rows[0]
                high = temperature(row.get("temperature"), item.round_temperature)
                low = temperature(row.get("templow"), item.round_temperature)
                label = "Next hour" if item.forecast_type == "hourly" else "Next"
                text = (
                    f"{label} {high}/{low}{unit}"
                    if row.get("templow") is not None
                    else f"{label} {high}{unit}"
                )
            else:
                text = "Forecast unavailable" if forecast else "Loading forecast..."
            screen.text(x + 4, y + h - 27, w - 8, 24, text, MUTED, align=1)
        return
    if item.show_current:
        condition = condition_label(entity.state)
        screen.begin(f"{key}-condition")
        screen.fill(x + 12, y + 35, 24, 24)
        screen.icon(WEATHER_ICONS.get(entity.state, "weather-cloudy"), x + 12, y + 35, ACCENT)
        screen.text(x + 45, y + 35, 215, 25, condition)
        value = temperature(a.get("temperature"), item.round_temperature)
        # Bound unusually long provider values to the temperature area.
        value = value if len(value) <= 6 else "--"
        digits(screen, value, x + 268, y + 31, 3, TEXT, PANEL, key)
        screen.begin(f"{key}-current-info")
        screen.text(x + 410, y + 40, 65, 26, unit)
        secondary = item.secondary_info
        if not secondary:
            secondary = "extrema" if rows and rows[0].get("templow") is not None else "humidity"
        if secondary == "extrema" and rows:
            high = temperature(rows[0].get("temperature"), item.round_temperature)
            low = temperature(rows[0].get("templow"), item.round_temperature)
            info = f"High {high} / Low {low} {unit}"
        else:
            units = {
                "humidity": "%",
                "wind_speed": a.get("wind_speed_unit", ""),
                "pressure": a.get("pressure_unit", ""),
                "visibility": a.get("visibility_unit", ""),
                "precipitation": a.get("precipitation_unit", ""),
            }
            info = (
                f"{secondary.replace('_', ' ').capitalize()} "
                f"{a.get(secondary, '--')} {units.get(secondary, '')}"
            )
        screen.text(x + 12, y + 73, w - 24, 24, info, MUTED)
    if not item.show_forecast:
        return
    screen.begin(f"{key}-forecast")
    top = y + (104 if item.show_current else 29 if w < 400 else 53)
    screen.fill(x, top, w, y + h - top)
    if not rows:
        screen.text(
            x + 12,
            y + min(123, h - 34),
            w - 24,
            30,
            (forecast.error if forecast and forecast.error else "No forecast data")
            if forecast
            else "Loading forecast...",
            MUTED,
            align=1,
        )
        return
    columns = 3 if w < 400 else 5
    width = w // columns
    for index, row in enumerate(rows[:columns]):
        left = x + index * width
        try:
            when, _ = local_time(datetime.fromisoformat(str(row.get("datetime", ""))), zone)
            label = when.strftime("%H:%M" if item.forecast_type == "hourly" else "%a %d")
            if item.forecast_type == "twice_daily":
                label = when.strftime("%a") + (" day" if row.get("is_daytime", True) else " night")
        except (ValueError, TypeError):
            label = "--"
        screen.text(left + 2, top, width - 4, 22, label, MUTED, align=1)
        screen.icon(
            WEATHER_ICONS.get(row.get("condition"), "weather-cloudy"),
            left + (width - 24) // 2,
            top + 24,
            ACCENT,
        )
        high = temperature(row.get("temperature"), item.round_temperature)
        low = temperature(row.get("templow"), item.round_temperature)
        screen.text(
            left + 2,
            top + 51,
            width - 4,
            27,
            f"{high}/{low}" if row.get("templow") is not None else f"{high}{unit}",
            TEXT,
            align=1,
        )
