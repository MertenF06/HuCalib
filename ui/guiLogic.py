"""Logic / orchestration glue for the PhysioMotionTracker UI.

Owns the calibration backend objects (manager, repository, current bundle)
and brokers signals between the designed UI and the tabs.
"""

from __future__ import annotations

import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtWidgets

from mocap_app.core.config import AppConfig
from mocap_app.io.calibration_io import CalibrationManager, CalibrationRepository

from .tab_cameras import TabCameras
from .tab_diagnostics import TabDiagnostics
from .tab_directory import TabDirectory
from .tab_home import TabHome
from .tab_results import TabResults
from .tab_settings import TabSettings


class Logic(QtCore.QObject):
    """Application logic owned by the MainWindow."""

    def __init__(self, window, config: AppConfig | None = None) -> None:
        super().__init__(window)
        self.window = window

        # Configuration + backend
        self.config: AppConfig = config or AppConfig.load()
        self.config.ensure_directories()
        self.calibration_repo = CalibrationRepository()
        self.calibration_manager = CalibrationManager()
        self.calibration_path: Path = self.config.calibration_dir / "current_calibration.json"
        self.current_bundle = self.calibration_repo.load(self.calibration_path)
        self.intrinsics_worker = None

        # Track app uptime for diagnostics.
        self._started_at = time.perf_counter()

        self.nav_buttons = [
            self.window.btn_home,
            self.window.btn_cameras,
            self.window.btn_results,
            self.window.btn_directory,
            self.window.btn_diagnostics,
            self.window.btn_advanced_settings,
        ]

        # Navigation
        self.window.btn_home.clicked.connect(lambda: self.switch_page(0))
        self.window.btn_cameras.clicked.connect(lambda: self.switch_page(1))
        self.window.btn_results.clicked.connect(lambda: self.switch_page(2))
        self.window.btn_directory.clicked.connect(lambda: self.switch_page(3))
        self.window.btn_diagnostics.clicked.connect(lambda: self.switch_page(4))
        self.window.btn_advanced_settings.clicked.connect(lambda: self.switch_page(5))

        # Home actions
        self.window.btn_newproject.clicked.connect(self.create_new_project)
        self.window.btn_loadproject.clicked.connect(self.load_project)

        # Console input
        self.window.lineedit_console_input.returnPressed.connect(self.handle_console_input)

        # Menu actions
        self.window.actionNew_project.triggered.connect(self.create_new_project)
        self.window.actionOpen_project.triggered.connect(self.load_project)
        self.window.actionQuit.triggered.connect(self.quit_application)
        self.window.actionOpen_documentation.triggered.connect(self.open_documentation)

        # Instantiate tabs
        self.tab_home = TabHome(self)
        self.tab_cameras = TabCameras(self)
        self.tab_results = TabResults(self)
        self.tab_directory = TabDirectory(self)
        self.tab_diagnostics = TabDiagnostics(self)
        self.tab_settings = TabSettings(self)

        self.tab_home.setup()
        self.tab_cameras.setup()
        self.tab_results.setup()
        self.tab_directory.setup()
        self.tab_diagnostics.setup()
        self.tab_settings.setup()

        self.switch_page(0)
        self.refresh_results()

        # Boot banner
        self.log_to_console(
            f"PhysioMotionTracker gestart - kalibratiemap: {self.config.calibration_dir}"
        )
        if self.current_bundle is not None:
            self.log_to_console(
                f"Bestaande kalibratie geladen: {self.calibration_path.name}"
            )

    # ----- public API for tabs -------------------------------------------

    def uptime_seconds(self) -> float:
        return time.perf_counter() - self._started_at

    def refresh_results(self) -> None:
        if hasattr(self, "tab_results"):
            self.tab_results.refresh()

    def log_to_console(self, text: str) -> None:
        stamp = datetime.now().strftime("[%H:%M:%S]")
        self.window.plaintextedit_console.appendPlainText(f"{stamp} {text}")

    # ----- console commands ----------------------------------------------

    def handle_console_input(self) -> None:
        input_text = self.window.lineedit_console_input.text().strip()
        self.window.lineedit_console_input.clear()
        if not input_text:
            return

        self.window.plaintextedit_console.appendPlainText(f"> {input_text}")
        command = input_text.lower()

        if command.startswith("capture intrinsics"):
            parts = command.split()
            if len(parts) == 3 and parts[2].isdigit():
                self.tab_cameras.capture_intrinsics_for_camera(int(parts[2]))
            else:
                self.log_to_console("Gebruik: capture intrinsics <nummer>")
        elif command.startswith("capture extrinsics"):
            self.tab_cameras.add_extrinsic_capture()
            self.log_to_console("Systeem: Extrinsics teller +1.")
        elif command == "home":
            self.switch_page(0)
        elif command in {"cameras", "kalibratie"}:
            self.switch_page(1)
        elif command in {"results", "export"}:
            self.switch_page(2)
        elif command == "directory":
            self.switch_page(3)
        elif command == "diagnostics":
            self.switch_page(4)
        elif command in {"settings", "instellingen"}:
            self.switch_page(5)
        elif command == "solve intrinsics":
            self.tab_cameras.calculate_intrinsics()
        elif command == "solve extrinsics":
            self.tab_cameras.calculate_extrinsics()
        elif command == "reset":
            self.tab_cameras.reset_calibration_buttons()
        elif command in {"help", "?"}:
            self.log_to_console(
                "Beschikbare commando's: home / cameras / results / directory / "
                "diagnostics / settings / solve intrinsics / solve extrinsics / "
                "capture intrinsics <n> / capture extrinsics / reset"
            )
        else:
            self.log_to_console(f"Onbekend commando: {input_text} (typ 'help')")

    # ----- navigation ----------------------------------------------------

    def switch_page(self, index: int) -> None:
        self.window.stackedWidget.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ----- project handling ---------------------------------------------

    def create_new_project(self) -> None:
        try:
            sessions_dir = self.config.sessions_dir or Path.cwd() / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H_%M_%S")
            project_path = sessions_dir / f"Session_{timestamp}"
            project_path.mkdir(parents=True, exist_ok=True)
            self.tab_cameras.reset_calibration_buttons()
            self.switch_page(3)
            self.tab_directory.load_root_directory(project_path)
            self.config.default_sessions_dir = project_path.parent
            self.config.save()
            self.log_to_console(f"Nieuw project aangemaakt: {project_path}")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self.window, "Fout", f"Kon project niet aanmaken:\n{exc}"
            )
            self.log_to_console(f"Fout bij aanmaken project: {exc}")

    def load_project(self) -> None:
        try:
            start_dir = self.config.default_sessions_dir or Path.cwd()
            selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self.window, "Selecteer een project map", str(start_dir)
            )
            if not selected_dir:
                return
            project_path = Path(selected_dir)
            self.config.default_sessions_dir = project_path.parent
            self.config.save()
            self.switch_page(3)
            self.tab_directory.load_root_directory(project_path)
            self.log_to_console(f"Project geladen: {project_path}")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self.window, "Fout", f"Kon project niet openen:\n{exc}"
            )
            self.log_to_console(f"Fout bij laden project: {exc}")

    def quit_application(self) -> None:
        self.window.close()

    def open_documentation(self) -> None:
        webbrowser.open("https://github.com/MaxUntersalmberger/PhysioMotionTracker")
        self.log_to_console("Documentatie geopend in de browser.")
