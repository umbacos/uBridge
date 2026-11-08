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
    desktop = ui.Desktop(path, redaction=SimpleNamespace(token=""), background_services=False)
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


def test_tabs_disabled_fields_and_palette_following(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = ui.QApplication.instance() or ui.QApplication([])
    window = ui.ConfigurationWindow()
    window.populate(ui.Settings(idle_mode="dashboard", idle_dashboard="clock"), "")
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Home Assistant",
        "Screen",
        "Dashboards",
        "Application",
    ]
    assert not window.idle_off_seconds.isEnabled()
    assert not window._field_labels[window.idle_off_seconds].isEnabled()
    assert "palette(base)" in window.styleSheet() and ":disabled" in window.styleSheet()
    assert "background: #f5f8fc" not in window.styleSheet()
    window.idle_enabled.setChecked(False)
    assert not window.idle_dashboard.isEnabled()
    window.deleteLater()
    app.processEvents()


def test_about_text_matches_requested_version_and_copyright(monkeypatch):
    texts = []
    monkeypatch.setattr(
        ui.QMessageBox, "information", lambda parent, title, text: texts.append(text)
    )
    ui.Desktop.about(SimpleNamespace(window=None))
    assert texts == [f"uBridge version {ui.__version__} - (c) 2026 umbacos"]


@pytest.mark.parametrize(
    "answer", [ui.QMessageBox.StandardButton.Yes, ui.QMessageBox.StandardButton.No]
)
def test_first_run_prompts_once_and_only_downloads_if_user_accepts(tmp_path, monkeypatch, answer):
    prompts, downloads = [], []

    def question(*args):
        prompts.append(args)
        assert args[-1] == ui.QMessageBox.StandardButton.Yes
        return answer

    monkeypatch.setattr(ui.QMessageBox, "question", question)
    desktop = SimpleNamespace(
        desktop_state=ui.DesktopState(tmp_path / "state.json"),
        window=None,
        check_updates=lambda: None,
        show_configuration=lambda: None,
        download_and_flash=lambda: downloads.append(True),
    )
    ui.Desktop.startup_services(desktop)
    ui.Desktop.startup_services(desktop)
    assert len(prompts) == 1
    assert downloads == ([True] if answer == ui.QMessageBox.StandardButton.Yes else [])


def test_download_error_does_not_open_serial_or_start_flash():
    calls = []
    desktop = SimpleNamespace(
        _quitting=False,
        window=SimpleNamespace(feedback=SimpleNamespace(setText=lambda text: calls.append(text))),
        flash_controls=lambda enabled: calls.append(enabled),
        disconnect=lambda: calls.append("disconnect"),
        _begin_flash=lambda: calls.append("flash"),
    )
    ui.Desktop.firmware_downloaded(desktop, False, "bad hash", "COM4", 115200)
    assert "flash" not in calls and "disconnect" not in calls
    ui.Desktop.firmware_downloaded(desktop, True, "verified.tft", "COM4", 115200)
    assert desktop._flash_request == ("verified.tft", "COM4", 115200)
    assert calls[-2:] == ["disconnect", "flash"]
