from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from mocap_app.io.calibration_io import CalibrationManager, ChessboardDetectionResult


LOGGER = logging.getLogger(__name__)


class CalibrationDetectionWorker(QObject):
    """Runs calibration pattern detection off the UI thread.

    ``detect_pattern`` only reads from the calibration manager (board config and
    the charuco board objects); it never mutates the captured samples or capture
    sets. Combined with OpenCV releasing the GIL during the heavy C++ detection
    work, this lets detection run concurrently with the UI thread that owns
    sample capture and solving, without blocking the Qt event loop.

    Each request carries the exact frames to detect on plus an opaque
    ``frames_snapshot`` that the UI thread pairs back with the result, so the
    stored corners and the frames used for auto-capture always belong to the
    same instant.
    """

    result_ready = Signal(object)

    def __init__(self, manager: CalibrationManager) -> None:
        super().__init__()
        self._manager = manager

    @Slot(object)
    def run_detection(self, payload: object) -> None:
        try:
            request = dict(payload)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        frames: dict[str, Any] = request.get("frames") or {}
        pattern = request.get("pattern")
        detections: dict[str, ChessboardDetectionResult] = {}
        for source_id, frame_bgr in frames.items():
            try:
                detections[source_id] = self._manager.detect_pattern(
                    source_id=source_id,
                    frame_bgr=frame_bgr,
                    pattern=pattern,
                )
            except Exception:  # noqa: BLE001 - a detection failure must not kill the worker
                LOGGER.exception("Detection failed for source '%s'.", source_id)
        self.result_ready.emit(
            {
                "detections": detections,
                "frames_snapshot": request.get("frames_snapshot") or {},
                "token": request.get("token"),
            }
        )
