"""Interactive loopback-only display simulator using the production controller."""

import argparse
import asyncio
import json
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core_loader import load_core

ROOT = load_core()

from tjc_hmi.controller import Controller  # noqa: E402
from tjc_hmi.history import HistoryData, Sample  # noqa: E402
from tjc_hmi.model import Entity, parse_dashboard  # noqa: E402
from tjc_hmi.protocol import Frame  # noqa: E402
from tjc_hmi.weather import Forecast  # noqa: E402


class MockPanel:
    connected = True

    def __init__(self):
        self.commands = []

    async def draw(self, commands):
        self.commands = list(commands)


class PreviewServer(ThreadingHTTPServer):
    # Windows otherwise permits two preview processes to share the same port.
    allow_reuse_address = False


async def main(port):
    import yaml

    panel = MockPanel()
    entities = {
        "light.living_room": Entity(
            "light.living_room",
            "on",
            {
                "friendly_name": "Living room",
                "supported_color_modes": ["brightness"],
                "brightness": 128,
            },
        ),
        "switch.desk": Entity("switch.desk", "off", {"friendly_name": "Desk"}),
        "sensor.room_temperature": Entity(
            "sensor.room_temperature",
            "21.4",
            {"unit_of_measurement": "°C", "friendly_name": "Indoor", "device_class": "temperature"},
        ),
        "sensor.room_humidity": Entity(
            "sensor.room_humidity", "48", {"unit_of_measurement": "%", "device_class": "humidity"}
        ),
        "scene.evening": Entity("scene.evening", "ready"),
        "input_boolean.guest_mode": Entity("input_boolean.guest_mode", "off"),
        "climate.living_room": Entity(
            "climate.living_room",
            "heat",
            {
                "friendly_name": "Living room",
                "current_temperature": 21.4,
                "temperature": 22,
                "target_temp_step": 0.5,
                "supported_features": 1,
            },
        ),
        "cover.blinds": Entity("cover.blinds", "closed", {"supported_features": 11}),
        "sensor.outdoor_temperature": Entity(
            "sensor.outdoor_temperature",
            "12.8",
            {
                "unit_of_measurement": "°C",
                "friendly_name": "Outdoor",
                "device_class": "temperature",
            },
        ),
        "input_number.target_level": Entity(
            "input_number.target_level", "50", {"min": 0, "max": 100, "step": 5}
        ),
        "input_select.home_mode": Entity(
            "input_select.home_mode",
            "Home",
            {"options": ["Home", "Away", "Guests", "Evening", "Holiday"]},
        ),
        "media_player.living_room": Entity(
            "media_player.living_room",
            "playing",
            {"supported_features": 16389, "volume_level": 0.4},
        ),
        "script.goodnight": Entity("script.goodnight", "off"),
    }
    entities["weather.forecast_home"] = Entity(
        "weather.forecast_home",
        "partlycloudy",
        {
            "friendly_name": "Home weather",
            "temperature": 18.5,
            "temperature_unit": "C",
            "humidity": 64,
        },
    )
    history = []

    async def service(action):
        history.append(
            {
                "action": f"{action.domain}.{action.service}",
                "target": action.target,
                "data": action.data,
            }
        )
        entity = entities.get(action.target.get("entity_id"))
        if not entity:
            return
        if action.service == "toggle":
            entity.state = "off" if entity.state == "on" else "on"
        elif action.service == "set_temperature":
            entity.attributes.update(action.data)
        elif action.service in {"select_option", "set_value"}:
            entity.state = str(action.data.get("option", action.data.get("value")))
        elif action.service == "turn_on":
            entity.state = "on"
        elif action.service in {"open_cover", "close_cover", "stop_cover"}:
            entity.state = {"open_cover": "open", "close_cover": "closed", "stop_cover": "stopped"}[
                action.service
            ]
        elif action.service == "media_play_pause":
            entity.state = "paused" if entity.state == "playing" else "playing"
        elif action.service == "volume_set":
            entity.attributes.update(action.data)
        controller.invalidate()

    controller = Controller(panel, service, entities.get)
    pages = []
    for slug, title in [
        ("home", "Home"),
        ("climate", "Climate"),
        ("controls", "Controls"),
        ("charts", "Charts"),
        ("clock-weather", "Clock and weather"),
    ]:
        config = yaml.safe_load(
            (ROOT / f"examples/dashboards/{slug}.yaml").read_text(encoding="utf-8")
        )
        page, _ = parse_dashboard(f"{slug}-panel", title, config)
        pages.extend(page)
    controller.update_pages(pages)
    controller.time_zone = "Europe/Rome"
    controller.forecasts[("weather.forecast_home", "daily")] = Forecast(
        tuple(
            {
                "datetime": f"2026-11-{8 + i:02d}T12:00:00+00:00",
                "condition": "sunny",
                "temperature": 19 + i,
                "templow": 10 + i,
            }
            for i in range(5)
        )
    )
    end = time.time()
    for key, item in controller.history_requests.items():
        start = end - item.hours * 3600
        series = {}
        for index, eid in enumerate(item.series):
            count = 24 if item.kind == "statistics-graph" else 80
            series[eid] = tuple(
                Sample(
                    start + (end - start) * step / (count - 1),
                    21 - index * 8 + math.sin(step / 5) * (1 + index) + math.cos(step / 11),
                )
                for step in range(count)
            )
        controller.histories[key] = HistoryData(start, end, series)
    controller.start()
    loop = asyncio.get_running_loop()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/api/screen":
                body = json.dumps(
                    {"commands": controller._screen.commands, "history": history[-6:]}
                ).encode()
                kind = "application/json"
            elif self.path == "/":
                body = (ROOT / "tools/preview.html").read_bytes()
                kind = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/api/touch":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 128:
                    raise ValueError
                value = json.loads(self.rfile.read(length))
                x, y = int(value["x"]), int(value["y"])
                if not 0 <= x < 480 or not 0 <= y < 272:
                    raise ValueError
                frame = Frame(
                    bytes([0x67])
                    + x.to_bytes(2, "big")
                    + y.to_bytes(2, "big")
                    + bytes([bool(value["pressed"])])
                )
                loop.call_soon_threadsafe(controller.frame, frame)
            except (KeyError, ValueError, TypeError):
                self.send_error(400)
                return
            self.send_response(204)
            self.end_headers()

    server = PreviewServer(("127.0.0.1", port), Handler)
    print(f"Preview: http://127.0.0.1:{port} (mock entities, no device actions)", flush=True)
    try:
        await asyncio.to_thread(server.serve_forever)
    finally:
        await controller.stop()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    asyncio.run(main(parser.parse_args().port))
