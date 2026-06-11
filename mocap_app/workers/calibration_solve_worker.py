"""Background threads for the intrinsics and extrinsics calibration solves."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from mocap_app.io.calibration_io import CalibrationManager
from mocap_app.models.types import CalibrationBundle


LOGGER = logging.getLogger(__name__)


class IntrinsicsSolveWorker(QThread):
    """Runs the potentially expensive OpenCV intrinsics solve off the UI thread."""

    ## Emitted with the updated ``CalibrationBundle`` on success.
    result_ready = Signal(object)
    ## Emitted with a human-readable message when the solve fails.
    error = Signal(str)
    ## Lifecycle marker: ``intrinsics_solve_started/finished/failed``.
    state_changed = Signal(str)
    ## (completed_cameras, total_cameras) so the UI can show a percentage.
    progress = Signal(int, int)

    def __init__(self, calibration_manager: CalibrationManager) -> None:
        """@param calibration_manager  Manager holding the captured samples to solve."""
        super().__init__()
        self._calibration_manager = calibration_manager

    def run(self) -> None:
        """Solve the per-camera intrinsics and emit the resulting bundle."""
        try:
            self.state_changed.emit("intrinsics_solve_started")
            bundle = self._calibration_manager.solve_intrinsics(
                progress_cb=lambda done, total: self.progress.emit(done, total)
            )
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

    ## Emitted with the updated ``CalibrationBundle`` on success.
    result_ready = Signal(object)
    ## Emitted with a human-readable message when the solve fails.
    error = Signal(str)
    ## Lifecycle marker: ``extrinsics_solve_started/finished/failed``.
    state_changed = Signal(str)
    ## (placed_cameras, total_cameras) so the UI can show a percentage.
    progress = Signal(int, int)

    def __init__(
        self,
        calibration_manager: CalibrationManager,
        base_bundle: CalibrationBundle | None,
        reference_source_id: str | None,
    ) -> None:
        """@param calibration_manager  Manager holding the captured samples to solve.
        @param base_bundle          Bundle with solved intrinsics to extend, if any.
        @param reference_source_id  Camera that anchors the world origin, or ``None``
                                    to let the solver pick one.
        """
        super().__init__()
        self._calibration_manager = calibration_manager
        self._base_bundle = base_bundle
        self._reference_source_id = reference_source_id

    def run(self) -> None:
        """Solve the camera extrinsics and emit the resulting bundle."""
        try:
            self.state_changed.emit("extrinsics_solve_started")
            bundle = self._calibration_manager.solve_extrinsics(
                base_bundle=self._base_bundle,
                reference_source_id=self._reference_source_id,
                progress_cb=lambda done, total: self.progress.emit(done, total),
            )
            self.result_ready.emit(bundle)
            self.state_changed.emit("extrinsics_solve_finished")
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Extrinsics solve failed.")
            self.error.emit(str(exc))
            self.state_changed.emit("extrinsics_solve_failed")
