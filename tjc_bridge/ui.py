"""Native Qt tray menu and configuration window for Windows, Linux and macOS."""

import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)
from serial.tools.list_ports import comports
from tjc_display.const import BAUD_RATES

from . import __version__, autostart
from .config import CredentialStore, Settings, load_settings, save_settings
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


class ConfigurationWindow(QWidget):
    save_requested = Signal()
    test_requested = Signal()
    flash_requested = Signal()

    def __init__(self, *, tray_available=True):
        super().__init__()
        self.tray_available = tray_available
        self.setWindowTitle("uBridge · Configuration")
        self.setWindowIcon(bridge_icon())
        self.setMinimumWidth(600)
        self.resize(680, 750)
        self.setStyleSheet(
            "QWidget {font-family: 'Segoe UI', sans-serif; font-size: 13px;"
            "background: #f5f8fc; color: #172b40;}"
            "QLineEdit,QComboBox,QSpinBox {min-height: 29px; padding: 2px 7px;"
            "background: white; border: 1px solid #c7d3df; border-radius: 4px;}"
            "QPushButton {padding: 7px 14px; background: #e6eef6;"
            "border: 1px solid #c7d3df; border-radius: 4px;}"
            "QLabel#title {font-size: 25px; font-weight: 600;}"
            "QLabel#subtitle {color: #627287;}"
            "QFrame#statusCard {background: #eaf3f8; border-radius: 9px;}"
            "QFrame#statusCard QLabel {background: transparent;}"
            "QLabel#feedback {color: #426179;}"
            "QPushButton#connect {background: #087fa1; color: white;"
            "border: 0; border-radius: 4px;}"
        )
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
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setVerticalSpacing(12)
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
        self.ca_file = QLineEdit()
        self.ca_file.setPlaceholderText("Optional — use system certificates by default")
        certificate = QPushButton("Browse…")
        certificate.clicked.connect(self._browse_certificate)
        certificate_layout = QHBoxLayout()
        certificate_layout.addWidget(self.ca_file, 1)
        certificate_layout.addWidget(certificate)
        form.addRow("TLS CA certificate", certificate_layout)
        self.auto_connect = QCheckBox("Connect automatically when the bridge opens")
        self.auto_login = QCheckBox("Start at sign-in")
        form.addRow("", self.auto_connect)
        form.addRow("", self.auto_login)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)
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
        self.flash_button = QPushButton("Flash display firmware…")
        self.flash_button.clicked.connect(self.flash_requested.emit)
        layout.addWidget(self.flash_button)
        version = QLabel(f"uBridge {__version__} · 480 × 272 · Serial ↔ API")
        version.setObjectName("subtitle")
        layout.addWidget(version)

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
    def __init__(self, path, *, redaction, show_config=False, background=False):
        super().__init__()
        self.path, self.redaction = path, redaction
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

    def preload_dashboards(self):
        if self.window.base_url.text().strip() and self.window.token.text().strip():
            self.test()

    def flash_firmware(self):
        if self.flasher and self.flasher.isRunning() or self._flash_request:
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
        self.window.flash_button.setEnabled(False)
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
        self.flasher.finished.connect(lambda: self.window.flash_button.setEnabled(True))
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
        if self._flash_request or self.flasher and self.flasher.isRunning():
            self.window.feedback.setText("Wait for the firmware upload to finish.")
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
        self._pending_start = False
        if self.tester:
            self.tester.stop()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        else:
            self._finish_quit()

    def _finish_quit(self):
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
    for worker in (desktop.worker, desktop.tester):
        if worker:
            worker.stop()
            worker.wait(5000)
    lock.unlock()
    return result
