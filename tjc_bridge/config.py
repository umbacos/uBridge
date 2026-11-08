"""Non-secret settings and operating-system credential storage."""

import json
import os
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from tjc_display.const import BAUD_RATES

# Keep the credential namespace to retain previously stored tokens.
SERVICE = "TJC Bridge"


def config_path() -> Path:
    if sys.platform == "win32":
        return (
            Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
            / "TJCBridge/config.json"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/TJCBridge/config.json"
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tjc-bridge/config.json"
    )


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Enter an http:// or https:// Home Assistant base address.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The base address cannot contain credentials, a query, or a fragment.")
    try:
        _port = parts.port  # Access performs urllib's port validation.
    except ValueError as err:
        raise ValueError("Enter a valid port in the Home Assistant address.") from err
    if any(ord(c) < 33 for c in value.strip()):
        raise ValueError("The base address cannot contain spaces or control characters.")
    path = parts.path.rstrip("/")
    if path.endswith("/api/websocket"):
        path = path.removesuffix("/api/websocket")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass(frozen=True)
class Settings:
    base_url: str = ""
    serial_port: str = ""
    baudrate: int = 115200
    dashboards: list[str] = field(default_factory=list)
    brightness: int = 80
    idle_enabled: bool = True
    idle_dim_seconds: int = 60
    idle_dim_percent: int = 50
    idle_off_seconds: int = 60
    idle_mode: str = "dim"
    idle_dashboard: str = ""
    idle_brightness: int = 50
    remember_token: bool = True
    connect_on_launch: bool = True
    start_at_login: bool = False
    ca_file: str = ""
    credential_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validated(self):
        values = asdict(self)
        values["base_url"] = normalize_url(self.base_url)
        values["serial_port"] = self.serial_port.strip()
        if not values["serial_port"]:
            raise ValueError("Select the serial port attached to the display.")
        if self.baudrate not in BAUD_RATES:
            raise ValueError("Select a supported baud rate.")
        if not isinstance(self.dashboards, list) or any(
            not isinstance(url, str) or not url for url in self.dashboards
        ):
            raise ValueError("Select dashboards from the Home Assistant dashboard list.")
        values["dashboards"] = list(dict.fromkeys(self.dashboards))
        if self.idle_mode not in {"dim", "dashboard"} or not 0 <= self.idle_brightness <= 100:
            raise ValueError("Select an idle mode and a brightness of 0–100%.")
        if self.idle_enabled and self.idle_mode == "dashboard" and not self.idle_dashboard:
            raise ValueError("Select the dashboard to show when idle.")
        if not 1 <= self.brightness <= 100:
            raise ValueError("Backlight brightness must be 1–100%.")
        if not 1 <= self.idle_dim_percent <= 100:
            raise ValueError("Idle brightness must be 1–100% of the configured backlight.")
        if any(not 1 <= value <= 86400 for value in (self.idle_dim_seconds, self.idle_off_seconds)):
            raise ValueError("Idle delays must be 1–86400 seconds.")
        if self.ca_file and not Path(self.ca_file).is_file():
            raise ValueError("The selected TLS certificate file does not exist.")
        return Settings(**values)


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The configuration file must contain a JSON object.")
    allowed = Settings.__dataclass_fields__
    defaults = Settings()
    if any(
        type(value) is not type(getattr(defaults, key))
        for key, value in data.items()
        if key in allowed
    ):
        raise ValueError("Configuration values have invalid types.")
    return Settings(**{key: value for key, value in data.items() if key in allowed})


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(asdict(settings), stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class CredentialStore:
    """Never fall back to a plaintext token file."""

    def _backend(self):
        import keyring

        backend = keyring.get_keyring()
        module = type(backend).__module__
        trusted = (
            "keyring.backends.Windows",
            "keyring.backends.macOS",
            "keyring.backends.SecretService",
            "keyring.backends.kwallet",
            "keyring.backends.libsecret",
        )
        if not module.startswith(trusted):
            # Chainer selects the first usable backend; inspect that backend too.
            backends = getattr(backend, "backends", ())
            if not backends or not type(backends[0]).__module__.startswith(trusted):
                raise RuntimeError("No supported system credential store is available.")
            backend = backends[0]
        return backend

    def get(self, settings: Settings) -> str:
        if not settings.remember_token:
            return ""
        return self._backend().get_password(SERVICE, settings.credential_id) or ""

    def set(self, settings: Settings, token: str) -> None:
        backend = self._backend()
        if settings.remember_token:
            backend.set_password(SERVICE, settings.credential_id, token)
        else:
            import keyring.errors

            try:
                backend.delete_password(SERVICE, settings.credential_id)
            except keyring.errors.PasswordDeleteError:
                pass
