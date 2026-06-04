from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from mocap_app.io.video_recorder import VideoRecorder


LOGGER = logging.getLogger(__name__)


class RecordingFinalizeWorker(QThread):
    """Re-encodes recorded clips to their real measured frame rate off the UI thread.

    Writing happens at the nominal fps while recording; if the capture loop ran
    slower, the clips play back too fast. Correcting that means reading each clip
    back and re-encoding it, which can take seconds for long multi-camera
    recordings, so it runs here instead of blocking the UI.
    """

    finished_ok = Signal()
    error = Signal(str)

    def __init__(self, recorder: VideoRecorder, written: dict[str, Path]) -> None:
        super().__init__()
        self._recorder = recorder
        self._written = dict(written)

    def run(self) -> None:
        try:
            self._recorder.correct_frame_rate(self._written)
            self.finished_ok.emit()
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Recording frame-rate correction failed.")
            self.error.emit(str(exc))
