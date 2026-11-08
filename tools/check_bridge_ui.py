"""Render tabbed uBridge configuration with light/dark application palettes."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QColor, QFontDatabase, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tjc_bridge.config import Settings  # noqa: E402
from tjc_bridge.ui import ConfigurationWindow  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = QApplication.instance() or QApplication([])
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        path = Path("C:/Windows/Fonts") / font
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    light = QPalette(app.palette())
    for dark in (False, True):
        palette = QPalette(light)
        if dark:
            for role, value in [
                (QPalette.ColorRole.Window, "#20242a"),
                (QPalette.ColorRole.WindowText, "#f1f3f5"),
                (QPalette.ColorRole.Base, "#13161b"),
                (QPalette.ColorRole.AlternateBase, "#2a3038"),
                (QPalette.ColorRole.Text, "#f1f3f5"),
                (QPalette.ColorRole.Button, "#30363f"),
                (QPalette.ColorRole.ButtonText, "#f1f3f5"),
                (QPalette.ColorRole.Mid, "#8a929d"),
                (QPalette.ColorRole.Midlight, "#535d69"),
                (QPalette.ColorRole.Highlight, "#3daee9"),
                (QPalette.ColorRole.HighlightedText, "#101418"),
            ]:
                palette.setColor(role, QColor(value))
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8a929d")
            )
        app.setPalette(palette)
        window = ConfigurationWindow()
        window.populate(
            Settings(
                base_url="http://homeassistant.local:8123",
                serial_port="COM4",
                dashboards=["home-panel", "climate-panel"],
                idle_mode="dashboard",
                idle_dashboard="charts-panel",
                idle_brightness=25,
            ),
            "",
        )
        window.set_dashboards(
            [("home-panel", "Home"), ("climate-panel", "Climate"), ("charts-panel", "Energy")]
        )
        window.resize(820, 1060)
        window.show()
        for index, label in enumerate(("homeassistant", "screen", "dashboards", "application")):
            window.tabs.setCurrentIndex(index)
            app.processEvents()
            window.grab().save(
                str(ROOT / f"docs/ubridge-{label}-{'dark' if dark else 'light'}.png")
            )
        if not dark:
            window.tabs.setCurrentIndex(0)
            app.processEvents()
            window.grab().save(str(ROOT / "docs/ubridge-configuration.png"))
        window.hide()
        window.deleteLater()
        app.processEvents()
    app.setPalette(light)
    print(
        "PASS: rendered all four tabs with light and dark palettes, "
        "including disabled idle controls"
    )


if __name__ == "__main__":
    main()
