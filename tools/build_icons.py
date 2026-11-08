"""Build compact 24px MDI masks. Pillow is a build-time dependency only."""

import gzip
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://raw.githubusercontent.com/Templarian/MaterialDesign-Webfont/v7.4.47/"


def main():
    cache = ROOT / ".cache/mdi-7.4.47"
    cache.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in ("css/materialdesignicons.css", "fonts/materialdesignicons-webfont.ttf", "LICENSE"):
        target = cache / Path(name).name
        if not target.exists():
            with urllib.request.urlopen(BASE + name, timeout=60) as response:
                target.write_bytes(response.read())
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    font = ImageFont.truetype(str(cache / "materialdesignicons-webfont.ttf"), 24)
    icons = {}
    for name, code in re.findall(
        r'\.mdi-([\w-]+)::before\s*\{\s*content:\s*"\\([0-9A-Fa-f]+)"',
        (cache / "materialdesignicons.css").read_text(),
    ):
        bitmap = Image.new("L", (24, 24))
        ImageDraw.Draw(bitmap).text((0, 0), chr(int(code, 16)), font=font, fill=255, anchor="la")
        # Merge identical adjacent horizontal runs into rectangles.
        rectangles, active = [], {}
        for y in range(25):
            runs = []
            x = 0
            while y < 24 and x < 24:
                if bitmap.getpixel((x, y)) < 100:
                    x += 1
                    continue
                start = x
                while x < 24 and bitmap.getpixel((x, y)) >= 100:
                    x += 1
                runs.append((start, x - start))
            for run in list(active):
                if run not in runs:
                    rectangles.append(active.pop(run))
            for run in runs:
                if run in active:
                    active[run][3] += 1
                else:
                    active[run] = [run[0], y, run[1], 1]
        icons[name] = rectangles
    assets = ROOT / "custom_components/tjc_hmi/display/assets"
    assets.mkdir(exist_ok=True)
    payload = json.dumps(icons, separators=(",", ":")).encode()
    (assets / "mdi24.json.gz").write_bytes(gzip.compress(payload, mtime=0))
    (assets / "MDI-LICENSE.txt").write_bytes((cache / "LICENSE").read_bytes())
    with urllib.request.urlopen("https://www.apache.org/licenses/LICENSE-2.0.txt", timeout=60) as r:
        (assets / "Apache-2.0.txt").write_bytes(r.read())
    (assets / "provenance.json").write_text(
        json.dumps(
            {"version": "7.4.47", "source": BASE, "sha256": hashes, "icons": len(icons)}, indent=2
        )
        + "\n"
    )
    print(f"Built {len(icons)} icons ({(assets / 'mdi24.json.gz').stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
