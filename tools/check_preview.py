"""Browser smoke test against a running local preview (playwright + Chrome)."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main(port=8765):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1080})
        page.goto(f"http://127.0.0.1:{port}")
        page.wait_for_function(
            "document.querySelector('#status').textContent==='Preview connected'"
        )
        page.screenshot(path=str(ROOT / "docs/preview.png"))
        canvas = page.locator("canvas")
        box = canvas.bounding_box()

        def tap(x, y):
            page.mouse.click(box["x"] + x * box["width"] / 480, box["y"] + y * box["height"] / 272)
            page.wait_for_timeout(250)

        before = page.evaluate("async () => (await (await fetch('/api/screen')).json()).commands")
        tap(230, 245)  # The unused Items slot must have no label or touch action.
        assert (
            page.evaluate("async () => (await (await fetch('/api/screen')).json()).commands")
            == before
        )
        tap(30, 60)
        assert "light.toggle" in page.locator("#log").inner_text()
        tap(420, 245)
        tap(30, 60)
        tap(300, 145)
        assert "climate.set_temperature" in page.locator("#log").inner_text()
        page.screenshot(path=str(ROOT / "docs/preview-climate.png"))
        tap(40, 245)
        tap(420, 245)
        tap(280, 60)
        tap(280, 145)
        assert "input_select.select_option" in page.locator("#log").inner_text()
        tap(40, 245)
        tap(280, 120)
        assert "script.turn_on" not in page.locator("#log").inner_text()
        tap(300, 190)
        assert "script.turn_on" in page.locator("#log").inner_text()
        tap(420, 245)
        page.screenshot(path=str(ROOT / "docs/preview-gauges.png"))
        tap(230, 245)
        page.screenshot(path=str(ROOT / "docs/preview-bars.png"))
        tap(420, 245)
        page.screenshot(path=str(ROOT / "docs/preview-history.png"))
        tap(420, 245)
        page.screenshot(path=str(ROOT / "docs/preview-statistics.png"))
        tap(420, 245)
        mixed = page.evaluate("async () => (await (await fetch('/api/screen')).json()).commands")
        assert not any("Items 1/1" in command for command in mixed)
        page.screenshot(path=str(ROOT / "docs/preview-clock-weather.png"))
        canvas.screenshot(path=str(ROOT / "docs/preview-clock-weather-display.png"))
        tap(30, 190)
        assert page.locator("#log").inner_text().count("light.toggle") >= 2
        tap(450, 190)
        page.screenshot(path=str(ROOT / "docs/preview-clock-weather-detail.png"))
        assert not page.locator("#status").inner_text().startswith("Preview disconnected")
        browser.close()
    print("Browser preview passed: toggle, page navigation, thermostat, selection, confirmation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    main(parser.parse_args().port)
