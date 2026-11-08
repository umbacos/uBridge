"""The connection label includes native/vector mode, without changing readiness."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from tjc_bridge import ui  # noqa: E402


@pytest.mark.parametrize(
    "mode", ["Connected (native icons)", "Connected (vector icons)", "Disconnected"]
)
def test_tray_status_accepts_both_connected_icon_modes(monkeypatch, mode):
    colors = []
    monkeypatch.setattr(ui, "bridge_icon", lambda color: color)
    desktop = SimpleNamespace(
        window=SimpleNamespace(show_status=lambda status: None),
        tray=SimpleNamespace(setIcon=colors.append, setToolTip=lambda text: None),
        status_action=SimpleNamespace(setText=lambda text: None),
    )
    ui.Desktop._status(desktop, {"home_assistant": "Connected", "display": mode})
    assert colors == ["#e9b54b" if mode == "Disconnected" else "#42ba97"]


def test_configuration_dashboard_list_idle_mode_and_ub_icon(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = ui.QApplication.instance() or ui.QApplication([])
    window = ui.ConfigurationWindow()
    settings = ui.Settings(
        base_url="http://example.test",
        serial_port="COM4",
        dashboards=["climate", "home"],
        idle_mode="dashboard",
        idle_dashboard="clock",
        idle_brightness=25,
    )
    window.populate(settings, "test-token")
    window.set_dashboards([("home", "House"), ("clock", "Clock"), ("climate", "Climate")])
    assert window.selected_dashboards() == ["climate", "home"]
    assert window.settings(settings).idle_dashboard == "clock"
    assert window.settings(settings).idle_brightness == 25
    assert not window.idle_off_seconds.isEnabled()
    assert window.idle_dashboard.isEnabled()
    assert window.windowTitle() == "uBridge · Configuration"
    assert not ui.bridge_icon().isNull()
    window.deleteLater()
    app.processEvents()


def test_save_repairs_startup_registration_even_when_already_checked(tmp_path, monkeypatch):
    settings = ui.Settings(
        base_url="http://example.test", serial_port="COM4", dashboards=["home"], start_at_login=True
    )
    registrations = []
    monkeypatch.setattr(
        ui.autostart, "set_enabled", lambda enabled, path: registrations.append((enabled, path))
    )
    desktop = SimpleNamespace(
        settings=settings,
        path=tmp_path / "config.json",
        _read_form=lambda: (settings, "test-token"),
        credentials=SimpleNamespace(set=lambda *args: None),
        redaction=SimpleNamespace(token=""),
        window=SimpleNamespace(feedback=SimpleNamespace(setText=lambda text: None)),
        connect=lambda: None,
    )
    ui.Desktop.save(desktop)
    ui.Desktop.save(desktop)
    assert registrations == [(True, desktop.path), (True, desktop.path)]


def test_dashboard_dropdown_add_remove_refresh_preserves_selection(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = ui.QApplication.instance() or ui.QApplication([])
    window = ui.ConfigurationWindow()
    window.populate(ui.Settings(), "")
    window.set_dashboards([("home", "Home"), ("climate", "Climate")])
    assert window.selected_dashboards() == []
    assert not window.add_dashboard_button.isEnabled()
    window.available_dashboard.setCurrentIndex(window.available_dashboard.findData("home"))
    assert window.add_dashboard_button.isEnabled()
    window.add_dashboard_button.click()
    assert window.selected_dashboards() == ["home"]
    assert not window.add_dashboard_button.isEnabled()
    window.add_dashboard()  # Duplicate requests do not duplicate the saved dashboard.
    window.available_dashboard.setCurrentIndex(window.available_dashboard.findData("climate"))
    window.add_dashboard_button.click()
    assert window.selected_dashboards() == ["home", "climate"]
    window.set_dashboards([("climate", "New title"), ("energy", "Energy")])
    assert window.selected_dashboards() == ["home", "climate"]
    assert "unavailable" in window.dashboards.item(0).text()
    assert "New title" in window.dashboards.item(1).text()
    assert window.available_dashboard.findData("home") == -1
    window.dashboards.setCurrentRow(0)
    window.remove_dashboard_button.click()
    assert window.selected_dashboards() == ["climate"]
    refreshes = []
    window.test_requested.connect(lambda: refreshes.append(True))
    window.refresh_dashboards.click()
    assert refreshes == [True]
    window.deleteLater()
    app.processEvents()


def test_startup_discovery_requires_credentials_but_no_dashboard_selection():
    calls = []
    desktop = SimpleNamespace(
        window=SimpleNamespace(
            base_url=SimpleNamespace(text=lambda: "http://example.test"),
            token=SimpleNamespace(text=lambda: "saved-token"),
        ),
        test=lambda: calls.append(True),
    )
    ui.Desktop.preload_dashboards(desktop)
    assert calls == [True]
    desktop.window.token.text = lambda: ""
    ui.Desktop.preload_dashboards(desktop)
    assert calls == [True]


def test_desktop_startup_preloads_dashboard_dropdown_without_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = ui.QApplication.instance() or ui.QApplication([])
    path = tmp_path / "config.json"
    ui.save_settings(path, ui.Settings(base_url="http://example.test"))
    monkeypatch.setattr(
        ui, "CredentialStore", lambda: SimpleNamespace(get=lambda settings: "saved-token")
    )
    monkeypatch.setattr(ui.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    loaded = []

    class Discovery(ui.QObject):
        dashboards = ui.Signal(object)
        result = ui.Signal(bool, str)
        finished = ui.Signal()

        def __init__(self, settings, token, *, test):
            super().__init__()
            assert test and token == "saved-token"
            assert settings.dashboards == [] and settings.serial_port == "not-tested"

        def isRunning(self):
            return False

        def start(self):
            loaded.append(True)
            self.dashboards.emit([("home", "Home"), ("climate", "Climate")])
            self.result.emit(True, "Dashboards loaded")
            self.finished.emit()

        def stop(self):
            pass

    monkeypatch.setattr(ui, "Worker", Discovery)
    desktop = ui.Desktop(path, redaction=SimpleNamespace(token=""))
    app.processEvents()
    assert loaded == [True]
    assert desktop.window.available_dashboard.findData("home") > 0
    assert desktop.window.selected_dashboards() == []
    assert desktop.window.refresh_dashboards.isEnabled()
    desktop.window.refresh_dashboards.click()
    assert loaded == [True, True]
    desktop.tray.hide()
    desktop.window.hide()
    desktop.window.deleteLater()
    desktop.deleteLater()
    app.processEvents()
