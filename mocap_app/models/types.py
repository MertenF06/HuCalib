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
class Pose2DKeypoint:
    name: str
    x: float
    y: float
    confidence: float


@dataclass(slots=True)
class Pose2D:
    source_id: str
    frame_index: int
    timestamp_sec: float
    keypoints: list[Pose2DKeypoint] = field(default_factory=list)

    def keypoints_by_name(self) -> dict[str, Pose2DKeypoint]:
        return {keypoint.name: keypoint for keypoint in self.keypoints}


@dataclass(slots=True)
class Pose3DKeypoint:
    name: str
    x: float
    y: float
    z: float
    confidence: float


@dataclass(slots=True)
class Pose3D:
    frame_index: int
    timestamp_sec: float
    keypoints: list[Pose3DKeypoint] = field(default_factory=list)


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
class SessionManifest:
    version: int
    session_id: str
    created_at_iso: str
    fps: float
    sources: list[CameraSourceConfig]
    video_files: dict[str, str]
    total_frames: int = 0
    pose_file: str | None = None
    calibration_file: str | None = None


@dataclass(slots=True)
class PipelineDebugInfo:
    detector_name: str
    triangulator_name: str
    active_cameras: int
    matched_keypoints: int
    reconstruction_mode: str = "unavailable"
    reconstructed_keypoints: int = 0
    mean_reprojection_error_px: float | None = None
    per_joint_reprojection_error_px: dict[str, float] = field(default_factory=dict)
    capture_latency_ms: float | None = None
    detection_ms: float = 0.0
    matching_ms: float = 0.0
    triangulation_ms: float = 0.0
    smoothing_ms: float = 0.0
    pipeline_ms: float = 0.0
    overlay_ms: float = 0.0
    display_ms: float = 0.0
    per_camera_fps: dict[str, float] = field(default_factory=dict)
    dropped_input_batches: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    frame_index: int
    timestamp_sec: float
    frames: dict[str, FramePacket]
    poses_2d: dict[str, Pose2D]
    pose_3d: Pose3D | None
    debug: PipelineDebugInfo
    reprojected_keypoints_px: dict[str, dict[str, tuple[float, float]]] = field(default_factory=dict)


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
    overlays_enabled: bool = True
    detection_capture_enabled: bool = False
    detection_reconstruction_enabled: bool = True
    detection_analysis_enabled: bool = False


@dataclass(slots=True)
class CameraProbeResult:
    index: int
    opened: bool
    width: int = 0
    height: int = 0
    backend: str = ""
