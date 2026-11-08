"""Generate native RGB565-compatible icon sheets for import into USART HMI.

Run before importing pictures (in numbered order) and compiling the HMI project.
Optionally prioritize icons used by the user's configured HA dashboards, using
the existing bridge credentials only for read-only calls to that HA instance.
"""

import argparse
import asyncio
import gzip
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "custom_components/tjc_hmi/display/assets"
PALETTES = [(65535, 6371), (65535, 14827), (1407, 2145), (1407, 6371), (62024, 2145)]
COLS, PER_SHEET, LIMIT = 20, 220, 7448


async def configured_icons():
    from tjc_display.icons import entity_icon
    from tjc_display.model import Entity, parse_dashboard

    from tjc_bridge.api import HomeAssistantAPI
    from tjc_bridge.config import CredentialStore, config_path, load_settings
    from tjc_bridge.runtime import dashboard_panels

    settings = load_settings(config_path()).validated()
    token = CredentialStore().get(settings)
    if not token:
        raise ValueError("No saved bridge token; omit --from-ha or use --names")
    result = set()
    async with HomeAssistantAPI(settings.base_url, token, settings.ca_file) as api:
        states = {
            state["entity_id"]: Entity(
                state["entity_id"], state["state"], state.get("attributes", {})
            )
            for state in await api.request("get_states")
        }
        panels = await api.request("get_panels")
        for url, title in dashboard_panels(panels, settings.dashboards):
            config = await api.request("lovelace/config", url_path=url)
            pages, _ = parse_dashboard(url, title, config, panels[url].get("icon"))
            for page in pages:
                result.add(page.icon.removeprefix("mdi:"))
                for item in (*page.items, *page.badges):
                    entity = states.get(item.entity_id, Entity(item.entity_id, "unknown"))
                    result.add(entity_icon(entity, item.icon).removeprefix("mdi:"))
    return sorted(result)


def rgb(color):
    return tuple(
        round(value * 255 / maximum)
        for value, maximum in (((color >> 11) & 31, 31), ((color >> 5) & 63, 63), (color & 31, 31))
    )


def build(prioritized):
    masks = json.loads(gzip.decompress((ASSETS / "mdi24.json.gz").read_bytes()))
    names = []

    def add(name):
        name = str(name).removeprefix("mdi:")
        if name in masks and name not in names and len(names) < LIMIT:
            names.append(name)

    mandatory = (
        "help-circle-outline view-dashboard chart-line home home-outline lightbulb "
        "lightbulb-outline thermometer water-percent battery flash lightning-bolt "
        "brightness-6 power-socket-eu eye checkbox-marked-circle thermostat blinds fan "
        "lock speaker palette script-text toggle-switch tune format-list-bulleted "
        "gesture-tap-button account weather-partly-cloudy white-balance-sunny ceiling- "
        "light desk-lamp home-thermometer chart-areaspline "
    ).split()
    for name in [*mandatory, *sorted(prioritized)]:
        add(name)
    # Round robin across common HA families: avoid filling the pack alphabetically.
    roots = (
        "home account air alarm alert arrow battery bed blinds bluetooth brightness "
        "calendar camera car cast ceiling chart checkbox check chevron clock close cloud "
        "coffee compass counter curtain desk desktop device dishwasher door download "
        "earth electric ethernet eye fan faucet file filter fire flash floor flower "
        "folder format fridge garage garden gas gate gauge gesture grid heart heat "
        "heating home house hvac information kettle lamp led light lightbulb lightning "
        "lock map menu microphone motion music network numeric office oil outdoor palette "
        "pause pencil percent phone play playlist plug plus pool power printer radiator "
        "remote robot router run script security server shield shower signal sleep smoke "
        "snowflake sofa solar speaker star stop store sun table television temperature "
        "thermometer timer toggle toilet tools trash tree tune umbrella upload vacuum "
        "valve ventilation volume walk wall washing water weather web wifi window wind "
        "wrench "
    ).split()
    families = [
        [name for name in sorted(masks) if name == root or name.startswith(root + "-")]
        for root in roots
    ]
    depth = 0
    while len(names) < LIMIT and any(depth < len(family) for family in families):
        for family in families:
            if depth < len(family):
                add(family[depth])
        depth += 1
    for name in sorted(masks):
        add(name)
    metadata = {
        "version": 1,
        "columns": COLS,
        "per_sheet": PER_SHEET,
        "names": names,
        "palettes": PALETTES,
        "sheets_per_palette": (len(names) + PER_SHEET - 1) // PER_SHEET,
    }
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    signature = int.from_bytes(hashlib.sha256(canonical).digest()[:4], "little") & 0x7FFFFFFF
    metadata["signature"] = signature
    destination = ROOT / "firmware/icon-atlas"
    destination.mkdir(parents=True, exist_ok=True)
    pictures = []
    for palette, (fg, bg) in enumerate(PALETTES):
        for sheet in range(metadata["sheets_per_palette"]):
            entries = names[sheet * PER_SHEET : (sheet + 1) * PER_SHEET]
            picture = Image.new("RGB", (480, ((len(entries) + COLS - 1) // COLS) * 24), rgb(bg))
            draw = ImageDraw.Draw(picture)
            for slot, name in enumerate(entries):
                x, y = slot % COLS * 24, slot // COLS * 24
                for dx, dy, w, h in masks[name]:
                    draw.rectangle((x + dx, y + dy, x + dx + w - 1, y + dy + h - 1), fill=rgb(fg))
            picture_id = palette * metadata["sheets_per_palette"] + sheet
            path = destination / f"{picture_id:03d}.bmp"
            picture.save(path)
            pictures.append(
                {
                    "id": picture_id,
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    (ASSETS / "icon-atlas.json").write_text(json.dumps(metadata, separators=(",", ":")) + "\n")
    (destination / "manifest.json").write_text(
        json.dumps({**metadata, "pictures": pictures}, indent=2) + "\n"
    )
    print(f"Native pack: {len(names)} icons, {len(pictures)} pictures; sys1={signature}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-ha", action="store_true")
    parser.add_argument("--names", type=Path, help="JSON list of additional mdi: names")
    args = parser.parse_args()
    selected = json.loads(args.names.read_text()) if args.names else []
    if args.from_ha:
        selected.extend(asyncio.run(configured_icons()))
    build(selected)
