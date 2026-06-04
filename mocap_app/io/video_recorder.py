from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import cv2

LOGGER = logging.getLogger(__name__)

# Only rewrite the clip to the measured frame rate when it deviates from the
# nominal one by more than this fraction; small jitter is not worth a re-encode.
_FPS_REENCODE_TOLERANCE = 0.03


class VideoRecorder:
    """Writes incoming live frames to one video file per camera source.

    The recorder lazily opens a :class:`cv2.VideoWriter` for each source the
    first time a frame for that source arrives, so the file dimensions always
    match the captured frames. It is fed the full capture-resolution frames
    (before any preview downscaling) so the clips are full quality and clean
    (no overlays, mirroring or undistortion), ready to validate the
    calibration in external tooling.
    """

    def __init__(
        self,
        output_dir: Path,
        fps: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._fps = max(1.0, float(fps))
        self._labels = dict(labels or {})
        self._writers: dict[str, cv2.VideoWriter] = {}
        self._paths: dict[str, Path] = {}
        self._sizes: dict[str, tuple[int, int]] = {}
        self._frame_counts: dict[str, int] = {}
        # Wall-clock span of the recording, used to derive the real average frame
        # rate so the saved clip plays back at the correct speed (the nominal fps
        # is rarely achieved exactly by the capture loop).
        self._first_frame_at: float | None = None
        self._last_frame_at: float | None = None

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def _safe_name(self, source_id: str) -> str:
        label = self._labels.get(source_id, source_id)
        cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label)
        cleaned = cleaned.strip("_")
        return cleaned or source_id

    def _ensure_writer(self, source_id: str, frame) -> cv2.VideoWriter | None:
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
            # Fall back to a more universally available codec/container.
            path = self._output_dir / f"{name}.avi"
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"XVID"),
                self._fps,
                (int(width), int(height)),
            )
        if not writer.isOpened():
            LOGGER.error("Could not open a VideoWriter for source '%s' (%dx%d).", source_id, width, height)
            return None

        self._writers[source_id] = writer
        self._paths[source_id] = path
        self._sizes[source_id] = (int(width), int(height))
        self._frame_counts[source_id] = 0
        return writer

    def write_frame(self, source_id: str, frame: Any) -> None:
        if frame is None:
            return
        writer = self._ensure_writer(source_id, frame)
        if writer is None:
            return
        expected = self._sizes[source_id]
        if (int(frame.shape[1]), int(frame.shape[0])) != expected:
            frame = cv2.resize(frame, expected)
        writer.write(frame)
        self._frame_counts[source_id] += 1
        now = time.perf_counter()
        if self._first_frame_at is None:
            self._first_frame_at = now
        self._last_frame_at = now

    def write_frames(self, frames: dict[str, Any]) -> None:
        for source_id, frame in frames.items():
            self.write_frame(source_id, frame)

    def total_frames(self) -> int:
        return sum(self._frame_counts.values())

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
        """Release the writers and return the written clip paths.

        This only stops writing; correcting the clips to the real measured frame
        rate (see :meth:`needs_frame_rate_correction` / :meth:`correct_frame_rate`)
        is done separately so the potentially slow re-encode can run off the UI
        thread.
        """
        for writer in self._writers.values():
            try:
                writer.release()
            except Exception:  # noqa: BLE001 - best effort cleanup
                LOGGER.exception("Failed to release a VideoWriter cleanly.")
        written = {
            source_id: path
            for source_id, path in self._paths.items()
            if self._frame_counts.get(source_id, 0) > 0
        }
        self._writers.clear()
        return written

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
            self._reencode_to_fps(path, measured, size)

    def _reencode_to_fps(self, path: Path, fps: float, size: tuple[int, int]) -> None:
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
            return

        fourcc_code = "mp4v" if path.suffix.lower() == ".mp4" else "XVID"
        tmp_path = path.with_name(f"{path.stem}_fps_tmp{path.suffix}")
        writer = cv2.VideoWriter(str(tmp_path), cv2.VideoWriter_fourcc(*fourcc_code), fps, size)
        if not writer.isOpened():
            cap.release()
            LOGGER.warning("Could not open a re-encode writer for '%s'.", path)
            return

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(frame)
        finally:
            cap.release()
            writer.release()

        try:
            os.replace(tmp_path, path)
            LOGGER.info("Re-encoded '%s' to %.2f fps (measured).", path.name, fps)
        except OSError:
            LOGGER.exception("Could not replace '%s' with its re-encoded version.", path)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
