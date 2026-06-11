"""Shared data types passed between the capture, calibration and UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray


## BGR image as an OpenCV-compatible ``uint8`` array (height x width x 3).
FrameArray = NDArray[np.uint8]
## Discriminator for camera sources: a live webcam or a video file.
SourceKind = Literal["webcam", "video"]


@dataclass(slots=True)
class CameraSourceConfig:
    """Describes one camera input feeding the capture pipeline.

    - ``source_id``: stable identifier used as the camera key throughout the app.
    - ``kind``: ``"webcam"`` (live device) or ``"video"`` (file playback).
    - ``uri``: device index for webcams, file path for video files.
    - ``label``: user-facing display name (empty means "use the source id").
    - ``enabled``: whether the source takes part in capture.
    """

    source_id: str
    kind: SourceKind
    uri: int | str
    label: str = ""
    enabled: bool = True


@dataclass(slots=True)
class FramePacket:
    """A single captured frame plus its acquisition metadata.

    - ``source_id``: camera that produced the frame.
    - ``frame_index``: 1-based counter per source within the capture run.
    - ``timestamp_sec``: wall-clock capture time, taken as the midpoint of the
      sensor read (between ``capture_started_sec`` and ``capture_completed_sec``).
    - ``frame_bgr``: full capture-resolution image data (OpenCV BGR layout).
    - ``batch_id``: identifier shared by all frames grabbed in the same
      synchronised capture round, or ``None`` outside batch capture.
    - ``batch_timestamp_sec``: wall-clock time at the start of the batch.
    - ``capture_started_sec`` / ``capture_completed_sec``: wall-clock times
      bracketing the actual sensor read, for latency diagnostics.
    """

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
    """Solved (or partially solved) calibration parameters for one camera.

    - ``intrinsics``: 3x3 camera matrix as nested lists, or ``None`` when unsolved.
    - ``distortion``: lens distortion coefficients.
    - ``rotation`` / ``translation``: extrinsic pose relative to the reference
      camera (rotation as 3x3 matrix or 3-element Rodrigues vector).
    - ``image_size``: (width, height) the calibration was solved at.
    - ``reprojection_error``: RMS reprojection error in pixels.
    - ``num_samples``: number of board detections used by the solve.
    - ``status``: human-readable solver state (starts as ``"unsolved"``).
    - ``diagnostics``: solver remarks shown to the user.
    - ``calibrated_at_iso``: ISO-8601 timestamp of the last successful solve.
    """

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
    """Complete calibration result for a camera rig: per-camera parameters
    keyed by source id, plus free-form notes and metadata. This is the unit
    that is saved to and loaded from disk."""

    cameras: dict[str, CameraCalibration] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CalibrationBoardSettings:
    """Physical layout of the supported calibration boards.

    Chessboard sizes are in *inner corners* (columns x rows); ChArUco sizes are
    in squares. All physical dimensions are in metres.
    """

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
