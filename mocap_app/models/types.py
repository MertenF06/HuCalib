from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray


FrameArray = NDArray[np.uint8]
SourceKind = Literal["webcam", "video"]


@dataclass(slots=True)
class CameraSourceConfig:
    source_id: str
    kind: SourceKind
    uri: int | str
    label: str = ""
    enabled: bool = True


@dataclass(slots=True)
class FramePacket:
    source_id: str
    frame_index: int
    timestamp_sec: float
    frame_bgr: FrameArray
    batch_id: str | None = None
    batch_timestamp_sec: float | None = None
    capture_started_sec: float | None = None
    capture_completed_sec: float | None = None


@dataclass(slots=True)
class CameraCalibration:
    source_id: str
    intrinsics: list[list[float]] | None = None
    distortion: list[float] | None = None
    rotation: list[float] | None = None
    translation: list[float] | None = None
    image_size: tuple[int, int] | None = None
    reprojection_error: float | None = None
    num_samples: int = 0
    status: str = "unsolved"
    diagnostics: list[str] = field(default_factory=list)
    calibrated_at_iso: str | None = None


@dataclass(slots=True)
class CalibrationBundle:
    cameras: dict[str, CameraCalibration] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CalibrationBoardSettings:
    chessboard_cols: int = 9
    chessboard_rows: int = 6
    chessboard_square_size_m: float = 0.024
    charuco_squares_x: int = 5
    charuco_squares_y: int = 3
    charuco_square_size_m: float = 0.077
    charuco_marker_size_m: float = 0.061


@dataclass(slots=True)
class RuntimeTuning:
    """Runtime performance and workload controls surfaced by the UI."""

    capture_fps: float = 30.0
    capture_width: int = 0
    capture_height: int = 0
    preview_fps: float = 30.0
    preview_max_width: int = 640
    preview_max_height: int = 480
    calibration_detection_hz: float = 5.0


@dataclass(slots=True)
class CameraProbeResult:
    """A webcam index that was successfully opened during probing."""

    index: int
    width: int = 0
    height: int = 0
    backend: str = ""
