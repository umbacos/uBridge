"""User-scoped login startup, enabled only by the configuration checkbox."""

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path


def launch_command(config: Path):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--background", "--config", str(config)]
    executable = sys.executable
    if sys.platform == "win32" and Path(executable).name.lower() == "python.exe":
        executable = str(Path(executable).with_name("pythonw.exe"))
    # Login starts with a different working directory. Source checkouts must be
    # importable there too; installed and frozen builds remain independent of it.
    entry = str(Path(__file__).with_name("startup.py"))
    return [executable, entry, "--background", "--config", str(config)]


def desktop_quote(value):
    # Desktop Entry Exec has its own quoting rules; it is not a shell command.
    return (
        '"'
        + value.replace("%", "%%")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        + '"'
    )


def set_enabled(enabled: bool, config: Path):
    command = launch_command(config.resolve())
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
        ) as key:
            try:
                winreg.DeleteValue(key, "TJCBridge")  # Replace the previous application name.
            except FileNotFoundError:
                pass
            if enabled:
                winreg.SetValueEx(
                    key, "uBridge", 0, winreg.REG_SZ, subprocess.list2cmdline(command)
                )
            else:
                try:
                    winreg.DeleteValue(key, "uBridge")
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        (Path.home() / "Library/LaunchAgents/io.tjc.bridge.plist").unlink(missing_ok=True)
        path = Path.home() / "Library/LaunchAgents/io.ubridge.desktop.plist"
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                plistlib.dumps(
                    {"Label": "io.ubridge.desktop", "ProgramArguments": command, "RunAtLoad": True}
                )
            )
        else:
            path.unlink(missing_ok=True)
    else:
        directory = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
        (directory / "tjc-bridge.desktop").unlink(missing_ok=True)
        path = directory / "ubridge.desktop"
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=uBridge\n"
                "Comment=Connect a TJC display to Home Assistant\n"
                f"Exec={' '.join(desktop_quote(value) for value in command)}\n"
                "Terminal=false\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        else:
            path.unlink(missing_ok=True)


def display_command(config: Path):
    return shlex.join(launch_command(config))
