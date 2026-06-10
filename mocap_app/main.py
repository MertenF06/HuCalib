from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mocap_app.core.config import AppConfig
from mocap_app.core.logging_config import configure_logging
from mocap_app.ui.designed_main_window import DesignedMainWindow
from ui.gui import IMAGES_DIR


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID so the taskbar uses our window
    icon instead of the host python.exe icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HU.HuMoCap.Calib.1")
    except Exception:
        pass


def run() -> int:
    config = AppConfig.load()
    config.ensure_directories()
    configure_logging(config.logs_dir)

    _set_windows_app_id()

    app = QApplication(sys.argv)
    icon_path = IMAGES_DIR / "hucalib_cube_icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = DesignedMainWindow(config=config)
    window.showMaximized()
    return app.exec()
