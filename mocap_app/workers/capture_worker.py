"""Background thread that grabs synchronised frames from all camera sources."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import cv2
from PySide6.QtCore import QThread, Signal

from mocap_app.io.video_recorder import VideoRecorder
from mocap_app.models.types import CameraSourceConfig, FramePacket


LOGGER = logging.getLogger(__name__)


class LiveCaptureWorker(QThread):
    """Captures frames from all enabled sources in a paced grab/retrieve loop.

    Each loop iteration first ``grab()``-s every camera and only then
    ``retrieve()``-s the images, so the frames within a batch are taken as
    close together in time as possible. The full capture-resolution frames are
    emitted as a ``dict[str, FramePacket]`` batch via ``batch_ready``; preview
    downscaling happens later in the UI layer. While a recorder is attached the
    same full-resolution frames are also queued for encoding.
    """

    ## Emitted with a ``dict[str, FramePacket]`` for every captured batch.
    batch_ready = Signal(object)
    ## Lifecycle marker: ``"live_started"`` / ``"live_stopped"``.
    state_changed = Signal(str)
    ## Emitted with a human-readable message on capture failures.
    error = Signal(str)

    def __init__(
        self,
        sources: list[CameraSourceConfig],
        target_fps: float,
        requested_width: int = 0,
        requested_height: int = 0,
    ) -> None:
        """@param sources           Camera sources to open (webcams and/or videos).
        @param target_fps        Pace of the capture loop in frames per second.
        @param requested_width   Capture width to request from webcams (0 = driver default).
        @param requested_height  Capture height to request from webcams (0 = driver default).
        """
        super().__init__()
        self._sources = sources
        self._target_fps = max(1.0, target_fps)
        self._requested_width = max(0, int(requested_width))
        self._requested_height = max(0, int(requested_height))
        self._stop_event = threading.Event()
        self._recorder_lock = threading.Lock()
        self._recorder: VideoRecorder | None = None

    def stop(self) -> None:
        """Ask the capture loop to exit; the thread finishes its current
        iteration and releases the captures."""
        self._stop_event.set()

    def attach_recorder(self, recorder: VideoRecorder) -> None:
        """Start recording the full-resolution captured frames."""
        with self._recorder_lock:
            self._recorder = recorder

    def detach_recorder(self) -> VideoRecorder | None:
        """Stop recording and return the active recorder, if any."""
        with self._recorder_lock:
            recorder = self._recorder
            self._recorder = None
        return recorder

    def run(self) -> None:
        """Open all sources, then capture batches until stop() is called.

        Webcams that fail a ``grab()`` fall back to a direct ``read()``; video
        files loop back to their first frame when they run out. Failures are
        reported via the ``error`` signal without ending the loop.
        """
        captures: dict[str, cv2.VideoCapture] = {}
        source_by_id: dict[str, CameraSourceConfig] = {source.source_id: source for source in self._sources}
        frame_indices: dict[str, int] = {source.source_id: 0 for source in self._sources}
        frame_interval = 1.0 / self._target_fps
        batch_index = 0

        try:
            for source in self._sources:
                uri: Any = source.uri
                if source.kind == "webcam" and isinstance(uri, str) and uri.isdigit():
                    uri = int(uri)

                capture = self._open_capture(uri=uri, kind=source.kind)
                if not capture.isOpened():
                    raise RuntimeError(f"Could not open source '{source.source_id}' ({source.uri}).")
                self._configure_capture(capture=capture, kind=source.kind)
                captures[source.source_id] = capture

            self.state_changed.emit("live_started")
            LOGGER.info("Live capture started with %d source(s).", len(captures))

            while not self._stop_event.is_set():
                loop_start = time.perf_counter()
                batch_timestamp_sec = time.time()
                batch_index += 1
                batch_id = f"live_{int(batch_timestamp_sec * 1000)}_{batch_index}"
                batch: dict[str, FramePacket] = {}
                record_batch: dict[str, Any] = {}
                grabbed_sources: list[str] = []
                direct_frames: dict[str, Any] = {}
                capture_started_by_source: dict[str, float] = {}
                capture_completed_by_source: dict[str, float] = {}

                for source_id, capture in captures.items():
                    capture_started = time.time()
                    capture_started_by_source[source_id] = capture_started
                    try:
                        ok = bool(capture.grab())
                    except Exception:  # noqa: BLE001 - keep capture alive if a backend lacks grab support
                        LOGGER.exception("Capture grab failed for source '%s'; falling back to read().", source_id)
                        ok = False
                    if not ok:
                        source = source_by_id[source_id]
                        if source.kind == "video":
                            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                        read_ok, frame = capture.read()
                        capture_completed_by_source[source_id] = time.time()
                        if not read_ok:
                            self.error.emit(f"Capture read failed for source '{source_id}'.")
                            continue
                        # Copy so the frame is independent of any internal capture
                        # buffer the backend may reuse on the next grab (avoids
                        # tearing/smearing once it crosses to the UI thread).
                        direct_frames[source_id] = frame.copy()
                        continue
                    grabbed_sources.append(source_id)

                for source_id in grabbed_sources:
                    capture = captures[source_id]
                    ok, frame = capture.retrieve()
                    capture_completed_by_source[source_id] = time.time()
                    if not ok:
                        self.error.emit(f"Capture retrieve failed for source '{source_id}'.")
                        continue
                    # Copy so the frame is independent of any internal capture
                    # buffer the backend may reuse on the next grab (avoids
                    # tearing/smearing once it crosses to the UI thread).
                    direct_frames[source_id] = frame.copy()

                for source_id, frame in direct_frames.items():
                    capture_started = capture_started_by_source.get(source_id, batch_timestamp_sec)
                    capture_completed = capture_completed_by_source.get(source_id, time.time())
                    timestamp_sec = (capture_started + capture_completed) / 2.0

                    frame_indices[source_id] += 1
                    # Emit the full capture-resolution frame. Detection, calibration
                    # and recording all use this; preview downscaling for display
                    # happens later in the UI layer.
                    record_batch[source_id] = frame
                    batch[source_id] = FramePacket(
                        source_id=source_id,
                        frame_index=frame_indices[source_id],
                        timestamp_sec=timestamp_sec,
                        frame_bgr=frame,
                        batch_id=batch_id,
                        batch_timestamp_sec=batch_timestamp_sec,
                        capture_started_sec=capture_started,
                        capture_completed_sec=capture_completed,
                    )

                if record_batch:
                    with self._recorder_lock:
                        if self._recorder is not None:
                            self._recorder.write_frames(record_batch)

                if batch:
                    self.batch_ready.emit(batch)

                elapsed = time.perf_counter() - loop_start
                delay = frame_interval - elapsed
                if delay > 0:
                    time.sleep(delay)
        except Exception as exc:  # pragma: no cover - UI surface area
            LOGGER.exception("Live capture worker failed.")
            self.error.emit(str(exc))
        finally:
            for capture in captures.values():
                capture.release()
            self.state_changed.emit("live_stopped")
            LOGGER.info("Live capture stopped.")

    def _open_capture(self, uri: Any, kind: str):
        """Open a ``cv2.VideoCapture`` for ``uri``, trying the preferred
        Windows backends (DSHOW, then MSMF) for webcam indices.

        @param uri   Device index or file path/URL.
        @param kind  Source kind (``"webcam"`` or ``"video"``).
        @return      The first capture that reports itself as opened.
        """
        if kind != "webcam" or not isinstance(uri, int):
            return cv2.VideoCapture(uri)

        backends: list[int | None] = []
        if os.name == "nt":
            if hasattr(cv2, "CAP_DSHOW"):
                backends.append(cv2.CAP_DSHOW)
            if hasattr(cv2, "CAP_MSMF"):
                backends.append(cv2.CAP_MSMF)
        backends.append(None)

        for backend in backends:
            capture = cv2.VideoCapture(uri, backend) if backend is not None else cv2.VideoCapture(uri)
            if capture.isOpened():
                return capture
            capture.release()
        return cv2.VideoCapture(uri)

    def _configure_capture(self, capture: cv2.VideoCapture, kind: str) -> None:
        """Apply the requested resolution, MJPG pixel format and FPS to a
        webcam capture (video files are left at their native settings), and
        log when the camera silently falls back to another resolution.

        @param capture  The opened capture to configure.
        @param kind     Source kind (``"webcam"`` or ``"video"``).
        """
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if kind != "webcam":
            # Video files must be decoded at their native resolution and frame
            # rate; forcing a capture size/FPS on a file is ignored by some
            # backends and could silently downscale on others, degrading the
            # calibration. The read loop already paces an uploaded video at the
            # configured FPS, and every decoded frame is processed in order.
            return

        # Set the resolution before the FOURCC: several Windows drivers reset the
        # pixel format when the frame size changes, which silently undoes an
        # MJPG request and drops the camera back to uncompressed YUY2 (lower
        # maximum FPS at high resolutions).
        if self._requested_width > 0:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._requested_width))
        if self._requested_height > 0:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._requested_height))
        if hasattr(cv2, "VideoWriter_fourcc"):
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FPS, float(self._target_fps))

        # Surface the resolution the camera actually delivers: webcams silently
        # fall back to a supported mode when the requested size is unavailable, so
        # the calibration may run at a different resolution than configured.
        if self._requested_width > 0 or self._requested_height > 0:
            actual_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            actual_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if actual_w > 0 and actual_h > 0 and (
                actual_w != self._requested_width or actual_h != self._requested_height
            ):
                LOGGER.warning(
                    "Camera did not honour requested capture resolution "
                    "%dx%d; using %dx%d instead.",
                    self._requested_width,
                    self._requested_height,
                    actual_w,
                    actual_h,
                )
