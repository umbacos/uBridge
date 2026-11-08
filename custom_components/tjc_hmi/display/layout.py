"""Keep dashboard order; rich cards get the full body between header and navigation."""

from .model import Item

FULL_BODY = (0, 39, 480, 189)
RICH_CARDS = {"gauge", "bar", "history-graph", "statistics-graph", "clock", "weather-forecast"}


def sheets(items: tuple[Item, ...], *, navigation=True):
    bottom = 228 if navigation else 272
    clocks = [(i, item) for i, item in enumerate(items) if item.kind == "clock"]
    weather = [(i, item) for i, item in enumerate(items) if item.kind == "weather-forecast"]
    tiles = [(i, item) for i, item in enumerate(items) if item.kind == "tile"]
    if len(clocks) == len(weather) == 1 and len(tiles) <= 2 and len(items) == 2 + len(tiles):
        height = bottom - 102 if tiles else bottom - 47
        placed = [(*clocks[0], 8, 43, 228, height), (*weather[0], 244, 43, 228, height)]
        placed.extend(
            (i, item, 8 + position * 236, bottom - 53, 228 if len(tiles) == 2 else 464, 49)
            for position, (i, item) in enumerate(tiles)
        )
        return [placed]
    result, placed = [], []
    for index, item in enumerate(items):
        if item.kind in RICH_CARDS:
            if placed:
                result.append(placed)
                placed = []
            result.append([(index, item, 0, 39, 480, bottom - 39)])
            continue
        cell = len(placed)
        placed.append((index, item, 8 + 236 * (cell % 2), 43 + 59 * (cell // 2), 228, 53))
        if len(placed) == 6:
            result.append(placed)
            placed = []
    if placed:
        result.append(placed)
    return result or [[]]
