"""Background re-encode of finished recordings to their real frame rate."""

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

    ## Emitted when all clips were corrected (or none needed correction).
    finished_ok = Signal()
    ## Emitted with a human-readable message when the re-encode fails.
    error = Signal(str)

    def __init__(self, recorder: VideoRecorder, written: dict[str, Path]) -> None:
        """@param recorder  The (already closed) recorder that wrote the clips.
        @param written   Clip paths per source id, as returned by ``VideoRecorder.close()``.
        """
        super().__init__()
        self._recorder = recorder
        self._written = dict(written)

    def run(self) -> None:
        """Run the frame-rate correction and emit the outcome."""
        try:
            self._recorder.correct_frame_rate(self._written)
            self.finished_ok.emit()
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Recording frame-rate correction failed.")
            self.error.emit(str(exc))
