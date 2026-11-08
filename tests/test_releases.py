"""Release checks, persistent daily scheduling and verified firmware downloads."""

import hashlib
import io
import json
import urllib.error
from zipfile import ZipFile

import pytest

from tjc_bridge.releases import (
    API,
    DAY,
    RAW,
    REPOSITORY,
    DesktopState,
    Release,
    download_firmware,
    latest_release,
    newer,
    verify_firmware,
    version_tuple,
)


def release_data(tag="v1.2.3", **changes):
    return {"tag_name": tag, "html_url": REPOSITORY + "/releases/tag/" + tag, **changes}


def test_stable_versions_and_release_urls():
    release = latest_release(download=lambda *args: json.dumps(release_data()).encode())
    assert release.version == "1.2.3" and newer(release, "1.2.2")
    assert newer(Release("1.10.0", "", ""), "1.9.9")
    assert not newer(release, "1.2.3") and version_tuple("v2.0.0") == (2, 0, 0)
    assert version_tuple("1.2.3-beta") is None
    assert (
        latest_release(download=lambda *args: json.dumps(release_data(prerelease=True)).encode())
        is None
    )
    with pytest.raises(ValueError, match="address"):
        latest_release(
            download=lambda *args: json.dumps(
                release_data(html_url="https://example.test/")
            ).encode()
        )

    def missing(*args):
        raise urllib.error.HTTPError(API, 404, "Not found", None, None)

    assert latest_release(download=missing) is None


def test_daily_state_is_persistent_and_preserves_onboarding(tmp_path):
    path = tmp_path / "desktop.json"
    state = DesktopState(path)
    assert state.due(100000)
    state.update(last_update_check=100000, firmware_prompt_version="0.8.0")
    assert not DesktopState(path).due(100000 + DAY - 1)
    assert DesktopState(path).due(100000 + DAY)
    assert state.due(1)  # Clock correction must not permanently suppress checks.
    state.update(available_release={"version": "1.0.0"})
    assert state.read()["firmware_prompt_version"] == "0.8.0"
    path.write_text("bad json")
    assert state.read() == {}


def firmware():
    data = b"fixture firmware" * 1024
    manifest = {
        "model": "TJC4827X243_011",
        "series": "X2",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return data, manifest


def test_download_without_releases_pins_manifest_and_firmware_to_one_commit(tmp_path):
    data, manifest = firmware()
    commit = "a" * 40
    calls = []

    def download(url, limit):
        calls.append(url)
        if url.endswith("/releases/latest"):
            raise urllib.error.HTTPError(url, 404, "Not found", None, None)
        if url.endswith("/commits/main"):
            return json.dumps({"sha": commit}).encode()
        if url.endswith("build.json"):
            return json.dumps(manifest).encode()
        return data

    result = download_firmware(tmp_path, download=download)
    assert open(result, "rb").read() == data
    assert f"{RAW}/{commit}/firmware/build.json" in calls
    assert f"{RAW}/{commit}/firmware/tjc_hmi.tft" in calls


def test_release_archive_firmware_is_verified_before_replacing_cache(tmp_path):
    data, manifest = firmware()
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("firmware/tjc_hmi.tft", data)
        archive.writestr("firmware/build.json", json.dumps(manifest))
    url = REPOSITORY + "/releases/download/v1.2.3/uBridge-install.zip"
    metadata = release_data(assets=[{"name": "uBridge-install.zip", "browser_download_url": url}])
    result = download_firmware(
        tmp_path,
        download=lambda url, limit: (
            buffer.getvalue() if url.endswith(".zip") else json.dumps(metadata).encode()
        ),
    )
    assert open(result, "rb").read() == data
    # A failed future download must leave the existing verified firmware intact.
    manifest["sha256"] = "0" * 64
    bad = io.BytesIO()
    with ZipFile(bad, "w") as archive:
        archive.writestr("firmware/tjc_hmi.tft", b"wrong" * 1024)
        archive.writestr("firmware/build.json", json.dumps(manifest))
    with pytest.raises(ValueError):
        download_firmware(
            tmp_path,
            download=lambda url, limit: (
                bad.getvalue() if url.endswith(".zip") else json.dumps(metadata).encode()
            ),
        )
    assert open(result, "rb").read() == data


@pytest.mark.parametrize(
    "field,value", [("model", "other"), ("series", "X3"), ("size", 100), ("sha256", "invalid")]
)
def test_wrong_model_size_or_hash_is_rejected(field, value):
    data, manifest = firmware()
    manifest[field] = value
    with pytest.raises(ValueError):
        verify_firmware(data, manifest)
