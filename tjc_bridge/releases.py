"""Public GitHub release checks and bounded, verified firmware downloads."""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from zipfile import ZipFile

from . import __version__

REPOSITORY = "https://github.com/umbacos/uBridge"
API = "https://api.github.com/repos/umbacos/uBridge"
RAW = "https://raw.githubusercontent.com/umbacos/uBridge"
DAY = 86400


def installation_marker():
    """MSI records each installation; portable/source runs use their app version."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\uBridge\Installer") as key:
                directory = winreg.QueryValueEx(key, "Directory")[0]
                session = winreg.QueryValueEx(key, "Session")[0]
            if Path(sys.executable).parent.resolve() == Path(directory).resolve():
                return f"{__version__}:{session}"
        except (OSError, TypeError, ValueError):
            pass
    return __version__


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    return tuple(map(int, match.groups())) if match else None


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    tag: str
    assets: tuple = ()


def fetch(url, limit, *, cancelled=lambda: False):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"uBridge/{__version__}",
            "Accept": "application/vnd.github+json" if url.startswith(API) else "*/*",
        },
    )
    started = time.monotonic()
    chunks, length = [], 0
    with urllib.request.urlopen(request, timeout=20) as response:
        while True:
            if cancelled() or time.monotonic() - started > 120:
                raise InterruptedError("Download cancelled or timed out")
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            length += len(chunk)
            if length > limit:
                raise ValueError("Download exceeds the supported size")
            chunks.append(chunk)
    return b"".join(chunks)


def latest_release(*, download=fetch):
    try:
        data = json.loads(download(API + "/releases/latest", 2 * 1024 * 1024))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    tag = data.get("tag_name", "")
    if data.get("draft") or data.get("prerelease") or version_tuple(tag) is None:
        return None
    url = str(data.get("html_url", ""))
    if not url.startswith(REPOSITORY + "/releases/tag/"):
        raise ValueError("Unexpected release address")
    return Release(tag.removeprefix("v"), url, tag, tuple(data.get("assets") or ()))


def newer(release, current=__version__):
    return bool(
        release
        and version_tuple(release.version)
        and version_tuple(current)
        and version_tuple(release.version) > version_tuple(current)
    )


class DesktopState:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def update(self, **changes):
        data = {**self.read(), **changes}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".desktop-", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def due(self, now=None):
        now = time.time() if now is None else now
        previous = self.read().get("last_update_check", 0)
        return not isinstance(previous, (int, float)) or now - previous >= DAY or previous > now


def verify_firmware(data, manifest):
    if manifest.get("model") != "TJC4827X243_011" or manifest.get("series") != "X2":
        raise ValueError("Firmware is for a different display model")
    if not 1024 <= len(data) <= 8 * 1024 * 1024 or len(data) != manifest.get("size"):
        raise ValueError("Firmware download size does not match its manifest")
    if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise ValueError("Firmware download failed SHA256 verification")


def download_firmware(directory, *, download=fetch, progress=lambda text: None):
    release = latest_release(download=download)
    data = manifest = None
    if release:
        assets = [
            asset
            for asset in release.assets
            if str(asset.get("browser_download_url", "")).startswith(
                REPOSITORY + "/releases/download/"
            )
        ]
        zipped = next(
            (asset for asset in assets if str(asset.get("name", "")).endswith("-install.zip")), None
        )
        if zipped:
            progress("Downloading release firmware…")
            raw = download(zipped["browser_download_url"], 128 * 1024 * 1024)
            from io import BytesIO

            with ZipFile(BytesIO(raw)) as archive:
                if archive.getinfo("firmware/tjc_hmi.tft").file_size > 8 * 1024 * 1024:
                    raise ValueError("Firmware archive entry is too large")
                if archive.getinfo("firmware/build.json").file_size > 64 * 1024:
                    raise ValueError("Firmware manifest is too large")
                data = archive.read("firmware/tjc_hmi.tft")
                manifest = json.loads(archive.read("firmware/build.json"))
    if data is None:
        if release:
            reference = quote(release.tag, safe="")
        else:
            # Pin both files to the same immutable revision when no releases exist.
            commit = json.loads(download(API + "/commits/main", 1024 * 1024)).get("sha", "")
            if not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError("Could not resolve the firmware repository revision")
            reference = commit
        progress("Downloading firmware and verification manifest…")
        manifest = json.loads(download(f"{RAW}/{reference}/firmware/build.json", 64 * 1024))
        data = download(f"{RAW}/{reference}/firmware/tjc_hmi.tft", 8 * 1024 * 1024)
    verify_firmware(data, manifest)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"tjc_hmi-{hashlib.sha256(data).hexdigest()[:16]}.tft"
    descriptor, name = tempfile.mkstemp(prefix=".firmware-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    progress("Firmware download verified. Ready to flash.")
    return str(path)
