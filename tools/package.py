"""Create the release ZIPs and Windows MSI, keeping only current release artifacts."""

import hashlib
import json
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
OUTPUT = ROOT / "dist"


def files(folder):
    return sorted(
        p
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix not in {".pyc", ".bak"}
        and p.name != "vendor-help-extract.txt"
    )


def write_zip(path, entries):
    checksums = {name: hashlib.sha256(p.read_bytes()).hexdigest() for p, name in entries}
    assert len(checksums) == len(entries), "Duplicate archive paths"
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        for p, name in entries:
            z.write(p, name)
        z.writestr("SHA256SUMS.json", json.dumps(checksums, indent=2) + "\n")
    with ZipFile(path) as z:
        assert z.testzip() is None
        assert all(
            hashlib.sha256(z.read(name)).hexdigest() == digest for name, digest in checksums.items()
        )
    print(path.name, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def package():
    from build_msi import build as build_msi

    portable = ROOT / ".cache/portable/uBridge"
    if not (portable / "uBridge.exe").is_file():
        raise SystemExit("Build the Windows bridge using tools/build_bridge.py first")
    OUTPUT.mkdir(exist_ok=True)
    common = [(ROOT / name, name) for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")]
    install = list(common)
    for folder in ("custom_components", "examples", "docs", "licenses"):
        install.extend((p, p.relative_to(ROOT).as_posix()) for p in files(folder))
    install.extend(
        (ROOT / "firmware" / name, "firmware/" + name) for name in ("tjc_hmi.tft", "build.json")
    )
    install.extend(
        (p, "bridge/" + p.relative_to(portable).as_posix())
        for p in files(".cache/portable/uBridge")
    )
    source = list(common) + [
        (ROOT / "pyproject.toml", "pyproject.toml"),
        (ROOT / ".gitignore", ".gitignore"),
        (ROOT / ".github/workflows/check.yml", ".github/workflows/check.yml"),
    ]
    for folder in (
        "custom_components",
        "examples",
        "firmware",
        "docs",
        "tools",
        "tests",
        "tjc_bridge",
        "licenses",
    ):
        source.extend((p, p.relative_to(ROOT).as_posix()) for p in files(folder))
    install_path = OUTPUT / f"tjc-hmi-{VERSION}-install.zip"
    source_path = OUTPUT / f"tjc-hmi-{VERSION}-source.zip"
    msi_path = build_msi()
    write_zip(install_path, install)
    write_zip(source_path, source)
    # This folder is exclusively generated releases; never traverse outside it.
    for p in OUTPUT.iterdir():
        if p.is_file() and p not in (install_path, source_path, msi_path):
            p.unlink()
    print("Release ZIPs and MSI built; older release artifacts removed.")


if __name__ == "__main__":
    package()
