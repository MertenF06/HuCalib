from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from mocap_app.io.calibration_io import CalibrationManager
from mocap_app.models.types import CalibrationBundle


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


class ExtrinsicsSolveWorker(QThread):
    """Runs the extrinsics solve (stereo chaining + bundle adjustment) off the UI thread.

    The synchronized stereoCalibrate passes and the SciPy bundle-adjustment refine
    can take a noticeable amount of time on a multi-camera rig, which previously
    froze the UI when the solve was triggered from the auto-capture completion.
    """

    result_ready = Signal(object)
    error = Signal(str)
    state_changed = Signal(str)

    def __init__(
        self,
        calibration_manager: CalibrationManager,
        base_bundle: CalibrationBundle | None,
        reference_source_id: str | None,
    ) -> None:
        super().__init__()
        self._calibration_manager = calibration_manager
        self._base_bundle = base_bundle
        self._reference_source_id = reference_source_id

    def run(self) -> None:
        try:
            self.state_changed.emit("extrinsics_solve_started")
            bundle = self._calibration_manager.solve_extrinsics(
                base_bundle=self._base_bundle,
                reference_source_id=self._reference_source_id,
            )
            self.result_ready.emit(bundle)
            self.state_changed.emit("extrinsics_solve_finished")
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Extrinsics solve failed.")
            self.error.emit(str(exc))
            self.state_changed.emit("extrinsics_solve_failed")
