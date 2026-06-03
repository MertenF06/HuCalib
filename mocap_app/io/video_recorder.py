from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2

LOGGER = logging.getLogger(__name__)


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

    def write_frames(self, frames: dict[str, Any]) -> None:
        for source_id, frame in frames.items():
            self.write_frame(source_id, frame)

    def total_frames(self) -> int:
        return sum(self._frame_counts.values())

    def close(self) -> dict[str, Path]:
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
