from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
from PySide6.QtCore import QThread, Signal

from mocap_app.models.types import FramePacket


LOGGER = logging.getLogger(__name__)


class SessionPlaybackWorker(QThread):
    batch_ready = Signal(object)
    progress = Signal(int, int)
    state_changed = Signal(str)
    error = Signal(str)

    def __init__(self, video_paths: dict[str, Path], fps: float, loop: bool = False) -> None:
        super().__init__()
        self._video_paths = video_paths
        self._fps = max(1.0, fps)
        self._loop = loop
        self._stop_event = threading.Event()
        self._paused_event = threading.Event()
        self._frame_index = 0

    def stop(self) -> None:
        self._stop_event.set()

    def pause(self) -> None:
        self._paused_event.set()
        self.state_changed.emit("playback_paused")

    def resume(self) -> None:
        self._paused_event.clear()
        self.state_changed.emit("playback_running")

    def run(self) -> None:
        captures: dict[str, cv2.VideoCapture] = {}
        total_frames = 0
        frame_interval = 1.0 / self._fps

        try:
            for source_id, video_path in self._video_paths.items():
                capture = cv2.VideoCapture(str(video_path))
                if not capture.isOpened():
                    raise RuntimeError(f"Could not open playback video: {video_path}")
                captures[source_id] = capture

            frame_counts = [
                int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                for capture in captures.values()
                if capture.get(cv2.CAP_PROP_FRAME_COUNT) > 0
            ]
            total_frames = min(frame_counts) if frame_counts else 0
            self.state_changed.emit("playback_running")

            while not self._stop_event.is_set():
                if self._paused_event.is_set():
                    time.sleep(0.03)
                    continue

                loop_start = time.perf_counter()
                timestamp_sec = time.time()
                batch_id = f"playback_{int(timestamp_sec * 1000)}_{self._frame_index}"
                batch: dict[str, FramePacket] = {}
                read_failed = False

                for source_id, capture in captures.items():
                    capture_started = time.time()
                    ok, frame = capture.read()
                    capture_completed = time.time()
                    if not ok:
                        read_failed = True
                        break
                    batch[source_id] = FramePacket(
                        source_id=source_id,
                        frame_index=self._frame_index,
                        timestamp_sec=(capture_started + capture_completed) / 2.0,
                        frame_bgr=frame,
                        batch_id=batch_id,
                        batch_timestamp_sec=timestamp_sec,
                        capture_started_sec=capture_started,
                        capture_completed_sec=capture_completed,
                    )

                if read_failed:
                    if self._loop:
                        for capture in captures.values():
                            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self._frame_index = 0
                        continue
                    break

                self.batch_ready.emit(batch)
                self.progress.emit(self._frame_index, total_frames)
                self._frame_index += 1

                elapsed = time.perf_counter() - loop_start
                delay = frame_interval - elapsed
                if delay > 0:
                    time.sleep(delay)
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Playback worker failed.")
            self.error.emit(str(exc))
        finally:
            for capture in captures.values():
                capture.release()
            self.state_changed.emit("playback_stopped")

