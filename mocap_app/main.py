from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mocap_app.core.config import AppConfig
from mocap_app.core.logging_config import configure_logging
from mocap_app.ui.designed_main_window import DesignedMainWindow
from ui.gui import IMAGES_DIR


def run() -> int:
    config = AppConfig.load()
    config.ensure_directories()
    configure_logging(config.logs_dir)

    app = QApplication(sys.argv)
    icon_path = IMAGES_DIR / "HU_Logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = DesignedMainWindow(config=config)
    window.showMaximized()
    return app.exec()
