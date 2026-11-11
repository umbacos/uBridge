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


def digits(screen, text, x, y, scale, fg, bg, key=None):
    for index, char in enumerate(text):
        width = (4 if char in ".:" else 8) * scale
        if key is not None:
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
        suffix = now.strftime("%p") if item.time_format == "12" else ""
        sizes = (
            {"small": 5, "medium": 6, "large": 7}
            if compact
            else {"small": 8, "medium": 10, "large": 12}
        )
        body_top = y + 28 + (22 if invalid_zone else 0)
        body_bottom = y + h - 27
        if not compact and (suffix or item.show_seconds):
            body_bottom -= 22
        scale = min(
            sizes.get(item.clock_size, sizes["small"]),
            (w - 12) // digit_width(text, 1),
            (body_bottom - body_top) // 11,
        )
        digits(
            screen,
            text,
            x + (w - digit_width(text, scale)) // 2,
            body_top + (body_bottom - body_top - 11 * scale) // 2,
            scale,
            TEXT,
            bg,
            key,
        )
        screen.begin(f"{key}-seconds")
        if compact:
            text = (
                now.strftime("%a %d %b")
                + (f" {now.second:02d}s" if item.show_seconds else "")
                + (f" {suffix}" if suffix else "")
            )
            screen.text(x + 4, y + h - 27, w - 8, 24, text, MUTED, bg, 1)
        elif suffix or item.show_seconds:
            screen.text(
                x + 20,
                body_bottom,
                w - 40,
                22,
                f"{now.second:02d} sec {suffix}" if item.show_seconds else suffix,
                ACCENT,
                bg,
                1,
            )
        if not compact:
            screen.begin(f"{key}-date")
            screen.text(x + 10, y + h - 27, w - 20, 24, now.strftime("%A %d %B %Y"), MUTED, bg, 1)
    screen.begin(f"{key}-zone")
    if invalid_zone:
        message = "Using UTC (bad zone)" if compact else "Unknown time zone; showing UTC"
        screen.text(x + 5, y + 28, w - 10, 22, message, ERROR, bg, 1)


def temperature(value, rounded=False):
    value = numeric(value)
    return "--" if value is None else str(round(value)) if rounded else f"{value:g}"


def degree_mark(screen, x, y, radius, color):
    # Font 0 is ASCII only. Draw the degree sign with a native circle.
    screen.commands.append(f"cir {x + radius},{y + radius},{radius},{color}")


def weather_temperatures(screen, x, y, width, height, values, color, bg, *, right=False):
    separator = 8
    degree_width = 6
    marks = sum(value != "--" for value in values)
    gap_width = degree_width * marks + separator * (len(values) - 1)
    # Keep both readings visible when a compact header has long temperatures.
    compact_digits = sum(len(value) * 10 for value in values) + gap_width > width and all(
        char in "0123456789.-" for value in values for char in value
    )
    widths = [digit_width(value, 1) if compact_digits else len(value) * 10 for value in values]
    available = width - sum(widths) - gap_width
    if available < 0:
        screen.text(x, y, width, height, "--", color, bg, align=2 if right else 1)
        return
    left = x + (available if right else available // 2)
    for index, (value, value_width) in enumerate(zip(values, widths, strict=True)):
        if index:
            middle = y + height // 2
            screen.line(left + 1, middle + 5, left + 5, middle - 5, color)
            left += separator
        if compact_digits:
            digits(screen, value, left, y + (height - 11) // 2, 1, color, bg)
        else:
            screen.text(left, y, value_width, height, value, color, bg)
        left += value_width
        if value != "--":
            degree_mark(screen, left, y + (height - 20) // 2 + 3, 2, color)
            left += degree_width


def condition_label(state):
    return "Partly cloudy" if state == "partlycloudy" else state.replace("-", " ").capitalize()


def weather_card(screen, item, entity, forecast, x, y, w, h, now, zone, key):
    from .render import ACCENT, ERROR, MUTED, PANEL, TEXT

    screen.fill(x, y, w, h)
    if not entity.available:
        screen.text(x + 12, y + (h - 30) // 2, w - 24, 30, "Weather unavailable", ERROR, align=1)
        return

    attributes = entity.attributes
    local_now, _ = local_time(now, zone)
    today = local_now.date()
    today_row = {}
    upcoming = []
    if item.show_forecast and forecast and not forecast.error:
        for row in forecast.rows:
            try:
                when = datetime.fromisoformat(str(row.get("datetime", "")))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=local_now.tzinfo)
            when = when.astimezone(local_now.tzinfo)
            if when.date() == today and not today_row and numeric(row.get("templow")) is not None:
                today_row = row
            if item.forecast_type == "hourly" or when.date() > today:
                upcoming.append((when, row))
        upcoming.sort(key=lambda entry: entry[0])
    compact = w < 400
    header_height = 42 if compact else 68
    top = y + 28
    if item.show_current:
        header_y = y if item.show_forecast else y + (h - header_height) // 2
        split = x + w - (88 if compact else 192)
        label_x = x + (40 if compact else 52)
        padding = 5 if compact else 12
        screen.begin(f"{key}-condition")
        screen.fill(x, header_y, split - x, header_height)
        screen.icon(
            WEATHER_ICONS.get(entity.state, "weather-cloudy"),
            x + padding,
            header_y + (header_height - 32) // 2,
            ACCENT,
            size=32,
        )
        condition = condition_label(entity.state)
        label_width = split - label_x
        if compact and len(condition) > label_width // 10 and " " in condition:
            first, second = condition.split(" ", 1)
            screen.text(label_x, header_y + 1, label_width, 20, first)
            screen.text(label_x, header_y + 21, label_width, 20, second)
        else:
            screen.text(label_x, header_y, label_width, header_height, condition)

        screen.begin(f"{key}-current-info")
        right = x + w - padding
        width = right - split
        screen.fill(split, header_y, x + w - split, header_height)
        value = temperature(attributes.get("temperature"), item.round_temperature)
        scale = 2 if compact else 4
        value_width = digit_width(value, scale)
        value_height = 11 * scale
        radius = scale
        degree_width = 2 * radius + 2 if value != "--" else 0
        if value_width <= width - degree_width and all(c in "0123456789.-" for c in value):
            digits(
                screen,
                value,
                right - degree_width - value_width,
                header_y,
                scale,
                TEXT,
                PANEL,
            )
            if degree_width:
                degree_mark(screen, right - degree_width, header_y + 1, radius, TEXT)
        else:
            weather_temperatures(
                screen, split, header_y, width, value_height, (value,), TEXT, PANEL, right=True
            )

        secondary = item.secondary_info or ("extrema" if today_row else "humidity")
        info_y = header_y + value_height
        if secondary == "extrema" and today_row:
            high = temperature(today_row.get("temperature"), item.round_temperature)
            low = temperature(today_row.get("templow"), item.round_temperature)
            weather_temperatures(
                screen, split, info_y, width, 20, (high, low), MUTED, PANEL, right=True
            )
        elif secondary != "extrema":
            units = {
                "humidity": "%",
                "wind_speed": attributes.get("wind_speed_unit", ""),
                "pressure": attributes.get("pressure_unit", ""),
                "visibility": attributes.get("visibility_unit", ""),
                "precipitation": attributes.get("precipitation_unit", ""),
            }
            info = f"{attributes.get(secondary, '--')} {units.get(secondary, '')}".strip()
            if not compact:
                info = f"{secondary.replace('_', ' ').capitalize()} {info}"
            screen.text(split, info_y, width, 20, info, MUTED, align=2)
        top = y + header_height
    else:
        screen.text(x + 10, y + 2, w - 20, 26, item.name or "Weather forecast", ACCENT)

    if not item.show_forecast:
        return
    screen.begin(f"{key}-forecast")
    height = y + h - top
    screen.fill(x, top, w, height)
    if not upcoming:
        message = (forecast.error or "No forecast data") if forecast else "Loading forecast..."
        screen.text(x + 4, top + (height - 20) // 2, w - 8, 20, message, MUTED, align=1)
        return

    # A compact card has room for three columns, including separate high and low rows.
    columns = min(len(upcoming), 3 if compact else 5)
    top += (height - 84) // 2
    for index, (when, row) in enumerate(upcoming[:columns]):
        left = x + index * w // columns
        width = x + (index + 1) * w // columns - left
        label = when.strftime("%H:%M" if item.forecast_type == "hourly" else "%a")
        if item.forecast_type == "twice_daily":
            label += " day" if row.get("is_daytime", True) else " night"
        screen.text(left, top, width, 20, label, MUTED, align=1)
        screen.icon(
            WEATHER_ICONS.get(row.get("condition"), "weather-cloudy"),
            left + (width - 24) // 2,
            top + 20,
            ACCENT,
        )
        high = temperature(row.get("temperature"), item.round_temperature)
        weather_temperatures(screen, left, top + 44, width, 20, (high,), TEXT, PANEL)
        if numeric(row.get("templow")) is not None:
            low = temperature(row.get("templow"), item.round_temperature)
            weather_temperatures(screen, left, top + 64, width, 20, (low,), MUTED, PANEL)
