"""Native Qt tray menu and configuration window for Windows, Linux and macOS."""

import asyncio
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from serial.tools.list_ports import comports
from tjc_display.const import BAUD_RATES

from . import __version__, autostart
from .config import CredentialStore, Settings, load_settings, save_settings
from .releases import (
    DesktopState,
    Release,
    download_firmware,
    fetch,
    installation_marker,
    latest_release,
    newer,
)
from .runtime import Bridge, test_connection

_LOGGER = logging.getLogger(__name__)


def bridge_icon(color="#29b6d8"):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#142337"))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    # Draw the uB lettering as paths so the tray/executable icon has no font dependency.
    letters = QPainterPath()
    letters.moveTo(10, 25)
    letters.lineTo(16, 25)
    letters.lineTo(16, 39)
    letters.cubicTo(16, 47, 25, 47, 25, 39)
    letters.lineTo(25, 25)
    letters.lineTo(31, 25)
    letters.lineTo(31, 50)
    letters.lineTo(25, 50)
    letters.lineTo(25, 47)
    letters.cubicTo(18, 55, 10, 49, 10, 40)
    letters.closeSubpath()
    letters.moveTo(36, 18)
    letters.lineTo(45, 18)
    letters.cubicTo(58, 18, 58, 30, 51, 33)
    letters.cubicTo(61, 37, 58, 51, 45, 51)
    letters.lineTo(36, 51)
    letters.closeSubpath()
    letters.moveTo(42, 24)
    letters.lineTo(45, 24)
    letters.cubicTo(51, 24, 51, 31, 45, 31)
    letters.lineTo(42, 31)
    letters.closeSubpath()
    letters.moveTo(42, 37)
    letters.lineTo(46, 37)
    letters.cubicTo(53, 37, 53, 45, 46, 45)
    letters.lineTo(42, 45)
    letters.closeSubpath()
    painter.fillPath(letters, QColor(color))
    painter.end()
    return QIcon(pixmap)


class Worker(QThread):
    status = Signal(dict)
    result = Signal(bool, str)
    dashboards = Signal(object)

    def __init__(self, settings, token, *, test=False):
        super().__init__()
        self.settings, self.token, self.test = settings, token, test
        self.loop = self.task = self.bridge = None
        self._stop_requested = False

    def run(self):
        try:
            asyncio.run(self._run_async())
        except asyncio.CancelledError:
            pass
        except Exception as err:
            message = str(err).replace(self.token, "[token]")
            _LOGGER.error("Bridge worker: %s", message)
            self.result.emit(False, message)

    async def _run_async(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if self._stop_requested:
            return
        if self.test:
            choices = await test_connection(self.settings, self.token)
            self.dashboards.emit(choices)
            self.result.emit(
                True, f"Home Assistant connected. Found {len(choices)} available dashboards."
            )
        else:
            self.bridge = Bridge(self.settings, self.token, self.status.emit)
            await self.bridge.run()

    def stop(self):
        self._stop_requested = True
        if self.loop and self.loop.is_running() and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)

    def reload(self):
        if self.loop and self.loop.is_running() and self.bridge:
            self.loop.call_soon_threadsafe(self.bridge.reload)


class FirmwareWorker(QThread):
    progress = Signal(str)
    result = Signal(bool, str)

    def __init__(self, filename, port, baud):
        super().__init__()
        self.filename, self.port, self.baud = filename, port, baud

    def run(self):
        from tjc_display.firmware import upload

        try:
            upload(self.filename, self.port, self.baud, progress=self.progress.emit)
        except Exception as err:
            self.result.emit(False, str(err))
        else:
            self.result.emit(True, "Firmware uploaded. Reconnect after the display reboots.")


class NetworkWorker(QThread):
    result = Signal(bool, object)
    progress = Signal(str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            value = self.operation(self)
        except Exception as err:
            self.result.emit(False, str(err))
        else:
            self.result.emit(True, value)

    def stop(self):
        self.requestInterruption()


def firmware_target_dialog(window):
    dialog = QDialog(window)
    dialog.setWindowTitle("Download and flash TJC firmware")
    layout = QVBoxLayout(dialog)
    message = QLabel(
        "Download the recommended firmware for TJC4827X243_011 (X2). "
        "Flashing replaces the display program. Keep the display powered until upload finishes."
    )
    message.setWordWrap(True)
    layout.addWidget(message)
    form = QFormLayout()
    serial = QComboBox()
    serial.setEditable(True)
    serial.addItems([port.device for port in comports()])
    serial.setCurrentText(window.serial.currentText())
    baud = QComboBox()
    baud.addItems([str(rate) for rate in BAUD_RATES])
    baud.setCurrentText(window.baud.currentText())
    form.addRow("Display serial port", serial)
    form.addRow("Baud rate", baud)
    layout.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
    confirm.setText("Download && flash")
    confirm.setEnabled(bool(serial.currentText().strip()))
    serial.currentTextChanged.connect(lambda value: confirm.setEnabled(bool(value.strip())))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return serial.currentText().strip(), int(baud.currentText())
    return None


class ConfigurationWindow(QWidget):
    save_requested = Signal()
    test_requested = Signal()
    flash_requested = Signal()
    download_flash_requested = Signal()
    check_updates_requested = Signal()

    def __init__(self, *, tray_available=True):
        super().__init__()
        self.tray_available = tray_available
        self.setWindowTitle("uBridge · Configuration")
        self.setWindowIcon(bridge_icon())
        self.setMinimumWidth(600)
        self.resize(680, 750)
        self._apply_theme()
        QApplication.instance().paletteChanged.connect(self._apply_theme)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(13)
        title = QLabel("Connect your display")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Home Assistant on the network. Your TJC on this computer.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        card = QFrame()
        card.setObjectName("statusCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        self.connection_status = QLabel("Home Assistant: Disconnected  ·  Display: Disconnected")
        self.connection_detail = QLabel("Enter your connection details below.")
        self.connection_detail.setWordWrap(True)
        self.connection_detail.setTextFormat(Qt.TextFormat.PlainText)
        card_layout.addWidget(self.connection_status)
        card_layout.addWidget(self.connection_detail)
        layout.addWidget(card)
        self.update_notice = QPushButton()
        self.update_notice.setObjectName("updateNotice")
        self.update_notice.hide()
        layout.addWidget(self.update_notice)
        self.tabs = QTabWidget()
        self.forms = {}
        for name in ("Home Assistant", "Screen", "Dashboards", "Application"):
            content = QWidget()
            form = QFormLayout(content)
            form.setVerticalSpacing(12)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(content)
            self.tabs.addTab(scroll, name)
            self.forms[name] = form
        layout.addWidget(self.tabs, 1)
        form = self.forms["Home Assistant"]
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("http://homeassistant.local:8123")
        form.addRow("Home Assistant address", self.base_url)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Paste a long-lived access token")
        show = QCheckBox("Show")
        show.toggled.connect(
            lambda checked: self.token.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        token_layout = QHBoxLayout()
        token_layout.addWidget(self.token, 1)
        token_layout.addWidget(show)
        form.addRow("API access token", token_layout)
        token_hint = QLabel(
            "Create one in your Home Assistant profile → Security → Long-lived access tokens."
        )
        token_hint.setWordWrap(True)
        form.addRow("", token_hint)
        self.remember = QCheckBox("Remember token in the system credential store")
        form.addRow("", self.remember)
        form = self.forms["Screen"]
        self.serial = QComboBox()
        self.serial.setEditable(True)
        self.serial.setMinimumContentsLength(16)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_ports)
        ports_layout = QHBoxLayout()
        ports_layout.addWidget(self.serial, 1)
        ports_layout.addWidget(refresh)
        form.addRow("Display serial port", ports_layout)
        self.port_description = QLabel()
        self.port_description.setObjectName("subtitle")
        self.port_description.setWordWrap(True)
        self.serial.currentTextChanged.connect(self._describe_port)
        form.addRow("", self.port_description)
        self.baud = QComboBox()
        self.baud.addItems([str(rate) for rate in BAUD_RATES])
        form.addRow("Baud rate", self.baud)
        form = self.forms["Dashboards"]
        self.available_dashboard = QComboBox()
        self.available_dashboard.addItem("Loading available dashboards…", "")
        self.add_dashboard_button = QPushButton("Add")
        self.add_dashboard_button.clicked.connect(self.add_dashboard)
        self.available_dashboard.currentIndexChanged.connect(self._dashboard_controls)
        self.refresh_dashboards = QPushButton("Refresh")
        self.refresh_dashboards.clicked.connect(self.test_requested.emit)
        picker = QHBoxLayout()
        picker.addWidget(self.available_dashboard, 1)
        picker.addWidget(self.add_dashboard_button)
        picker.addWidget(self.refresh_dashboards)
        form.addRow("Available dashboards", picker)
        self.dashboards = QListWidget()
        self.dashboards.setMinimumHeight(140)
        self.dashboards.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.dashboards.currentRowChanged.connect(self._dashboard_controls)
        form.addRow("Display dashboards", self.dashboards)
        self.remove_dashboard_button = QPushButton("Remove selected dashboard")
        self.remove_dashboard_button.clicked.connect(self.remove_dashboard)
        form.addRow("", self.remove_dashboard_button)
        dashboard_hint = QLabel(
            "Choose a dashboard above and click Add. Select a row below to remove it; "
            "drag rows to set their order. The available list loads automatically from HA."
        )
        dashboard_hint.setWordWrap(True)
        form.addRow("", dashboard_hint)
        form = self.forms["Screen"]
        self.brightness = QSpinBox()
        self.brightness.setRange(1, 100)
        self.brightness.setSuffix(" %")
        form.addRow("Backlight", self.brightness)
        self.idle_enabled = QCheckBox("Enable idle behavior")
        form.addRow("Idle display", self.idle_enabled)
        self.idle_mode = QComboBox()
        self.idle_mode.addItem("Dim, then turn off", "dim")
        self.idle_mode.addItem("Show a dashboard", "dashboard")
        form.addRow("When idle", self.idle_mode)
        self.idle_dashboard = QComboBox()
        self.idle_dashboard.addItem("Select a dashboard", "")
        form.addRow("Idle dashboard", self.idle_dashboard)
        self.idle_brightness = QSpinBox()
        self.idle_brightness.setRange(0, 100)
        self.idle_brightness.setSuffix(" %")
        form.addRow("Idle dashboard brightness", self.idle_brightness)
        self.idle_dim_seconds = QSpinBox()
        self.idle_dim_seconds.setRange(1, 86400)
        self.idle_dim_seconds.setSuffix(" seconds")
        form.addRow("Idle after", self.idle_dim_seconds)
        self.idle_dim_percent = QSpinBox()
        self.idle_dim_percent.setRange(1, 100)
        self.idle_dim_percent.setSuffix(" % of backlight")
        form.addRow("Dimmed brightness", self.idle_dim_percent)
        self.idle_off_seconds = QSpinBox()
        self.idle_off_seconds.setRange(1, 86400)
        self.idle_off_seconds.setSuffix(" seconds")
        form.addRow("Then turn off after", self.idle_off_seconds)
        self.idle_enabled.toggled.connect(self._idle_controls)
        self.idle_mode.currentIndexChanged.connect(self._idle_controls)
        hint = QLabel(
            "Touch to wake. The first touch restores brightness without operating a device."
        )
        hint.setWordWrap(True)
        form.addRow("", hint)
        self.flash_button = QPushButton("Flash local firmware…")
        self.flash_button.clicked.connect(self.flash_requested.emit)
        form.addRow("Firmware", self.flash_button)
        self.download_flash_button = QPushButton("Download && flash recommended firmware…")
        self.download_flash_button.clicked.connect(self.download_flash_requested.emit)
        form.addRow("", self.download_flash_button)
        form = self.forms["Home Assistant"]
        self.ca_file = QLineEdit()
        self.ca_file.setPlaceholderText("Optional — use system certificates by default")
        certificate = QPushButton("Browse…")
        certificate.clicked.connect(self._browse_certificate)
        certificate_layout = QHBoxLayout()
        certificate_layout.addWidget(self.ca_file, 1)
        certificate_layout.addWidget(certificate)
        form.addRow("TLS CA certificate", certificate_layout)
        form = self.forms["Application"]
        self.auto_connect = QCheckBox("Connect automatically when the bridge opens")
        self.auto_login = QCheckBox("Start at sign-in")
        form.addRow("", self.auto_connect)
        form.addRow("", self.auto_login)
        self.update_status = QLabel("GitHub releases are checked once a day.")
        self.update_status.setWordWrap(True)
        self.update_status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow("Updates", self.update_status)
        self.check_updates_button = QPushButton("Check for updates now")
        self.check_updates_button.clicked.connect(self.check_updates_requested.emit)
        form.addRow("", self.check_updates_button)
        self._field_labels = {}
        for tab_form in [self.forms["Screen"]]:
            for control in (
                self.idle_mode,
                self.idle_dim_seconds,
                self.idle_dim_percent,
                self.idle_off_seconds,
                self.idle_dashboard,
                self.idle_brightness,
            ):
                label = tab_form.labelForField(control)
                if label is not None:
                    self._field_labels[control] = label
        self.feedback = QLabel(
            "Select dashboards from HA. The display must use this project's firmware."
        )
        self.feedback.setObjectName("feedback")
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.test_button = QPushButton("Test HA connection")
        self.test_button.clicked.connect(self.test_requested.emit)
        buttons.addWidget(self.test_button)
        buttons.addStretch()
        close = QPushButton("Hide" if tray_available else "Quit")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        self.save_button = QPushButton("Save & connect")
        self.save_button.setObjectName("connect")
        self.save_button.clicked.connect(self.save_requested.emit)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        version = QLabel(f"uBridge {__version__} · 480 × 272 · Serial ↔ API")
        version.setObjectName("subtitle")
        layout.addWidget(version)

    def _apply_theme(self, *_):
        palette = QApplication.instance().palette()
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        # Mid is a shadow/border color in native palettes and can be too dark for text.
        muted_text = "#b6bec9" if dark else "palette(mid)"
        disabled_border = "#626d7b" if dark else "palette(midlight)"
        self.setStyleSheet(
            """
            QWidget {font-family: 'Segoe UI', sans-serif; font-size: 13px;}
            QLineEdit,QComboBox,QSpinBox,QListWidget {min-height: 29px; padding: 2px 7px;
                background: palette(base); color: palette(text);
                border: 1px solid palette(mid); border-radius: 4px;}
            QPushButton {padding: 7px 14px; background: palette(button);
                color: palette(button-text); border: 1px solid palette(mid); border-radius: 4px;}
            QLabel#title {font-size: 25px; font-weight: 600;}
            QLabel#subtitle {color: $muted_text;}
            QFrame#statusCard {background: palette(alternate-base); border-radius: 9px;}
            QFrame#statusCard QLabel {background: transparent;}
            QPushButton#connect {background: palette(highlight);
                color: palette(highlighted-text); border: 0;}
            QPushButton#updateNotice {border: 2px solid palette(highlight); text-align: left;}
            QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QListWidget:disabled,
            QPushButton:disabled,QPushButton#connect:disabled {
                background: palette(window); color: $muted_text; border-color: $disabled_border;}
            QLabel:disabled,QCheckBox:disabled {color: $muted_text;}
        """.replace("$muted_text", muted_text).replace("$disabled_border", disabled_border)
        )

    def selected_dashboards(self):
        return [
            self.dashboards.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.dashboards.count())
        ]

    def _dashboard_controls(self):
        # Combo-box signals can fire before the rest of the form is constructed.
        if not hasattr(self, "remove_dashboard_button"):
            return
        url = self.available_dashboard.currentData()
        self.add_dashboard_button.setEnabled(bool(url) and url not in self.selected_dashboards())
        self.remove_dashboard_button.setEnabled(self.dashboards.currentRow() >= 0)

    def _append_dashboard(self, url, label):
        item = QListWidgetItem(f"{label} [{url}]")
        item.setData(Qt.ItemDataRole.UserRole, url)
        self.dashboards.addItem(item)

    def add_dashboard(self):
        url = self.available_dashboard.currentData()
        if url and url not in self.selected_dashboards():
            self._append_dashboard(url, self._available_dashboards[url])
            self.dashboards.setCurrentRow(self.dashboards.count() - 1)
        self._dashboard_controls()

    def remove_dashboard(self):
        row = self.dashboards.currentRow()
        if row >= 0:
            self.dashboards.takeItem(row)
        self._dashboard_controls()

    def set_dashboards(self, choices, *, selected=None, idle=None):
        selected = self.selected_dashboards() if selected is None else selected
        idle = (self.idle_dashboard.currentData() or "") if idle is None else idle
        available = self._available_dashboards = dict(choices)
        current = self.available_dashboard.currentData()
        self.available_dashboard.clear()
        self.available_dashboard.addItem(
            "Choose a dashboard"
            if available
            else "No dashboards loaded — enter HA details and refresh",
            "",
        )
        for url, label in choices:
            self.available_dashboard.addItem(f"{label} [{url}]", url)
        if current in available:
            self.available_dashboard.setCurrentIndex(self.available_dashboard.findData(current))
        self.dashboards.clear()
        for url in dict.fromkeys(selected):
            self._append_dashboard(url, available.get(url, url + " (unavailable)"))
        self.idle_dashboard.clear()
        self.idle_dashboard.addItem("Select a dashboard", "")
        urls = list(dict.fromkeys([*available, *([idle] if idle else [])]))
        for url in urls:
            label = available.get(url, url + " (unavailable)")
            self.idle_dashboard.addItem(f"{label} [{url}]", url)
        self.idle_dashboard.setCurrentIndex(max(0, self.idle_dashboard.findData(idle)))
        self._dashboard_controls()

    def _idle_controls(self):
        enabled = self.idle_enabled.isChecked()
        dashboard = self.idle_mode.currentData() == "dashboard"
        self.idle_mode.setEnabled(enabled)
        self.idle_dim_seconds.setEnabled(enabled)
        self.idle_dim_percent.setEnabled(enabled and not dashboard)
        self.idle_off_seconds.setEnabled(enabled and not dashboard)
        self.idle_dashboard.setEnabled(enabled and dashboard)
        self.idle_brightness.setEnabled(enabled and dashboard)
        for control, label in getattr(self, "_field_labels", {}).items():
            label.setEnabled(control.isEnabled())

    def _browse_certificate(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select TLS CA certificate", "", "Certificates (*.pem *.crt);;All files (*)"
        )
        if filename:
            self.ca_file.setText(filename)

    def _describe_port(self):
        text = self.serial.currentText()
        index = self.serial.currentIndex()
        description = self.serial.currentData() if self.serial.itemText(index) == text else ""
        self.port_description.setText(
            (description or "Port not currently detected. Connect the adapter and refresh.")
            if text
            else "Select a serial port."
        )

    def refresh_ports(self):
        previous = self.serial.currentText()
        self.serial.clear()
        for port in sorted(comports(), key=lambda item: item.device):
            self.serial.addItem(port.device, port.description)
        self.serial.setCurrentText(previous)
        self._describe_port()

    def populate(self, settings: Settings, token: str):
        self.base_url.setText(settings.base_url)
        self.token.setText(token)
        self.remember.setChecked(settings.remember_token)
        self.refresh_ports()
        self.serial.setCurrentText(settings.serial_port)
        self.baud.setCurrentText(str(settings.baudrate))
        self.set_dashboards(
            [],
            selected=settings.dashboards,
            idle=settings.idle_dashboard,
        )
        self.brightness.setValue(settings.brightness)
        self.idle_enabled.setChecked(settings.idle_enabled)
        self.idle_dim_seconds.setValue(settings.idle_dim_seconds)
        self.idle_dim_percent.setValue(settings.idle_dim_percent)
        self.idle_off_seconds.setValue(settings.idle_off_seconds)
        self.idle_mode.setCurrentIndex(max(0, self.idle_mode.findData(settings.idle_mode)))
        self.idle_brightness.setValue(settings.idle_brightness)
        self._idle_controls()
        self.ca_file.setText(settings.ca_file)
        self.auto_connect.setChecked(settings.connect_on_launch)
        self.auto_login.setChecked(settings.start_at_login)

    def settings(self, original, *, network_only=False):
        return replace(
            original,
            base_url=self.base_url.text(),
            serial_port=self.serial.currentText() or ("not-tested" if network_only else ""),
            baudrate=int(self.baud.currentText()),
            dashboards=self.selected_dashboards(),
            idle_mode=self.idle_mode.currentData(),
            idle_dashboard=self.idle_dashboard.currentData() or "",
            idle_brightness=self.idle_brightness.value(),
            brightness=self.brightness.value(),
            idle_enabled=self.idle_enabled.isChecked() and not network_only,
            idle_dim_seconds=self.idle_dim_seconds.value(),
            idle_dim_percent=self.idle_dim_percent.value(),
            idle_off_seconds=self.idle_off_seconds.value(),
            remember_token=self.remember.isChecked(),
            connect_on_launch=self.auto_connect.isChecked(),
            start_at_login=self.auto_login.isChecked(),
            ca_file=self.ca_file.text().strip(),
        ).validated()

    def show_status(self, status):
        self.connection_status.setText(
            f"Home Assistant: {status['home_assistant']}  ·  Display: {status['display']}"
            f"  ·  {status['pages']} pages"
        )
        self.connection_detail.setText(status["detail"])

    def closeEvent(self, event):
        if not self.flash_button.isEnabled():
            event.ignore()
            return
        if self.tray_available:
            event.ignore()
            self.hide()
        else:
            QApplication.instance().quit()


class Desktop(QObject):
    def __init__(
        self, path, *, redaction, show_config=False, background=False, background_services=True
    ):
        super().__init__()
        self.path, self.redaction = path, redaction
        self.desktop_state = DesktopState(path.with_name(path.stem + ".desktop.json"))
        self.updater = self.downloader = None
        self._release_url = ""
        self.credentials = CredentialStore()
        self.worker = self.tester = None
        self._pending_start = False
        self._quitting = False
        message = ""
        try:
            self.settings = load_settings(path)
        except (ValueError, TypeError, OSError):
            self.settings = Settings()
            message = "Could not read the configuration file. Enter your settings again."
        try:
            self.token = self.credentials.get(self.settings) if self.settings.base_url else ""
        except Exception:
            self.token = ""
            message = "The saved token could not be read. Enter it again to connect."
        self.redaction.token = self.token
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.window = ConfigurationWindow(tray_available=self.tray_available)
        self.window.populate(self.settings, self.token)
        if message:
            self.window.feedback.setText(message)
        self.window.save_requested.connect(self.save)
        self.window.test_requested.connect(self.test)
        self.window.flash_requested.connect(self.flash_firmware)
        self.window.download_flash_requested.connect(self.download_and_flash)
        self.window.check_updates_requested.connect(lambda: self.check_updates(force=True))
        self.window.update_notice.clicked.connect(self.open_release)
        self.flasher = None
        self._flash_request = None
        self.tray = QSystemTrayIcon(bridge_icon(), self)
        self.menu = QMenu()
        self.status_action = self.menu.addAction("uBridge · Disconnected")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.configure_action = self.menu.addAction("Configuration…", self.show_configuration)
        self.menu.addAction("Reconnect", self.connect)
        self.menu.addAction("Reload dashboards", self.reload)
        self.menu.addAction("Disconnect", self.disconnect)
        self.menu.addSeparator()
        self.menu.addAction(
            "Open log folder",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))),
        )
        self.menu.addAction("About…", self.about)
        self.menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("uBridge · Disconnected")
        self.tray.activated.connect(
            lambda reason: (
                self.show_configuration()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )
        )
        self.tray.show()
        valid = False
        try:
            self.settings.validated()
            valid = bool(self.token and self.settings.dashboards)
        except ValueError:
            pass
        if valid and self.settings.connect_on_launch:
            self.connect()
        if (
            show_config
            or not valid
            or not self.tray_available
            or not background
            and not path.exists()
        ):
            self.show_configuration()
        # Discovery does not require selected dashboards or a connected serial adapter.
        QTimer.singleShot(0, self.preload_dashboards)
        self.window.base_url.editingFinished.connect(self.preload_dashboards)
        self.window.token.editingFinished.connect(self.preload_dashboards)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(60_000)
        self.update_timer.timeout.connect(self.check_updates)
        if background_services:
            self.update_timer.start()
            QTimer.singleShot(0, self.startup_services)

    def about(self):
        QMessageBox.information(
            self.window, "About uBridge", f"uBridge version {__version__} - (c) 2026 umbacos"
        )

    def startup_services(self):
        self.check_updates()
        marker = installation_marker()
        if self.desktop_state.read().get("firmware_prompt_version") == marker:
            return
        self.show_configuration()
        answer = QMessageBox.question(
            self.window,
            "Set up your TJC display",
            "Does your TJC display need to be flashed? "
            "Flashing the recommended uBridge firmware is recommended for a new installation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        self.desktop_state.update(firmware_prompt_version=marker)
        if answer == QMessageBox.StandardButton.Yes:
            self.download_and_flash()

    def check_updates(self, *, force=False):
        if self._quitting or self.updater and self.updater.isRunning():
            return
        cached = self.desktop_state.read().get("available_release")
        if isinstance(cached, dict) and str(cached.get("url", "")).startswith(
            "https://github.com/umbacos/uBridge/releases/tag/"
        ):
            release = Release(str(cached.get("version", "")), cached["url"], "")
            self.show_release(release)
        if not force and not self.desktop_state.due():
            return
        try:
            self.desktop_state.update(last_update_check=time.time())
        except OSError as err:
            self.window.update_status.setText(f"Cannot save update check state: {err}")
            return
        self.window.check_updates_button.setEnabled(False)
        self.window.update_status.setText("Checking GitHub releases…")
        self.updater = NetworkWorker(
            lambda worker: latest_release(
                download=lambda url, limit: fetch(
                    url, limit, cancelled=worker.isInterruptionRequested
                )
            )
        )
        self.updater.result.connect(self.update_result)
        self.updater.finished.connect(lambda: self.window.check_updates_button.setEnabled(True))
        self.updater.start()

    def show_release(self, release):
        available = newer(release)
        self._release_url = release.url if available else ""
        self.window.update_notice.setText(
            f"uBridge {release.version} is available — open release" if available else ""
        )
        self.window.update_notice.setVisible(available)

    def update_result(self, ok, value):
        if self._quitting:
            return
        if not ok:
            self.window.update_status.setText(
                f"Could not check GitHub: {value}. The next automatic check is in 24 hours."
            )
            return
        self.show_release(value)
        try:
            self.desktop_state.update(
                available_release={"version": value.version, "url": value.url} if value else None
            )
        except OSError:
            pass
        self.window.update_status.setText(
            f"New release: {value.version}."
            if newer(value)
            else "You are up to date."
            if value
            else "No published GitHub releases yet."
        )

    def open_release(self):
        if self._release_url:
            QDesktopServices.openUrl(QUrl(self._release_url))

    def download_and_flash(self):
        if (
            self.flasher
            and self.flasher.isRunning()
            or self.downloader
            and self.downloader.isRunning()
            or self._flash_request
        ):
            return
        self.window.tabs.setCurrentIndex(1)
        target = firmware_target_dialog(self.window)
        if target is None:
            return
        port, baud = target
        self.window.serial.setCurrentText(port)
        self.window.baud.setCurrentText(str(baud))
        settings = replace(self.settings, serial_port=port, baudrate=baud)
        try:
            save_settings(self.path, settings)
        except OSError as err:
            self.window.feedback.setText(f"Could not save display settings: {err}")
            return
        self.settings = settings
        self.flash_controls(False)
        self.downloader = NetworkWorker(
            lambda worker: download_firmware(
                self.path.parent / "firmware",
                progress=worker.progress.emit,
                download=lambda url, limit: fetch(
                    url, limit, cancelled=worker.isInterruptionRequested
                ),
            )
        )
        self.downloader.progress.connect(self.window.feedback.setText)
        self.downloader.result.connect(
            lambda ok, value: self.firmware_downloaded(ok, value, port, baud)
        )
        self.downloader.start()

    def firmware_downloaded(self, ok, value, port, baud):
        if self._quitting:
            return
        if not ok:
            self.window.feedback.setText(f"Firmware download failed: {value}")
            self.flash_controls(True)
            return
        self._flash_request = value, port, baud
        self.disconnect()
        self._begin_flash()

    def flash_controls(self, enabled):
        self.window.flash_button.setEnabled(enabled)
        self.window.download_flash_button.setEnabled(enabled)
        self.window.serial.setEnabled(enabled)
        self.window.baud.setEnabled(enabled)
        self.window.save_button.setEnabled(enabled)

    def preload_dashboards(self):
        if self.window.base_url.text().strip() and self.window.token.text().strip():
            self.test()

    def flash_firmware(self):
        if (
            self.flasher
            and self.flasher.isRunning()
            or self.downloader
            and self.downloader.isRunning()
            or self._flash_request
        ):
            return
        port = self.window.serial.currentText().strip()
        baud = int(self.window.baud.currentText())
        if not port:
            self.window.feedback.setText("Select the display serial port first.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self.window, "Select firmware for TJC4827X243_011", "", "TJC firmware (*.tft)"
        )
        if not filename:
            return
        if (
            QMessageBox.question(
                self.window,
                "Flash display",
                f"Upload {filename} to {port} at {baud} baud? "
                "The bridge will disconnect. Keep the display powered until upload finishes.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._flash_request = filename, port, baud
        self.flash_controls(False)
        self.disconnect()
        self._begin_flash()

    def _begin_flash(self):
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(100, self._begin_flash)
            return
        filename, port, baud = self._flash_request
        self._flash_request = None
        self.flasher = FirmwareWorker(filename, port, baud)
        self.flasher.progress.connect(self.window.feedback.setText)
        self.flasher.result.connect(lambda ok, message: self.window.feedback.setText(message))
        self.flasher.finished.connect(lambda: self.flash_controls(True))
        self.flasher.start()

    def show_configuration(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _read_form(self, *, network_only=False):
        settings = self.window.settings(self.settings, network_only=network_only)
        token = self.window.token.text().strip()
        if not token:
            raise ValueError("Enter a Home Assistant long-lived access token.")
        return settings, token

    def save(self):
        try:
            settings, token = self._read_form()
            notice = "Settings saved. Connecting…"
            try:
                self.credentials.set(settings, token)
            except Exception:
                if settings.remember_token:
                    settings = replace(settings, remember_token=False)
                    self.window.remember.setChecked(False)
                    notice = "Credential store unavailable. Token is kept for this session only."
                else:
                    notice = "Session token only; the old vault entry could not be removed."
            if not settings.dashboards:
                raise ValueError("Load dashboards from HA and select at least one dashboard.")
            autostart.set_enabled(settings.start_at_login, self.path)
            save_settings(self.path, settings)
            self.settings, self.token = settings, token
            self.redaction.token = token
            self.window.feedback.setText(notice)
            self.connect()
        except (ValueError, OSError) as err:
            self.window.feedback.setText(str(err))

    def test(self):
        if self.tester and self.tester.isRunning():
            return
        try:
            settings, token = self._read_form(network_only=True)
        except ValueError as err:
            self.window.feedback.setText(str(err))
            return
        self.window.test_button.setEnabled(False)
        self.window.refresh_dashboards.setEnabled(False)
        self.window.feedback.setText("Loading available dashboards from Home Assistant…")
        self.tester = Worker(settings, token, test=True)
        self.tester.dashboards.connect(self.window.set_dashboards)
        self.tester.result.connect(lambda ok, message: self.window.feedback.setText(message))
        self.tester.finished.connect(lambda: self.window.test_button.setEnabled(True))
        self.tester.finished.connect(lambda: self.window.refresh_dashboards.setEnabled(True))
        self.tester.start()

    def connect(self):
        if (
            self._flash_request
            or self.flasher
            and self.flasher.isRunning()
            or self.downloader
            and self.downloader.isRunning()
        ):
            self.window.feedback.setText("Wait for the firmware download or upload to finish.")
            return
        try:
            self.settings.validated()
            if not self.token:
                raise ValueError("Enter your access token in Configuration.")
        except ValueError as err:
            self.window.feedback.setText(str(err))
            self.show_configuration()
            return
        if self.worker and self.worker.isRunning():
            self._pending_start = True
            self.worker.stop()
        else:
            self._start_worker()

    def _start_worker(self):
        self._pending_start = False
        self.worker = Worker(self.settings, self.token)
        self.worker.status.connect(self._status)
        self.worker.result.connect(self._failure)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _status(self, status):
        self.window.show_status(status)
        connected = status["home_assistant"] == "Connected" and status["display"].startswith(
            "Connected"
        )
        self.tray.setIcon(bridge_icon("#42ba97" if connected else "#e9b54b"))
        message = f"HA {status['home_assistant']} · Display {status['display']}"
        self.status_action.setText(message)
        self.tray.setToolTip(f"uBridge · {message}")

    def _failure(self, ok, message):
        self._status(
            {
                "home_assistant": "Disconnected",
                "display": "Disconnected",
                "pages": 0,
                "detail": message,
            }
        )
        self.window.feedback.setText(message)

    def _worker_finished(self):
        if self._quitting:
            self._finish_quit()
        elif self._pending_start:
            self._start_worker()

    def disconnect(self):
        self._pending_start = False
        if self.worker:
            self.worker.stop()
        self._status(
            {
                "home_assistant": "Disconnected",
                "display": "Disconnected",
                "pages": 0,
                "detail": "Bridge disconnected. Use Reconnect to resume.",
            }
        )

    def reload(self):
        if self.worker:
            self.worker.reload()

    def quit(self):
        if self._flash_request or self.flasher and self.flasher.isRunning():
            self.window.feedback.setText("Wait for the firmware upload to finish before quitting.")
            self.show_configuration()
            return
        self._quitting = True
        self.update_timer.stop()
        for network in (self.updater, self.downloader):
            if network:
                network.stop()
        self._pending_start = False
        if self.tester:
            self.tester.stop()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        else:
            self._finish_quit()

    def _finish_quit(self):
        if any(worker and worker.isRunning() for worker in (self.updater, self.downloader)):
            QTimer.singleShot(50, self._finish_quit)
            return
        if self.tester and self.tester.isRunning():
            QTimer.singleShot(50, self._finish_quit)
            return
        self.tray.hide()
        QApplication.instance().quit()


def run_desktop(path: Path, show_config=False, background=False, redaction=None):
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("uBridge")
    application.setApplicationVersion(__version__)
    application.setQuitOnLastWindowClosed(False)
    application.styleHints().unsetColorScheme()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path.with_suffix(".lock")))
    if not lock.tryLock(100):
        QMessageBox.information(
            None,
            "uBridge",
            "The bridge is already running for this configuration. "
            "Use its tray icon to open Configuration.",
        )
        return 0
    desktop = Desktop(path, redaction=redaction, show_config=show_config, background=background)
    # Keep Python references alive for the entire native event loop.
    application._tjc_desktop = desktop
    result = application.exec()
    desktop.quit()
    for worker in (desktop.worker, desktop.tester, desktop.updater, desktop.downloader):
        if worker:
            worker.stop()
            worker.wait(5000)
    lock.unlock()
    return result
