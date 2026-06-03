from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from mocap_app.io.calibration_io import CalibrationManager


LOGGER = logging.getLogger(__name__)


class IntrinsicsSolveWorker(QThread):
    """Runs the potentially expensive OpenCV intrinsics solve off the UI thread."""

    result_ready = Signal(object)
    error = Signal(str)
    state_changed = Signal(str)

    def __init__(self, calibration_manager: CalibrationManager) -> None:
        super().__init__()
        self._calibration_manager = calibration_manager

    def run(self) -> None:
        try:
            self.state_changed.emit("intrinsics_solve_started")
            bundle = self._calibration_manager.solve_intrinsics()
            self.result_ready.emit(bundle)
            self.state_changed.emit("intrinsics_solve_finished")
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Intrinsics solve failed.")
            self.error.emit(str(exc))
            self.state_changed.emit("intrinsics_solve_failed")
