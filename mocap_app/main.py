from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mocap_app.core.config import AppConfig
from mocap_app.core.logging_config import configure_logging
from mocap_app.core.updater import UpdateController
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

    # Check GitHub for a newer release shortly after the window is up, so the
    # check never delays startup. The controller is parented to the window so it
    # stays alive for the app's lifetime; the window also drives manual checks.
    updater = UpdateController(window)
    window.update_controller = updater
    QTimer.singleShot(2500, updater.start_background_check)

    return app.exec()
