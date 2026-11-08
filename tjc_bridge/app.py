"""CLI and desktop entry point. The headless bridge does not import Qt."""

import argparse
import asyncio
import importlib
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import CredentialStore, config_path, load_settings


class RedactToken(logging.Filter):
    def __init__(self):
        super().__init__()
        self.token = ""

    def filter(self, record):
        if self.token:
            record.msg = record.getMessage().replace(self.token, "[token]")
            record.args = ()
        return True


def configure_logging(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path.with_name("bridge.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    redaction = RedactToken()
    handler.addFilter(redaction)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return redaction


def main(argv=None):
    parser = argparse.ArgumentParser(description="TJC serial display to Home Assistant API bridge")
    parser.add_argument(
        "--config", type=Path, default=config_path(), help="Non-secret JSON settings"
    )
    parser.add_argument("--configure", action="store_true", help="Open the configuration window")
    parser.add_argument("--background", action="store_true", help="Start minimized when configured")
    parser.add_argument("--headless", action="store_true", help="Run without a desktop/tray")
    parser.add_argument(
        "--diagnostics", type=Path, help="Write offline startup diagnostics and exit"
    )
    parser.add_argument("--version", action="version", version="uBridge 0.8.0")
    args = parser.parse_args(argv)
    path = args.config.expanduser().resolve()
    redaction = configure_logging(path)
    if args.diagnostics:
        import platform

        report = {"version": "0.8.0", "platform": platform.system(), "imports": {}}
        for name in (
            "PySide6.QtCore",
            "PySide6.QtWidgets",
            "aiohttp",
            "serial_asyncio_fast",
            "tjc_bridge.ui",
            "tjc_bridge.releases",
        ):
            try:
                importlib.import_module(name)
                report["imports"][name] = "ok"
            except ImportError as err:
                report["imports"][name] = str(err)
        try:
            report["credential_backend"] = type(CredentialStore()._backend()).__name__
        except Exception as err:
            report["credential_backend"] = str(err)
        args.diagnostics.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0 if all(value == "ok" for value in report["imports"].values()) else 1
    if args.headless:
        from .runtime import Bridge

        try:
            settings = load_settings(path).validated()
            token = os.environ.get("TJC_HA_TOKEN", "") or CredentialStore().get(settings)
            if not token:
                raise ValueError(
                    "Set TJC_HA_TOKEN or save a token using the desktop Configuration window."
                )
            redaction.token = token
            asyncio.run(Bridge(settings, token).run())
        except KeyboardInterrupt:
            return 0
        except Exception as err:
            logging.getLogger(__name__).error("Bridge stopped: %s", err)
            return 1
        return 0
    from .ui import run_desktop

    return run_desktop(path, args.configure, args.background, redaction)
