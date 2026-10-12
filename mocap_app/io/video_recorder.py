"""Per-camera video recording with a dedicated encoder thread."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

import cv2

from mocap_app.io.calibration_export import camera_sort_key
from mocap_app.io.recording_session import RecordingSession
from mocap_app.models.types import CameraSourceConfig, FramePacket

LOGGER = logging.getLogger(__name__)

# Only rewrite the clip to the measured frame rate when it deviates from the
# nominal one by more than this fraction; small jitter is not worth a re-encode.
_FPS_REENCODE_TOLERANCE = 0.03

# Bounded hand-off between the capture thread and the encoder thread. At 25 FPS
# this buffers a little over two seconds of batches; if encoding cannot keep up
# we drop the newest batch instead of stalling the live capture loop.
_WRITE_QUEUE_MAX_BATCHES = 64


class VideoRecorder:
    """Writes incoming live frames to one video file per camera source.

    The recorder lazily opens a :class:`cv2.VideoWriter` for each source the
    first time a frame for that source arrives, so the file dimensions always
    match the captured frames. It is fed the full capture-resolution frames
    (before any preview downscaling) so the clips are full quality and clean
    (no overlays, mirroring or undistortion), ready to validate the
    calibration in external tooling.

    Encoding runs on a dedicated writer thread: ``write_frames`` queues images
    and journals failed capture/drop outcomes without waiting for video encode. When the
    encoder cannot keep up the newest batches are dropped; the measured frame
    rate can correct average playback speed, but cannot recover lost frames or
    synchronization. The journal retains the actual gaps for later analysis.
    """

    def __init__(
        self,
        output_dir: Path,
        fps: float,
        labels: dict[str, str] | None = None,
        *,
        sources: list[CameraSourceConfig] | None = None,
        calibration_payload: dict[str, Any] | None = None,
    ) -> None:
        """Create the output folder and start the encoder thread.

        @param output_dir  Folder that receives one clip per source.
        @param fps         Nominal frame rate the clips are written at.
        @param labels      Optional user-facing names, stored in the manifest.
        @param sources     Explicit camera order is derived from their unique IDs.
        @param calibration_payload Immutable calibration snapshot for this session.
        """
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        if any(self._output_dir.iterdir()):
            raise FileExistsError("Recording folder must be empty; existing files are preserved.")
        self._fps = max(1.0, float(fps))
        self._labels = dict(labels or {})
        self._session = RecordingSession(self._output_dir, self._fps, self._labels, sources, calibration_payload)
        self._state_lock = threading.RLock()
        self._batch_index = 0
        self._writer_error: Exception | None = None
        self._writers: dict[str, cv2.VideoWriter] = {}
        self._paths: dict[str, Path] = {}
        self._sizes: dict[str, tuple[int, int]] = {}
        self._frame_counts: dict[str, int] = {}
        # Monotonic span of successfully encoded frames, used for average frame
        # rate so the saved clip plays back at the correct speed (the nominal fps
        # is rarely achieved exactly by the capture loop).
        self._first_frame_at: float | None = None
        self._last_frame_at: float | None = None
        self._dropped_batches = 0
        self._dropped_frames = 0
        self._closed = False
        self._write_queue: queue.Queue[tuple[dict[str, Any], dict[str, dict[str, Any]]] | None] = queue.Queue(
            maxsize=_WRITE_QUEUE_MAX_BATCHES
        )
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="video-recorder-writer", daemon=True
        )
        self._writer_thread.start()

    @property
    def output_dir(self) -> Path:
        """The folder the clips are written to."""
        return self._output_dir

    def _safe_name(self, source_id: str) -> str:
        """Canonical names follow manifest order; labels never affect paths."""
        return self._session.camera(source_id)['video_stem']

    def _ensure_writer(self, source_id: str, frame) -> cv2.VideoWriter | None:
        """Return the writer for ``source_id``, opening it on first use.

        The clip dimensions are taken from the first frame. Tries MP4 (mp4v)
        first and falls back to AVI (XVID); returns ``None`` when neither
        opens, which skips recording for this source.
        """
        writer = self._writers.get(source_id)
        if writer is not None:
            return writer
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            return None

        name = self._safe_name(source_id)
        path = self._output_dir / f"{name}.mp4"
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            self._fps,
            (int(width), int(height)),
        )
        if not writer.isOpened():
            writer.release()
            # Fall back to a more universally available codec/container.
            path = self._output_dir / f"{name}.avi"
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"XVID"),
                self._fps,
                (int(width), int(height)),
            )
        if not writer.isOpened():
            writer.release()
            LOGGER.error("Could not open a VideoWriter for source '%s' (%dx%d).", source_id, width, height)
            return None

        self._writers[source_id] = writer
        self._paths[source_id] = path
        self._sizes[source_id] = (int(width), int(height))
        self._frame_counts[source_id] = 0
        self._session.video_opened(source_id, path, self._sizes[source_id], self._fps)
        return writer

    def write_frames(
        self, frames: dict[str, Any], *, packets: dict[str, FramePacket] | None = None,
        failures: dict[str, dict[str, Any]] | None = None, batch_id: str | None = None,
        capture_batch_index: int | None = None,
    ) -> None:
        """Hand off images without waiting for encoding; journal missing frames.

        Small metadata writes still use disk I/O. Every known camera gets one
        terminal outcome per submitted batch, including all-camera failures.
        Calls without packets remain supported, with handoff timestamps rather
        than fabricated sensor/read timestamps.
        """
        if not frames and not failures:
            return
        with self._state_lock:
            if self._closed:
                return
            if self._writer_error is not None:
                raise RuntimeError('Recording journal/encoder failed.') from self._writer_error
            packets, failures = packets or {}, failures or {}
            for source_id in sorted(set(frames) | set(failures), key=camera_sort_key):
                self._session.camera(source_id)
            for source_id, packet in packets.items():
                if packet.source_id != source_id:
                    raise ValueError('Frame packet camera ID does not match its dictionary key.')
            self._batch_index += 1
            submitted_ns = time.perf_counter_ns()
            batch_id = batch_id or f"recording_{self._batch_index}"
            rows: dict[str, dict[str, Any]] = {}
            valid_frames: dict[str, Any] = {}
            for source_id in self._session.camera_ids:
                packet = packets.get(source_id)
                failure = failures.get(source_id, {})
                started = (packet.capture_started_monotonic_ns if packet else failure.get('capture_started_monotonic_ns'))
                completed = (packet.capture_completed_monotonic_ns if packet else failure.get('capture_completed_monotonic_ns'))
                has_read_time = started is not None and completed is not None
                row = {
                    'camera_id': source_id, 'batch_id': batch_id, 'batch_index': self._batch_index,
                    'capture_batch_index': capture_batch_index,
                    'source_frame_index': packet.frame_index if packet else None,
                    'capture_started_monotonic_ns': started, 'capture_completed_monotonic_ns': completed,
                    'timestamp_monotonic_ns': (started + completed) // 2 if has_read_time else submitted_ns,
                    'timestamp_kind': 'host_read_midpoint' if has_read_time else 'recorder_handoff',
                    'timestamp_unix_sec': packet.timestamp_sec if packet else failure.get('timestamp_sec'),
                    'submitted_monotonic_ns': submitted_ns,
                }
                rows[source_id] = row
                if source_id in failures or frames.get(source_id) is None:
                    reason = failure.get('reason', 'frame_not_received')
                    self._session.event(row, 'capture_failed', reason=reason)
                    self._dropped_frames += 1
                else:
                    valid_frames[source_id] = frames[source_id]
            if not valid_frames:
                return
            queued_rows = {key: rows[key] for key in valid_frames}
            try:
                self._write_queue.put_nowait((dict(valid_frames), queued_rows))
            except queue.Full:
                self._dropped_batches += 1
                self._dropped_frames += len(valid_frames)
                for row in queued_rows.values():
                    self._session.event(row, 'queue_dropped', reason='encoder_queue_full')

    def _writer_loop(self) -> None:
        """Drain images; the same thread owns writer release and journal close."""
        try:
            while True:
                item = self._write_queue.get()
                if item is None:
                    break
                frames, rows = item
                for source_id, frame in frames.items():
                    row = rows[source_id]
                    try:
                        index, resized = self._write_frame(source_id, frame)
                    except Exception as exc:  # keep other cameras recording
                        LOGGER.exception("Failed to encode a frame for source '%s'.", source_id)
                        with self._state_lock:
                            self._dropped_frames += 1
                        self._session.event(row, 'encode_failed', reason=str(exc))
                    else:
                        self._session.event(row, 'written', video_frame_index=index, resized=resized)
                        timestamp = row['timestamp_monotonic_ns'] / 1e9
                        with self._state_lock:
                            if self._first_frame_at is None:
                                self._first_frame_at = timestamp
                            self._last_frame_at = timestamp
        except Exception as exc:
            # Logging failure must be surfaced, not silently reported as success.
            self._writer_error = exc
            LOGGER.exception('Recording encoder/journal failed.')
        finally:
            try:
                for writer in self._writers.values():
                    writer.release()
                if self._writer_error is None:
                    self._session.finish()
            except Exception as exc:
                self._writer_error = exc
                LOGGER.exception('Recording files/manifest could not be finalized.')

    def _write_frame(self, source_id: str, frame: Any) -> tuple[int, bool]:
        """Submit to the codec and return its 1-based video index.

        VideoWriter does not acknowledge durable storage. The journal's
        ``written`` means the codec call returned; decoding remains the
        independent integrity check (also used by our video smoke test).
        """
        writer = self._ensure_writer(source_id, frame)
        if writer is None:
            raise RuntimeError('video_writer_unavailable')
        expected = self._sizes[source_id]
        resized = (int(frame.shape[1]), int(frame.shape[0])) != expected
        if resized:
            frame = cv2.resize(frame, expected)
        writer.write(frame)
        with self._state_lock:
            self._frame_counts[source_id] += 1
            return self._frame_counts[source_id], resized

    def total_frames(self) -> int:
        """Total number of frames encoded so far, summed over all sources."""
        return sum(self._frame_counts.values())

    def dropped_frames(self) -> int:
        """Known missing frames: capture failures, queue drops and encoder errors."""
        return self._dropped_frames

    def measured_fps(self) -> float | None:
        """Average frame rate actually achieved during the recording, or ``None``
        if too few frames were captured to estimate it."""
        if self._first_frame_at is None or self._last_frame_at is None:
            return None
        duration = self._last_frame_at - self._first_frame_at
        max_frames = max(self._frame_counts.values(), default=0)
        if duration <= 0.0 or max_frames < 2:
            return None
        # ``max_frames`` frames span ``max_frames - 1`` inter-frame intervals.
        return (max_frames - 1) / duration

    def close(self) -> dict[str, Path]:
        """Drain and finalize videos and metadata before returning clip paths.

        The encoding thread owns releases: a slow encoder must never race
        against a main-thread release or re-encode of its still-open files.
        """
        with self._state_lock:
            send_stop = not self._closed
            self._closed = True
        if send_stop:
            # Do not wait for space while holding the state lock: the encoder
            # needs that lock to finish each queued image.
            while self._writer_thread.is_alive():
                try:
                    self._write_queue.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue
        self._writer_thread.join(timeout=30)
        if self._writer_thread.is_alive():
            raise TimeoutError('Encoder is still finalizing; recording files have not been released.')
        if self._writer_error is not None:
            raise RuntimeError('Recording could not be finalized reliably.') from self._writer_error
        if self._dropped_frames:
            LOGGER.warning('Recording has %d missing frame(s). See frames.jsonl.', self._dropped_frames)
        return {source_id: path for source_id, path in self._paths.items()
                if self._frame_counts.get(source_id, 0) > 0}

    def needs_frame_rate_correction(self) -> bool:
        """Whether the measured frame rate deviates from the nominal one enough
        to be worth re-encoding the clips (they were written at the nominal fps,
        so a slower real rate makes them play back too fast)."""
        measured = self.measured_fps()
        if measured is None:
            return False
        return abs(measured - self._fps) > self._fps * _FPS_REENCODE_TOLERANCE

    def correct_frame_rate(self, written: dict[str, Path]) -> None:
        """Re-encode the given clips to the real measured frame rate.

        Safe to call from a background thread: the writers are already released
        by :meth:`close`, and each clip is rewritten via a temp file. Does
        nothing when no correction is needed.
        """
        if not self.needs_frame_rate_correction():
            return
        measured = self.measured_fps()
        if measured is None:
            return
        for source_id, path in list(written.items()):
            size = self._sizes.get(source_id)
            if size is None:
                continue
            self._session.correction(source_id, None, 'in_progress')
            try:
                success = self._reencode_to_fps(path, measured, size)
            except Exception:
                self._session.correction(source_id, None, 'failed')
                raise
            self._session.correction(source_id, measured if success else None,
                                     'corrected' if success else 'failed')

    def _reencode_to_fps(self, path: Path, fps: float, size: tuple[int, int]) -> bool:
        """Rewrite ``path`` at ``fps`` so it plays back at real-time speed.

        Reads the just-written clip back frame by frame and writes a new file at
        the corrected rate, then atomically replaces the original. Best effort:
        on any failure the original clip is left untouched.
        """
        fps = max(1.0, float(fps))
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            LOGGER.warning("Could not reopen '%s' to correct its frame rate.", path)
            return False

        fourcc_code = "mp4v" if path.suffix.lower() == ".mp4" else "XVID"
        tmp_path = path.with_name(f"{path.stem}_fps_tmp{path.suffix}")
        writer = cv2.VideoWriter(str(tmp_path), cv2.VideoWriter_fourcc(*fourcc_code), fps, size)
        if not writer.isOpened():
            cap.release()
            writer.release()
            LOGGER.warning("Could not open a re-encode writer for '%s'.", path)
            return False

        frame_count = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(frame)
                frame_count += 1
        finally:
            cap.release()
            writer.release()

        source_id = next(key for key, value in self._paths.items() if value == path)
        if frame_count != self._frame_counts[source_id]:
            tmp_path.unlink(missing_ok=True)
            LOGGER.warning('Frame-rate correction would lose frames; keeping original %s.', path)
            return False
        try:
            os.replace(tmp_path, path)
            LOGGER.info("Re-encoded '%s' to %.2f fps (measured).", path.name, fps)
            return True
        except OSError:
            LOGGER.exception("Could not replace '%s' with its re-encoded version.", path)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

            return False
