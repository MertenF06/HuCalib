from __future__ import annotations

import logging
import os
import threading

import cv2
from PySide6.QtCore import QThread, Signal

from mocap_app.models.types import CameraProbeResult


LOGGER = logging.getLogger(__name__)


class CameraProbeWorker(QThread):
    """Scans webcam indices in a background thread."""

    result_ready = Signal(object)
    error = Signal(str)
    state_changed = Signal(str)

    def __init__(self, max_index: int = 10) -> None:
        super().__init__()
        self._max_index = max(0, int(max_index))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            self.state_changed.emit("camera_probe_started")
            results: list[CameraProbeResult] = []
            for index in range(self._max_index + 1):
                if self._stop_event.is_set():
                    self.state_changed.emit("camera_probe_stopped")
                    return
                capture = self._open_capture(index)
                if capture is None or not capture.isOpened():
                    if capture is not None:
                        capture.release()
                    continue

                # Keep probing light and fast.
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                ok, frame = capture.read()
                width = 0
                height = 0
                if ok and frame is not None:
                    height, width = frame.shape[:2]
                else:
                    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                backend = ""
                if hasattr(capture, "getBackendName"):
                    try:
                        backend = str(capture.getBackendName())
                    except Exception:
                        backend = ""
                capture.release()
                results.append(
                    CameraProbeResult(
                        index=index,
                        opened=True,
                        width=width,
                        height=height,
                        backend=backend,
                    )
                )

            if self._stop_event.is_set():
                self.state_changed.emit("camera_probe_stopped")
                return
            self.result_ready.emit(results)
            self.state_changed.emit("camera_probe_finished")
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Camera probe failed.")
            self.error.emit(str(exc))
            self.state_changed.emit("camera_probe_failed")

    def _open_capture(self, index: int):
        backends: list[int | None] = []
        if os.name == "nt":
            if hasattr(cv2, "CAP_MSMF"):
                backends.append(cv2.CAP_MSMF)
            if hasattr(cv2, "CAP_DSHOW"):
                backends.append(cv2.CAP_DSHOW)
        backends.append(None)

        for backend in backends:
            capture = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
            if capture.isOpened():
                return capture
            capture.release()
        return None
