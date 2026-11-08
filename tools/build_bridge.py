"""Build a portable desktop application on the target operating system."""

import os
import shutil
import struct
import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def build():
    staging = ROOT / ".cache/bridge-staging"
    staging.mkdir(parents=True, exist_ok=True)
    for source, name in (
        (ROOT / "tjc_bridge", "tjc_bridge"),
        (ROOT / "custom_components/tjc_hmi/display", "tjc_display"),
    ):
        shutil.copytree(
            source,
            staging / name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from tjc_bridge.ui import bridge_icon

    application = QApplication.instance() or QApplication([])
    data = QBuffer()
    data.open(QIODevice.OpenModeFlag.WriteOnly)
    bridge_icon().pixmap(64, 64).save(data, "PNG")
    png = bytes(data.data())
    icon = staging / "bridge.ico"
    icon.write_bytes(struct.pack("<HHHBBBBHHII", 0, 1, 1, 64, 64, 0, 0, 1, 32, len(png), 22) + png)
    del application
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "uBridge",
        "--distpath",
        str(ROOT / ".cache/portable"),
        "--workpath",
        str(ROOT / ".cache/pyinstaller"),
        "--specpath",
        str(ROOT / ".cache/pyinstaller"),
        "--paths",
        str(staging),
        "--icon",
        str(icon),
        "--collect-submodules",
        "keyring.backends",
        "--collect-data",
        "tzdata",
        "--hidden-import",
        "serial.urlhandler.protocol_socket",
        "--add-data",
        f"{staging / 'tjc_display/assets'}{os.pathsep}tjc_display/assets",
        str(ROOT / "tools/bridge_entry.py"),
    ]
    environment = os.environ.copy()
    environment.pop("QT_QPA_PLATFORM", None)
    if sys.platform == "win32":
        # Qt uses the Windows ICU API. Unrelated tools on the developer's PATH
        # can contain an incompatible icuuc.dll, which must not be bundled.
        windows = Path(os.environ["SystemRoot"])
        environment["PATH"] = os.pathsep.join(
            str(path)
            for path in (
                Path(sys.executable).parent,
                Path(sys.base_prefix),
                Path(sys.base_prefix) / "DLLs",
                windows / "System32",
                windows,
            )
        )
    subprocess.run(command, check=True, cwd=ROOT, env=environment)
    output = ROOT / ".cache/portable/uBridge"
    if sys.platform == "win32":
        import PySide6

        (output / "_internal/icuuc.dll").unlink(missing_ok=True)
        qt = Path(PySide6.__file__).parent
        for pattern in ("vcruntime140*.dll", "msvcp140*.dll"):
            for library in qt.glob(pattern):
                shutil.copy2(library, output / "_internal" / library.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    (output / "licenses").mkdir(exist_ok=True)
    if python_license.is_file():
        shutil.copy2(python_license, output / "licenses/CPython-LICENSE.txt")
    packages = (
        "PySide6-Essentials",
        "shiboken6",
        "aiohttp",
        "pyserial",
        "pyserial-asyncio-fast",
        "keyring",
        "tzdata",
        "attrs",
        "multidict",
        "yarl",
        "frozenlist",
        "propcache",
        "aiohappyeyeballs",
        "aiosignal",
        "idna",
        "jaraco.classes",
        "jaraco.context",
        "jaraco.functools",
        "more-itertools",
        "pywin32-ctypes",
        "pyinstaller",
    )
    for package in packages:
        try:
            installed = distribution(package)
        except ModuleNotFoundError:
            continue
        for resource in installed.files or ():
            if any(
                "license" in part.lower() or "copying" in part.lower() for part in resource.parts
            ):
                source = Path(installed.locate_file(resource))
                if source.is_file():
                    destination = output / "licenses" / package / Path(*resource.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    print(f"Built portable application: {output}")


if __name__ == "__main__":
    build()
