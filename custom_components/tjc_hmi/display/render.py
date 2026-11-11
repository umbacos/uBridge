"""480x272 renderer with independent drawing blocks and native chart primitives."""

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .actions import Action, controls, default_action, explicit_action
from .cards import clock_card, weather_card
from .history import numeric
from .icons import entity_icon, icon_commands
from .layout import sheets
from .model import Entity, Page
from .protocol import display_text

BG, PANEL, TEXT, MUTED, ACCENT, ACTIVE, ERROR = 2145, 6371, 65535, 38066, 1407, 14827, 62024


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int
    event: str
    action: Action | None = None
    entity_id: str | None = None

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


@dataclass
class Screen:
    commands: list[str] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    blocks: dict[str, list[str]] = field(default_factory=dict)
    layout: tuple = ()
    _start: int = 1
    native_icons: bool = False

    def begin(self, key):
        self.finish()
        self._key, self._start = key, len(self.commands)

    def finish(self):
        if hasattr(self, "_key"):
            self.blocks[self._key] = self.commands[self._start :]

    def changed_commands(self, previous):
        if (
            not previous
            or self.layout != previous.layout
            or self.blocks.keys() != previous.blocks.keys()
        ):
            return self.commands
        return [
            command
            for key, commands in self.blocks.items()
            if commands != previous.blocks[key]
            for command in commands
        ]

    def fill(self, x, y, w, h, color=PANEL):
        self.commands.append(f"fill {x},{y},{w},{h},{color}")

    def line(self, x1, y1, x2, y2, color=MUTED):
        self.commands.append(f"line {round(x1)},{round(y1)},{round(x2)},{round(y2)},{color}")

    def icon(self, name, x, y, color=TEXT, bg=PANEL, *, size=24):
        self.commands.extend(
            icon_commands(name, x, y, color, background=bg, native=self.native_icons, size=size)
        )

    def text(self, x, y, w, h, text, fg=TEXT, bg=PANEL, align=0):
        # The bundled font uses 10px fixed-width ASCII glyphs at 20px height.
        value = display_text(text, max(1, w // 10))
        self.commands.append(f'xstr {x},{y},{w},{h},0,{fg},{bg},{align},1,1,"{value}"')

    def button(self, x, y, w, h, label, event, action=None, entity_id=None, color=PANEL):
        self.commands.append(f"fill {x},{y},{w},{h},{color}")
        self.text(x + 3, y, w - 6, h, label, bg=color, align=1)
        self.regions.append(Region(x, y, w, h, event, action, entity_id))


def _render_controls(
    pages: list[Page],
    entities: dict[str, Entity],
    page_index=0,
    sheet=0,
    detail: str | None = None,
    option_sheet=0,
    message="",
    confirm: Action | None = None,
    badge_sheet=0,
    native_icons=False,
    show_page_number=True,
) -> Screen:
    screen = Screen([f"cls {BG}"], native_icons=native_icons)
    if not pages:
        screen.begin("connection")
        screen.text(20, 50, 440, 32, "Home Assistant", ACCENT, BG)
        screen.text(20, 100, 440, 32, message or "Select dashboards in Configuration", bg=BG)
        screen.text(20, 150, 440, 52, "Waiting for configuration / connection", MUTED, BG)
        screen.finish()
        return screen
    page = pages[page_index % len(pages)]
    _header(screen, pages, entities, page_index, badge_sheet, message, show_page_number)
    if confirm:
        screen.begin("confirmation")
        screen.text(15, 60, 450, 30, "Confirm action", ACCENT, BG)
        screen.text(15, 105, 450, 28, confirm.confirmation, bg=BG)
        screen.button(15, 165, 220, 52, "Cancel", "cancel")
        screen.button(245, 165, 220, 52, "Confirm", "confirmed", action=confirm, color=ACTIVE)
    elif detail:
        entity = entities.get(detail, Entity(detail, "unavailable"))
        screen.begin("detail-label")
        screen.text(12, 45, 420, 30, entity.label, bg=BG)
        screen.icon(entity_icon(entity), 440, 48, ACCENT, BG)
        screen.begin("detail-value")
        screen.text(12, 83, 456, 28, entity.value, MUTED, BG)
        actions = controls(entity)
        confirmation = next(
            (
                action.confirmation
                for item in (*page.items, *page.badges)
                if item.entity_id == detail
                and (action := explicit_action(item))
                and action.confirmation
            ),
            None,
        )
        if confirmation:
            actions = [replace(action, confirmation=confirmation) for action in actions]
        start = option_sheet * 4
        screen.begin("detail-controls")
        screen.fill(8, 120, 464, 104, BG)
        for index, action in enumerate(actions[start : start + 4]):
            x = 12 + (index % 2) * 234
            y = 125 + (index // 2) * 51
            screen.button(x, y, 222, 44, action.label, "action", action, detail, color=ACTIVE)
        screen.begin("navigation")
        screen.fill(8, 228, 464, 38, BG)
        screen.button(8, 228, 100, 38, "Back", "back")
        if len(actions) > 4:
            screen.button(
                118,
                228,
                354,
                38,
                f"More options {option_sheet + 1}/{(len(actions) + 3) // 4}",
                "options",
            )
    if message:
        # A toast replaces the header, preserving all tile and navigation hit areas.
        screen.begin("toast")
        screen.text(8, 4, 464, 32, message, ERROR, BG)
    screen.finish()
    return screen


def _gauge(screen, item, entity, x, y, w, h):
    screen.fill(x, y, w, h)
    screen.icon(entity_icon(entity, item.icon), x + 5, y + 2, ACCENT)
    screen.text(x + 33, y + 1, w - 38, 25, item.name or entity.label)
    if item.attribute and entity.available:
        entity = replace(entity, state=str(entity.attributes.get(item.attribute, "unknown")))
    if item.unit is not None:
        entity = replace(entity, attributes={**entity.attributes, "unit_of_measurement": item.unit})
    value = numeric(entity.state) if entity.available else None
    ratio = min(1, max(0, ((value or 0) - item.minimum) / (item.maximum - item.minimum)))
    color = ACCENT
    for threshold, name in item.severity:
        if value is not None and value >= threshold:
            color = {"green": 2016, "yellow": 65504, "red": ERROR}[name]
    if item.kind == "bar":
        bar_y, bar_h = y + 50, max(22, h // 4)
        screen.fill(x + 12, bar_y, w - 24, bar_h, BG)
        if value is not None and ratio > 0:
            screen.fill(x + 12, bar_y, max(1, round((w - 24) * ratio)), bar_h, color)
        screen.text(
            x + 8,
            y + h - 32,
            w - 16,
            26,
            entity.value,
            TEXT if value is not None else ERROR,
            align=1,
        )
    else:
        radius = min(w // 2 - 18, h - 70)
        cx, cy = x + w // 2, y + 30 + radius
        inner = radius - max(12, radius // 6)
        palette = [(t, _rgb565(c), label) for t, c, label in item.segments] if item.needle else []
        if item.needle and not palette:
            palette = [
                (t, {"green": 2016, "yellow": 65504, "red": ERROR}[c], "") for t, c in item.severity
            ]
        for dy in range(-radius, 1):
            run = None
            for dx in range(-radius, radius + 2):
                distance = dx * dx + dy * dy
                pixel = None
                if inner * inner <= distance <= radius * radius:
                    fraction = math.atan2(-dy, -dx) / math.pi
                    pixel = MUTED
                    if value is not None:
                        if item.needle:
                            pixel = ACCENT
                            at = item.minimum + fraction * (item.maximum - item.minimum)
                            for threshold, segment_color, _ in palette:
                                if at >= threshold:
                                    pixel = segment_color
                        elif fraction <= ratio:
                            pixel = color
                if run is not None and pixel != run[1]:
                    screen.fill(cx + run[0], cy + dy, dx - run[0], 1, run[1])
                    run = None
                if pixel is not None and run is None:
                    run = dx, pixel
        if value is not None and item.needle:
            angle = math.pi * ratio
            tip = (cx - (inner - 3) * math.cos(angle), cy - (inner - 3) * math.sin(angle))
            for offset in (-1, 0, 1):
                screen.line(cx + offset, cy, tip[0] + offset, tip[1], TEXT)
        label = entity.value
        if value is not None and item.needle:
            for threshold, _, segment_label in palette:
                if value >= threshold:
                    label = segment_label or entity.value
        screen.text(x + 5, cy + 4, w - 10, 24, label, TEXT if value is not None else ERROR, align=1)
        screen.text(x + 5, cy - 3, 45, 22, f"{item.minimum:g}", MUTED)
        screen.text(x + w - 50, cy - 3, 45, 22, f"{item.maximum:g}", MUTED, align=2)


def _rgb565(value):
    names = {
        "green": "#43a047",
        "yellow": "#ffa600",
        "red": "#db4437",
        "blue": "#039be5",
        "orange": "#ff9800",
        "white": "#ffffff",
        "black": "#000000",
        "var(--success-color)": "#43a047",
        "var(--warning-color)": "#ffa600",
        "var(--error-color)": "#db4437",
        "var(--info-color)": "#039be5",
    }
    value = names.get(value.lower(), value)
    try:
        if value.startswith("#"):
            v = value[1:]
            if len(v) == 3:
                v = "".join(c * 2 for c in v)
            if len(v) != 6:
                return ACCENT
            red, green, blue = (int(v[i : i + 2], 16) for i in (0, 2, 4))
        elif value.startswith("rgb(") and value.endswith(")"):
            red, green, blue = (int(c.strip()) for c in value[4:-1].split(","))
        else:
            return ACCENT
        return (
            (max(0, min(255, red)) >> 3) << 11
            | (max(0, min(255, green)) >> 2) << 5
            | max(0, min(255, blue)) >> 3
        )
    except ValueError:
        return ACCENT


def _chart(screen, item, entities, history, x, y, w, h):
    screen.fill(x, y, w, h)
    screen.icon(item.icon or "mdi:chart-line", x + 5, y + 1, ACCENT)
    title = item.name or entities.get(item.entity_id, Entity(item.entity_id, "unknown")).label
    screen.text(x + 34, y, w - 112, 25, title)
    screen.text(x + w - 76, y, 70, 25, f"{item.hours:g}h", MUTED, align=2)
    points = [
        p
        for values in (history.series.values() if history else [])
        for p in values
        if p.value is not None
    ]
    if not points:
        screen.text(
            x + 10,
            y + 38,
            w - 20,
            30,
            (history.error if history and history.error else "No numeric history"),
            MUTED,
            align=1,
        )
        return
    low, high = min(p.value for p in points), max(p.value for p in points)
    if item.chart_type == "bar":
        low, high = min(0, low), max(0, high)
    if low == high:
        low, high = low - 1, high + 1
    left, right, top, bottom = x + 62, x + w - 10, y + 28, y + h - 49
    screen.text(x + 2, top - 6, 55, 22, f"{high:.3g}", MUTED, align=2)
    screen.text(x + 2, bottom - 17, 55, 22, f"{low:.3g}", MUTED, align=2)
    for fraction in (0, 0.5, 1):
        gy = round(bottom - fraction * (bottom - top))
        screen.line(left, gy, right, gy, BG)
        if fraction == 0.5 and bottom - top >= 60:
            screen.text(x + 2, gy - 10, 55, 22, f"{(low + high) / 2:.3g}", MUTED, align=2)
        tx = round(left + fraction * (right - left))
        stamp = history.start + fraction * (history.end - history.start)
        label = datetime.fromtimestamp(stamp).strftime("%H:%M" if item.hours <= 24 else "%m/%d")
        screen.text(min(right - 50, max(left, tx - 25)), bottom + 2, 50, 22, label, MUTED)
    screen.line(left, top, left, bottom)
    screen.line(left, bottom, right, bottom)
    for index, entity_id in enumerate(item.series):
        color = (1407, 64704, 63519)[index % 3]
        entity = entities.get(entity_id, Entity(entity_id, "unknown"))
        label = (
            entity.label.split(".")[-1]
            + " "
            + str(entity.attributes.get("unit_of_measurement", ""))
        )
        part = (w - 12) // len(item.series)
        screen.text(x + 6 + index * part, y + h - 23, part - 4, 22, label, color)
        values = history.series.get(entity_id, ())
        previous = None
        for point in values:
            if point.value is None:
                previous = None
                continue
            px = round(
                left
                + (right - left)
                * min(1, max(0, (point.time - history.start) / max(1, history.end - history.start)))
            )
            py = round(bottom - (bottom - top) * (point.value - low) / (high - low))
            if item.chart_type == "bar":
                zero = round(bottom - (bottom - top) * (0 - low) / (high - low))
                bw = max(1, min(12, (right - left) // max(1, len(values) * len(item.series))))
                screen.fill(
                    min(right - bw + 1, max(left, px + (index - len(item.series) // 2) * bw)),
                    min(py, zero),
                    bw,
                    max(1, abs(zero - py)),
                    color,
                )
            elif previous:
                screen.line(*previous, px, py, color)
            else:
                screen.fill(px, py, 1, 1, color)
            previous = px, py


def _header(screen, pages, entities, page_index, badge_sheet, message, show_page_number=True):
    page = pages[page_index % len(pages)]
    screen.begin("header")
    screen.fill(0, 0, 480, 39, BG)
    screen.icon(page.icon, 8, 8, ACCENT, BG)
    screen.text(
        38, 5, 100 if page.badges else 335 if show_page_number else 434, 30, page.title, ACCENT, BG
    )
    if show_page_number:
        screen.text(430, 5, 42, 30, f"{page_index + 1}/{len(pages)}", MUTED, BG, 2)
    if page.badges:
        count = 3
        group = badge_sheet % ((len(page.badges) + count - 1) // count)
        for index, badge in enumerate(page.badges[group * count : group * count + count]):
            screen.begin(f"badge{index}-icon")
            entity = entities.get(badge.entity_id, Entity(badge.entity_id, "unavailable"))
            bx = 142 + 94 * index
            screen.fill(bx, 4, 26, 31, BG)
            screen.icon(
                entity_icon(entity, badge.icon), bx, 7, ACCENT if entity.available else ERROR, BG
            )
            screen.begin(f"badge{index}-value")
            value = entity.value
            if numeric(entity.state) is not None:
                value = f"{float(entity.state):.4g}"
                unit = str(entity.attributes.get("unit_of_measurement", "")).replace("°", "")
                if len(value + unit) <= 6:
                    value += unit
            screen.text(bx + 26, 4, 64, 31, value, TEXT if entity.available else ERROR, BG)
            if entity.available and not message:
                action = explicit_action(badge) or Action("More", detail=True)
                screen.regions.append(Region(bx, 4, 90, 31, "action", action, entity.entity_id))
        if len(page.badges) > count and not message:
            screen.regions.append(Region(430, 0, 50, 39, "badges"))
            screen.begin("badge-pager")
            screen.text(
                430, 4, 42, 31, f"B{group + 1}/{(len(page.badges) + 2) // 3}", ACCENT, BG, 2
            )


def render(
    pages: list[Page],
    entities: dict[str, Entity],
    page_index=0,
    sheet=0,
    detail: str | None = None,
    option_sheet=0,
    message="",
    confirm: Action | None = None,
    histories=None,
    badge_sheet=0,
    native_icons=False,
    *,
    now=None,
    time_zone="UTC",
    forecasts=None,
    dashboard_count=None,
    idle_dashboard=False,
) -> Screen:
    if idle_dashboard:
        detail = confirm = None
    if dashboard_count is None:
        dashboard_count = len({page.key.split("/", 1)[0] for page in pages})
    show_counter = not idle_dashboard and dashboard_count > 1
    show_pages = not idle_dashboard and len(pages) > 1
    current_items = pages[page_index % len(pages)].items if pages else ()
    show_items = not idle_dashboard and len(sheets(current_items)) > 1
    show_navigation = show_pages or show_items
    layout = (
        page_index,
        sheet,
        detail,
        option_sheet,
        bool(confirm),
        repr(pages),
        bool(message),
        badge_sheet,
        show_pages,
        show_items,
        show_counter,
    )
    if detail or confirm or not pages:
        screen = _render_controls(
            pages,
            entities,
            page_index,
            sheet,
            detail,
            option_sheet,
            message,
            confirm,
            badge_sheet,
            native_icons,
            show_counter,
        )
        screen.layout = layout
        return screen
    screen = Screen([f"cls {BG}"], layout=(*layout, native_icons), native_icons=native_icons)
    _header(screen, pages, entities, page_index, badge_sheet, message, show_counter)
    page = pages[page_index % len(pages)]
    packed = sheets(page.items, navigation=show_navigation)
    sheet %= len(packed)
    now = now or datetime.now(UTC)
    for index, item, x, y, w, h in packed[sheet]:
        entity = entities.get(item.entity_id, Entity(item.entity_id, "unavailable"))
        screen.begin(f"item{index}-base")
        if item.kind == "clock":
            clock_card(screen, item, x, y, w, h, now, time_zone, f"item{index}")
            continue
        if item.kind == "weather-forecast":
            forecast = (forecasts or {}).get((item.entity_id, item.forecast_type))
            weather_card(screen, item, entity, forecast, x, y, w, h, now, time_zone, f"item{index}")
            action = explicit_action(item)
            if action and entity.available:
                screen.regions.append(Region(x, y, w, h, "action", action, entity.entity_id))
            continue
        if item.kind in {"history-graph", "statistics-graph"}:
            _chart(screen, item, entities, (histories or {}).get(item.history_key), x, y, w, h)
            continue
        if item.kind in {"gauge", "bar"}:
            _gauge(screen, item, entity, x, y, w, h)
            if entity.available:
                screen.regions.append(Region(x, y, w, h, "detail", entity_id=entity.entity_id))
            continue
        color = ACTIVE if entity.state in {"on", "playing", "open", "heat", "cool"} else PANEL
        screen.fill(x, y, w, 27, color)
        screen.icon(entity_icon(entity, item.icon), x + 5, y + 1, bg=color)
        screen.text(x + 34, y + 1, 154, 25, item.name or entity.label, bg=color)
        screen.text(x + 196, y + 1, 26, 25, ">" if entity.available else "", ACCENT, color)
        screen.begin(f"item{index}-value")
        screen.text(x, y + 27, w, h - 27, entity.value, MUTED if entity.available else ERROR, color)
        action = explicit_action(item) or default_action(entity)
        if entity.available:
            screen.regions.append(Region(x, y, 188, h, "action", action, entity.entity_id))
            screen.regions.append(Region(x + 188, y, 40, h, "detail", entity_id=entity.entity_id))
    screen.begin("navigation")
    if not page.items:
        screen.text(12, 80, 456, 40, "Add supported entity cards", bg=BG)
    if show_pages:
        screen.button(8, 228, 106, 38, "< Page", "previous")
    if show_items:
        screen.button(122, 228, 236, 38, f"Items {sheet + 1}/{len(packed)}", "sheet")
    if show_pages:
        screen.button(366, 228, 106, 38, "Page >", "next")
    if message:
        screen.begin("toast")
        screen.text(8, 4, 464, 32, message, ERROR, BG)
    screen.finish()
    return screen
