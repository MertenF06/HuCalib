from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from mocap_app.models.types import CalibrationBoardSettings, CalibrationBundle, CameraCalibration


LOGGER = logging.getLogger(__name__)
CALIBRATION_SCHEMA_VERSION = 2
DEFAULT_SPATIAL_GRID_SHAPE = (6, 4)
DEFAULT_MIN_SPATIAL_GRID_COVERAGE_RATIO = 0.70

FloatArray = NDArray[np.float32]
U8Array = NDArray[np.uint8]


@dataclass(slots=True)
class ChessboardDetectionResult:
    """Result of a chessboard detection pass on a single frame."""

    source_id: str
    found: bool
    image_size: tuple[int, int]
    pattern_type: Literal["chessboard", "charuco"] = "chessboard"
    corners: FloatArray | None = None
    charuco_ids: NDArray[np.int32] | None = None
    detected_corners: int = 0
    quality_score: float = 0.0
    coverage_ratio: float = 0.0
    sharpness_score: float = 0.0
    board_bbox_px: tuple[float, float, float, float] | None = None
    board_center_px: tuple[float, float] | None = None
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CalibrationSample:
    """Accepted calibration sample with quality metrics."""

    pattern_type: Literal["chessboard", "charuco"]
    object_points: FloatArray
    image_points: FloatArray
    charuco_ids: NDArray[np.int32] | None
    image_size: tuple[int, int]
    quality_score: float
    coverage_ratio: float
    sharpness_score: float
    captured_at_iso: str
    capture_group_id: str | None = None
    accepted_for_intrinsics: bool = True
    corner_points_px: list[tuple[float, float]] = field(default_factory=list)
    board_bbox_px: tuple[float, float, float, float] | None = None
    board_center_px: tuple[float, float] | None = None


@dataclass(slots=True)
class CalibrationCaptureSet:
    """Synchronized multi-camera capture used for pairwise extrinsics solving."""

    capture_group_id: str
    pattern_type: Literal["chessboard", "charuco"]
    samples_by_source: dict[str, CalibrationSample]
    captured_at_iso: str
    sync_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CalibrationCaptureFeedback:
    """User-facing feedback after attempting to capture a sample."""

    source_id: str
    accepted: bool
    sample_count: int
    message: str
    detection: ChessboardDetectionResult


class CalibrationRepository:
    """Reads and writes calibration profiles using an explicit JSON schema."""

    def to_payload(self, bundle: CalibrationBundle) -> dict:
        """Build the serializable calibration payload (shared by save and export)."""
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "saved_at_iso": datetime.now().isoformat(),
            "metadata": dict(bundle.metadata),
            "notes": list(bundle.notes),
            "cameras": {
                source_id: {
                    "source_id": camera.source_id,
                    "status": camera.status,
                    "num_samples": camera.num_samples,
                    "image_size": list(camera.image_size) if camera.image_size else None,
                    "intrinsics": camera.intrinsics,
                    "distortion": camera.distortion,
                    "rotation": camera.rotation,
                    "translation": camera.translation,
                    "reprojection_error": camera.reprojection_error,
                    "diagnostics": list(camera.diagnostics),
                    "calibrated_at_iso": camera.calibrated_at_iso,
                }
                for source_id, camera in bundle.cameras.items()
            },
        }

    def save(self, bundle: CalibrationBundle, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_payload(bundle)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        LOGGER.info("Calibration saved: %s", path)

    def load(self, path: Path) -> CalibrationBundle | None:
        if not path.exists():
            return None

        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = int(payload.get("schema_version", 1))

        if schema >= 2:
            cameras = {
                source_id: CameraCalibration(
                    source_id=str(camera_data.get("source_id", source_id)),
                    status=str(camera_data.get("status", "unsolved")),
                    num_samples=int(camera_data.get("num_samples", 0)),
                    image_size=tuple(camera_data["image_size"]) if camera_data.get("image_size") else None,
                    intrinsics=camera_data.get("intrinsics"),
                    distortion=camera_data.get("distortion"),
                    rotation=camera_data.get("rotation"),
                    translation=camera_data.get("translation"),
                    reprojection_error=(
                        float(camera_data["reprojection_error"])
                        if camera_data.get("reprojection_error") is not None
                        else None
                    ),
                    diagnostics=list(camera_data.get("diagnostics", [])),
                    calibrated_at_iso=camera_data.get("calibrated_at_iso"),
                )
                for source_id, camera_data in payload.get("cameras", {}).items()
            }
            return CalibrationBundle(
                cameras=cameras,
                notes=list(payload.get("notes", [])),
                metadata=dict(payload.get("metadata", {})),
            )

        # Legacy MVP format compatibility.
        cameras = {
            source_id: CameraCalibration(**camera_data)
            for source_id, camera_data in payload.get("cameras", {}).items()
        }
        return CalibrationBundle(
            cameras=cameras,
            notes=list(payload.get("notes", [])),
            metadata=dict(payload.get("metadata", {})),
        )


class CalibrationManager:
    """Calibration solve logic independent from Qt UI widgets."""

    def __init__(
        self,
        board_shape: tuple[int, int] = (9, 6),
        square_size_m: float = 0.024,
        min_samples_per_camera: int = 8,
        min_quality_score: float = 0.25,
        min_coverage_ratio: float = 0.018,
        # Extrinsics/sync capture only needs the board shared between cameras, so
        # its acceptance thresholds are deliberately more lenient than the
        # intrinsics ones; they are applied automatically in sync_extrinsics mode.
        sync_min_quality_score: float = 0.15,
        sync_min_coverage_ratio: float = 0.012,
        default_pattern: Literal["chessboard", "charuco"] = "charuco",
        charuco_squares_x: int = 5,
        charuco_squares_y: int = 3,
        charuco_square_size_m: float = 0.077,
        charuco_marker_size_m: float = 0.061,
        min_charuco_corners: int = 8,
        min_sample_novelty_px: float = 14.0,
        spatial_grid_shape: tuple[int, int] = DEFAULT_SPATIAL_GRID_SHAPE,
        min_spatial_grid_coverage_ratio: float = DEFAULT_MIN_SPATIAL_GRID_COVERAGE_RATIO,
    ) -> None:
        self._board_shape = board_shape
        self._square_size_m = square_size_m
        self._min_samples_per_camera = min_samples_per_camera
        self._min_quality_score = min_quality_score
        self._min_coverage_ratio = min_coverage_ratio
        self._sync_min_quality_score = min(sync_min_quality_score, min_quality_score)
        self._sync_min_coverage_ratio = min(sync_min_coverage_ratio, min_coverage_ratio)
        self._default_pattern: Literal["chessboard", "charuco"] = default_pattern
        self._charuco_squares_x = max(2, charuco_squares_x)
        self._charuco_squares_y = max(2, charuco_squares_y)
        self._charuco_square_size_m = charuco_square_size_m
        self._charuco_marker_size_m = charuco_marker_size_m
        self._min_charuco_corners = max(4, min_charuco_corners)
        self._min_sample_novelty_px = max(2.0, min_sample_novelty_px)
        self._spatial_grid_shape = (
            max(1, int(spatial_grid_shape[0])),
            max(1, int(spatial_grid_shape[1])),
        )
        self._min_spatial_grid_coverage_ratio = float(
            np.clip(min_spatial_grid_coverage_ratio, 0.0, 1.0)
        )

        self._object_template = self._build_object_points()
        self._charuco_available = hasattr(cv2, "aruco")
        self._charuco_dict: Any | None = None
        self._charuco_board: Any | None = None
        # OpenCV >= 4.7 replaced the free aruco.detectMarkers /
        # interpolateCornersCharuco functions with detector objects. We build
        # them when available and fall back to the legacy free functions on older
        # builds, so the app works regardless of the installed OpenCV version.
        self._aruco_detector: Any | None = None
        self._charuco_detector: Any | None = None
        self._init_charuco()
        self._samples: dict[str, list[CalibrationSample]] = {}
        self._capture_sets: list[CalibrationCaptureSet] = []
        self._last_solution: CalibrationBundle | None = None
        # Cache of precomputed undistort rectify maps per source. Keyed on the
        # intrinsics/distortion/image-size signature so the maps are rebuilt
        # automatically whenever the calibration or frame size changes. Guarded
        # by a lock because undistort_frame is called from the UI thread and the
        # preview-render worker thread.
        self._undistort_map_cache: dict[str, tuple[Any, Any, Any]] = {}
        self._undistort_lock = threading.Lock()

    @property
    def board_shape(self) -> tuple[int, int]:
        return self._board_shape

    @property
    def square_size_m(self) -> float:
        return self._square_size_m

    def board_settings(self) -> CalibrationBoardSettings:
        return CalibrationBoardSettings(
            chessboard_cols=self._board_shape[0],
            chessboard_rows=self._board_shape[1],
            chessboard_square_size_m=self._square_size_m,
            charuco_squares_x=self._charuco_squares_x,
            charuco_squares_y=self._charuco_squares_y,
            charuco_square_size_m=self._charuco_square_size_m,
            charuco_marker_size_m=self._charuco_marker_size_m,
        )

    def apply_board_settings(self, settings: CalibrationBoardSettings) -> bool:
        current = self.board_settings()
        if current == settings:
            return False

        self._board_shape = (
            max(2, int(settings.chessboard_cols)),
            max(2, int(settings.chessboard_rows)),
        )
        self._square_size_m = max(0.0001, float(settings.chessboard_square_size_m))
        self._charuco_squares_x = max(2, int(settings.charuco_squares_x))
        self._charuco_squares_y = max(2, int(settings.charuco_squares_y))
        self._charuco_square_size_m = max(0.0001, float(settings.charuco_square_size_m))
        self._charuco_marker_size_m = max(0.0001, float(settings.charuco_marker_size_m))
        if self._charuco_marker_size_m >= self._charuco_square_size_m:
            self._charuco_marker_size_m = self._charuco_square_size_m * 0.75

        self._object_template = self._build_object_points()
        self._charuco_dict = None
        self._charuco_board = None
        self._aruco_detector = None
        self._charuco_detector = None
        self._init_charuco()
        self.reset_all()
        return True

    @property
    def min_samples_per_camera(self) -> int:
        return self._min_samples_per_camera

    @property
    def min_quality_score(self) -> float:
        return self._min_quality_score

    @property
    def min_coverage_ratio(self) -> float:
        return self._min_coverage_ratio

    @property
    def spatial_grid_shape(self) -> tuple[int, int]:
        return self._spatial_grid_shape

    @property
    def min_spatial_grid_coverage_ratio(self) -> float:
        return self._min_spatial_grid_coverage_ratio

    @property
    def sync_min_quality_score(self) -> float:
        return self._sync_min_quality_score

    @property
    def sync_min_coverage_ratio(self) -> float:
        return self._sync_min_coverage_ratio

    @property
    def default_pattern(self) -> Literal["chessboard", "charuco"]:
        return self._default_pattern

    def available_patterns(self) -> list[str]:
        patterns = ["chessboard"]
        if self._charuco_available and self._charuco_board is not None:
            patterns.append("charuco")
        return patterns

    def set_sync_acceptance_thresholds(
        self,
        min_quality_score: float,
        min_coverage_ratio: float,
    ) -> None:
        self._sync_min_quality_score = float(np.clip(min_quality_score, 0.0, 1.0))
        self._sync_min_coverage_ratio = float(np.clip(min_coverage_ratio, 0.0, 1.0))

    def set_intrinsics_acceptance_thresholds(
        self,
        min_quality_score: float,
        min_coverage_ratio: float,
    ) -> None:
        self._min_quality_score = float(np.clip(min_quality_score, 0.0, 1.0))
        self._min_coverage_ratio = float(np.clip(min_coverage_ratio, 0.0, 1.0))

    def set_spatial_coverage_grid(
        self,
        cols: int,
        rows: int,
        min_grid_coverage_ratio: float | None = None,
    ) -> None:
        self._spatial_grid_shape = (max(1, int(cols)), max(1, int(rows)))
        if min_grid_coverage_ratio is not None:
            self._min_spatial_grid_coverage_ratio = float(
                np.clip(min_grid_coverage_ratio, 0.0, 1.0)
            )

    def reset(self) -> None:
        """Remove all captured calibration samples."""
        self._samples.clear()
        self._capture_sets.clear()

    def reset_all(self) -> None:
        """Remove captured samples and forget the last solved calibration."""
        self.reset()
        self._last_solution = None

    def sources(self) -> list[str]:
        return sorted(self._samples.keys())

    def observation_count(self, source_id: str, include_sync_only: bool = True) -> int:
        samples = self._samples.get(source_id, [])
        if include_sync_only:
            return len(samples)
        return sum(1 for sample in samples if sample.accepted_for_intrinsics)

    def observations_summary(self, include_sync_only: bool = True) -> dict[str, int]:
        return {
            source_id: self.observation_count(source_id, include_sync_only=include_sync_only)
            for source_id in self._samples
        }

    def observations_breakdown_summary(self) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for source_id, samples in self._samples.items():
            total = len(samples)
            intrinsics = sum(1 for sample in samples if sample.accepted_for_intrinsics)
            synchronized = sum(1 for sample in samples if sample.capture_group_id is not None)
            sync_only = sum(
                1
                for sample in samples
                if sample.capture_group_id is not None and not sample.accepted_for_intrinsics
            )
            summary[source_id] = {
                "total": total,
                "intrinsics": intrinsics,
                "synchronized": synchronized,
                "sync_only": sync_only,
            }
        return summary

    def last_solution(self) -> CalibrationBundle | None:
        return self._last_solution

    def synchronized_capture_count(self) -> int:
        return len(self._capture_sets)

    def _init_charuco(self) -> None:
        if not self._charuco_available:
            return
        try:
            aruco = cv2.aruco  # type: ignore[attr-defined]
            dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
            board = None
            if hasattr(aruco, "CharucoBoard"):
                try:
                    board = aruco.CharucoBoard(
                        (self._charuco_squares_x, self._charuco_squares_y),
                        self._charuco_square_size_m,
                        self._charuco_marker_size_m,
                        dictionary,
                    )
                except Exception:
                    board = None
            if board is None and hasattr(aruco, "CharucoBoard_create"):
                board = aruco.CharucoBoard_create(
                    self._charuco_squares_x,
                    self._charuco_squares_y,
                    self._charuco_square_size_m,
                    self._charuco_marker_size_m,
                    dictionary,
                )
            self._charuco_dict = dictionary
            self._charuco_board = board
            if self._charuco_board is None:
                self._charuco_available = False
                return
            # Prefer the OpenCV >= 4.7 detector objects. Older builds keep the
            # free functions and these classes simply do not exist, in which case
            # detect_charuco falls back to the legacy API.
            self._aruco_detector = None
            self._charuco_detector = None
            if hasattr(aruco, "ArucoDetector") and hasattr(aruco, "DetectorParameters"):
                try:
                    self._aruco_detector = aruco.ArucoDetector(dictionary, aruco.DetectorParameters())
                except Exception:
                    self._aruco_detector = None
            if hasattr(aruco, "CharucoDetector"):
                try:
                    self._charuco_detector = aruco.CharucoDetector(board)
                except Exception:
                    self._charuco_detector = None
        except Exception:
            self._charuco_available = False
            self._charuco_dict = None
            self._charuco_board = None
            self._aruco_detector = None
            self._charuco_detector = None

    def _metadata_units(self) -> dict[str, str]:
        return {
            "all_length_fields": "meters",
            "calibration_board.square_size_m": "meters",
            "calibration_board.marker_size_m": "meters",
            "sample_collection.duration_sec": "seconds",
            "sample_collection.first_sample_at_iso": "ISO-8601 local timestamp",
            "sample_collection.last_sample_at_iso": "ISO-8601 local timestamp",
            "cameras.*.intrinsics": "pixels",
            "cameras.*.distortion": "unitless OpenCV distortion coefficients",
            "cameras.*.rotation": "unitless 3x3 row-major world-to-camera rotation matrix",
            "cameras.*.translation": "meters, world-to-camera translation",
            "cameras.*.reprojection_error": "pixels",
            "metadata.extrinsics.*.baseline_m": "meters",
            "metadata.extrinsics.*.stereo_rms": "pixels",
            "metadata.spatial_coverage.*.grid_coverage_ratio": "0..1 ratio",
            "metadata.spatial_coverage.*.center_spread_x_px": "pixels",
            "metadata.spatial_coverage.*.center_spread_y_px": "pixels",
            "metadata.spatial_coverage.*.edge_coverage_score": "0..1 ratio",
            "metadata.spatial_coverage.*.cell_hit_counts": "sample-hit counts per grid cell",
            "metadata.sample_collection.synchronized_timing.max_timestamp_skew_ms": "milliseconds",
            "metadata.sample_collection.synchronized_timing.mean_timestamp_skew_ms": "milliseconds",
            "metadata.sample_collection.synchronized_timing.max_allowed_timestamp_skew_ms": "milliseconds",
            "image_size": "pixels [width, height]",
        }

    def _metadata_validation_guidance(self) -> list[str]:
        return [
            "Measure the printed calibration board with a ruler or caliper. ChArUco square_size_m is one full square edge in meters; marker_size_m is the black marker edge in meters.",
            "Measure camera lens-center to lens-center distance and compare it with metadata.extrinsics.<camera>.baseline_m. A large scale mismatch usually means the board square/marker size was entered incorrectly.",
            "Check cameras.*.reprojection_error and metadata.extrinsics.*.stereo_rms in pixels. Values below about 1 px are usually a good sign; higher values suggest blur, weak coverage, wrong board settings, or mismatched samples.",
            "Move the board through the full image area and multiple angles. Low coverage warnings mean the calibration may fit the center but extrapolate poorly near the edges.",
            "Check metadata.spatial_coverage.per_camera.*.grid_coverage_ratio and edge_coverage_score. Low values mean the board stayed in one image region even if reprojection error is low.",
            "Confirm the active_pattern and calibration_board block match the physical board before trusting translation or baseline values in meters.",
        ]

    def _attach_metadata_help(self, metadata: dict[str, Any]) -> dict[str, Any]:
        metadata["units"] = self._metadata_units()
        metadata["validation_guidance"] = self._metadata_validation_guidance()
        return metadata

    def sample_collection_metadata(self) -> dict[str, Any]:
        samples_by_source = self._samples
        all_samples = [
            sample
            for samples in samples_by_source.values()
            for sample in samples
        ]
        if not all_samples:
            return {
                "first_sample_at_iso": None,
                "last_sample_at_iso": None,
                "duration_sec": 0.0,
                "total_samples": 0,
                "total_intrinsics_samples": 0,
                "synchronized_sets": len(self._capture_sets),
                "synchronized_timing": self.synchronized_timing_metadata(),
                "per_camera": {},
            }

        sample_times = [datetime.fromisoformat(sample.captured_at_iso) for sample in all_samples]
        first_sample_at = min(sample_times)
        last_sample_at = max(sample_times)
        duration_sec = max(0.0, (last_sample_at - first_sample_at).total_seconds())
        per_camera: dict[str, dict[str, Any]] = {}
        for source_id, samples in samples_by_source.items():
            times = [datetime.fromisoformat(sample.captured_at_iso) for sample in samples]
            first_at = min(times) if times else None
            last_at = max(times) if times else None
            per_camera[source_id] = {
                "total_samples": len(samples),
                "intrinsics_samples": sum(1 for sample in samples if sample.accepted_for_intrinsics),
                "sync_only_samples": sum(1 for sample in samples if not sample.accepted_for_intrinsics),
                "first_sample_at_iso": first_at.isoformat() if first_at is not None else None,
                "last_sample_at_iso": last_at.isoformat() if last_at is not None else None,
                "duration_sec": (
                    max(0.0, (last_at - first_at).total_seconds())
                    if first_at is not None and last_at is not None
                    else 0.0
                ),
            }

        return {
            "first_sample_at_iso": first_sample_at.isoformat(),
            "last_sample_at_iso": last_sample_at.isoformat(),
            "duration_sec": duration_sec,
            "total_samples": len(all_samples),
            "total_intrinsics_samples": sum(1 for sample in all_samples if sample.accepted_for_intrinsics),
            "synchronized_sets": len(self._capture_sets),
            "synchronized_timing": self.synchronized_timing_metadata(),
            "per_camera": per_camera,
        }

    def synchronized_timing_metadata(self) -> dict[str, Any]:
        timing_sets = [
            capture_set.sync_metadata
            for capture_set in self._capture_sets
            if capture_set.sync_metadata
        ]
        skews = [
            float(metadata["timestamp_skew_ms"])
            for metadata in timing_sets
            if isinstance(metadata.get("timestamp_skew_ms"), (int, float))
        ]
        max_allowed_values = [
            float(metadata["max_allowed_timestamp_skew_ms"])
            for metadata in timing_sets
            if isinstance(metadata.get("max_allowed_timestamp_skew_ms"), (int, float))
        ]
        return {
            "software_sync": True,
            "timing_available_sets": len(timing_sets),
            "max_timestamp_skew_ms": max(skews) if skews else None,
            "mean_timestamp_skew_ms": float(np.mean(skews)) if skews else None,
            "max_allowed_timestamp_skew_ms": max(max_allowed_values) if max_allowed_values else None,
        }

    def detect_pattern(
        self,
        source_id: str,
        frame_bgr: U8Array,
        pattern: Literal["chessboard", "charuco"] | str,
    ) -> ChessboardDetectionResult:
        normalized = str(pattern).lower().strip()
        if normalized == "charuco":
            return self.detect_charuco(source_id=source_id, frame_bgr=frame_bgr)
        return self.detect_chessboard(source_id=source_id, frame_bgr=frame_bgr)

    def detect_chessboard(self, source_id: str, frame_bgr: U8Array) -> ChessboardDetectionResult:
        """Run checkerboard detection and quality analysis for preview/capture gating."""
        height, width = frame_bgr.shape[:2]
        image_size = (width, height)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        corners: FloatArray | None = None
        found = False

        if hasattr(cv2, "findChessboardCornersSB"):
            flags_sb = cv2.CALIB_CB_EXHAUSTIVE + cv2.CALIB_CB_ACCURACY
            found, corners_sb = cv2.findChessboardCornersSB(gray, self._board_shape, flags=flags_sb)
            if found and corners_sb is not None:
                corners = corners_sb.astype(np.float32)

        if not found or corners is None:
            flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
            found, corners_raw = cv2.findChessboardCorners(gray, self._board_shape, flags=flags)
            if found and corners_raw is not None:
                refined = cv2.cornerSubPix(
                    gray,
                    corners_raw,
                    (11, 11),
                    (-1, -1),
                    criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 35, 0.0008),
                )
                corners = refined.astype(np.float32)

        if corners is None or not found:
            return ChessboardDetectionResult(
                source_id=source_id,
                pattern_type="chessboard",
                found=False,
                image_size=image_size,
                diagnostics=["Chessboard not detected."],
            )

        quality_score, coverage_ratio, sharpness_score = self._compute_quality_metrics(
            gray=gray,
            corners=corners,
            expected_corner_count=self._board_shape[0] * self._board_shape[1],
        )
        board_bbox_px, board_center_px = self._board_geometry_from_corners(corners)
        diagnostics: list[str] = []
        if coverage_ratio < self._min_coverage_ratio:
            diagnostics.append(
                "Coverage below intrinsics target "
                f"({coverage_ratio * 100:.1f}% < {self._min_coverage_ratio * 100:.1f}%)."
            )
        if quality_score < self._min_quality_score:
            diagnostics.append(
                "Quality below intrinsics target "
                f"({quality_score:.2f} < {self._min_quality_score:.2f})."
            )

        return ChessboardDetectionResult(
            source_id=source_id,
            pattern_type="chessboard",
            found=True,
            image_size=image_size,
            corners=corners,
            detected_corners=int(corners.shape[0]),
            quality_score=quality_score,
            coverage_ratio=coverage_ratio,
            sharpness_score=sharpness_score,
            board_bbox_px=board_bbox_px,
            board_center_px=board_center_px,
            diagnostics=diagnostics,
        )

    def _detect_charuco_corners(self, gray: U8Array) -> tuple[Any, Any, Any]:
        """Detect ArUco markers and interpolate ChArUco corners.

        Uses the OpenCV >= 4.7 detector objects when available and falls back to
        the legacy free functions on older builds. Returns
        ``(marker_ids, charuco_corners, charuco_ids)``; any element may be ``None``
        when nothing was detected.
        """
        aruco = cv2.aruco  # type: ignore[attr-defined]
        if self._charuco_detector is not None:
            # CharucoDetector.detectBoard runs marker detection and corner
            # interpolation in a single call (OpenCV >= 4.7).
            charuco_corners, charuco_ids, _marker_corners, marker_ids = (
                self._charuco_detector.detectBoard(gray)
            )
            return marker_ids, charuco_corners, charuco_ids

        if self._aruco_detector is not None:
            marker_corners, marker_ids, _ = self._aruco_detector.detectMarkers(gray)
        else:
            marker_corners, marker_ids, _ = aruco.detectMarkers(gray, self._charuco_dict)
        if marker_ids is None or len(marker_ids) < 4:
            return marker_ids, None, None
        _retval, charuco_corners, charuco_ids = aruco.interpolateCornersCharuco(
            marker_corners,
            marker_ids,
            gray,
            self._charuco_board,
        )
        return marker_ids, charuco_corners, charuco_ids

    def detect_charuco(self, source_id: str, frame_bgr: U8Array) -> ChessboardDetectionResult:
        height, width = frame_bgr.shape[:2]
        image_size = (width, height)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        if not self._charuco_available or self._charuco_dict is None or self._charuco_board is None:
            return ChessboardDetectionResult(
                source_id=source_id,
                pattern_type="charuco",
                found=False,
                image_size=image_size,
                diagnostics=["Charuco unavailable: install OpenCV build with cv2.aruco support."],
            )

        try:
            marker_ids, charuco_corners, charuco_ids = self._detect_charuco_corners(gray)
        except cv2.error as exc:
            return ChessboardDetectionResult(
                source_id=source_id,
                pattern_type="charuco",
                found=False,
                image_size=image_size,
                diagnostics=[f"Charuco detection failed: {exc}"],
            )

        if marker_ids is None or len(marker_ids) < 4:
            return ChessboardDetectionResult(
                source_id=source_id,
                pattern_type="charuco",
                found=False,
                image_size=image_size,
                diagnostics=["Charuco markers not reliably detected."],
            )

        if charuco_corners is None or charuco_ids is None:
            return ChessboardDetectionResult(
                source_id=source_id,
                pattern_type="charuco",
                found=False,
                image_size=image_size,
                diagnostics=["Charuco interpolation failed."],
            )

        corner_count = int(charuco_corners.shape[0])
        diagnostics: list[str] = []
        if corner_count < self._min_charuco_corners:
            diagnostics.append(
                f"Charuco corners below intrinsics target ({corner_count}/{self._min_charuco_corners})."
            )

        expected = max(1, (self._charuco_squares_x - 1) * (self._charuco_squares_y - 1))
        quality_score, coverage_ratio, sharpness_score = self._compute_quality_metrics(
            gray=gray,
            corners=charuco_corners.astype(np.float32),
            expected_corner_count=expected,
        )
        board_bbox_px, board_center_px = self._board_geometry_from_corners(
            charuco_corners.astype(np.float32)
        )
        if coverage_ratio < self._min_coverage_ratio:
            diagnostics.append(
                "Coverage below intrinsics target "
                f"({coverage_ratio * 100:.1f}% < {self._min_coverage_ratio * 100:.1f}%)."
            )
        if quality_score < self._min_quality_score:
            diagnostics.append(
                "Quality below intrinsics target "
                f"({quality_score:.2f} < {self._min_quality_score:.2f})."
            )

        return ChessboardDetectionResult(
            source_id=source_id,
            pattern_type="charuco",
            found=True,
            image_size=image_size,
            corners=charuco_corners.astype(np.float32),
            charuco_ids=charuco_ids.astype(np.int32),
            detected_corners=corner_count,
            quality_score=quality_score,
            coverage_ratio=coverage_ratio,
            sharpness_score=sharpness_score,
            board_bbox_px=board_bbox_px,
            board_center_px=board_center_px,
            diagnostics=diagnostics,
        )

    def _compute_quality_metrics(
        self,
        gray: U8Array,
        corners: FloatArray,
        expected_corner_count: int,
    ) -> tuple[float, float, float]:
        points = corners.reshape(-1, 2)
        width = gray.shape[1]
        height = gray.shape[0]
        hull = cv2.convexHull(points)
        board_area = float(cv2.contourArea(hull))
        image_area = float(width * height) if width and height else 1.0
        coverage_ratio = board_area / image_area

        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        sharpness_score = float(np.clip(lap_var / 220.0, 0.0, 1.0))
        coverage_score = float(np.clip(coverage_ratio / 0.18, 0.0, 1.0))
        corner_ratio = float(np.clip(points.shape[0] / max(expected_corner_count, 1), 0.0, 1.0))
        quality_score = 0.52 * coverage_score + 0.28 * sharpness_score + 0.20 * corner_ratio
        return quality_score, coverage_ratio, sharpness_score

    def _board_geometry_from_corners(
        self,
        corners: FloatArray,
    ) -> tuple[tuple[float, float, float, float] | None, tuple[float, float] | None]:
        points = np.array(corners, dtype=np.float32).reshape(-1, 2)
        if points.size == 0:
            return None, None
        min_xy = np.min(points, axis=0)
        max_xy = np.max(points, axis=0)
        width = float(max_xy[0] - min_xy[0])
        height = float(max_xy[1] - min_xy[1])
        bbox = (float(min_xy[0]), float(min_xy[1]), width, height)
        center = (float(min_xy[0] + width * 0.5), float(min_xy[1] + height * 0.5))
        return bbox, center

    def try_add_observation(
        self,
        source_id: str,
        frame_bgr: U8Array,
        pattern: Literal["chessboard", "charuco"] | str | None = None,
    ) -> CalibrationCaptureFeedback:
        """Attempt to store a sample only when detection quality and consistency are valid."""
        selected_pattern = self._normalize_pattern_name(pattern)
        detection = self.detect_pattern(
            source_id=source_id,
            frame_bgr=frame_bgr,
            pattern=selected_pattern,
        )
        current_count = self.observation_count(source_id, include_sync_only=True)
        sample, message, rejection_reasons = self._build_sample_from_detection(
            source_id=source_id,
            detection=detection,
            selected_pattern=selected_pattern,
            acceptance_mode="intrinsics",
        )
        if sample is None:
            self._append_unique_diagnostics(detection, rejection_reasons)
            return CalibrationCaptureFeedback(
                source_id=source_id,
                accepted=False,
                sample_count=current_count,
                message=message,
                detection=detection,
            )

        sample_count = self._append_sample(source_id=source_id, sample=sample)
        return CalibrationCaptureFeedback(
            source_id=source_id,
            accepted=True,
            sample_count=sample_count,
            message=message,
            detection=detection,
        )

    def try_add_detection_set(
        self,
        detections_by_source: dict[str, ChessboardDetectionResult],
        pattern: Literal["chessboard", "charuco"] | str | None = None,
        allow_relaxed_sync: bool = True,
        workflow_mode: Literal["hybrid", "intrinsics", "sync_extrinsics"] = "hybrid",
        sync_metadata: dict[str, Any] | None = None,
    ) -> dict[str, CalibrationCaptureFeedback]:
        """Store single-camera samples and synchronized capture sets from precomputed detections."""
        selected_pattern = self._normalize_pattern_name(pattern)
        mode = str(workflow_mode).lower().strip()
        if mode not in {"hybrid", "intrinsics", "sync_extrinsics"}:
            mode = "hybrid"
        strict_candidates: dict[str, CalibrationSample] = {}
        relaxed_candidates: dict[str, CalibrationSample] = {}
        sync_candidates: dict[str, CalibrationSample] = {}
        strict_messages: dict[str, str] = {}
        relaxed_messages: dict[str, str] = {}
        sync_messages: dict[str, str] = {}
        rejected_messages: dict[str, tuple[str, list[str]]] = {}

        for source_id, detection in detections_by_source.items():
            strict_sample, strict_message, strict_reasons = self._build_sample_from_detection(
                source_id=source_id,
                detection=detection,
                selected_pattern=selected_pattern,
                acceptance_mode="intrinsics",
            )

            if mode == "intrinsics":
                if strict_sample is not None:
                    strict_candidates[source_id] = strict_sample
                    strict_messages[source_id] = strict_message
                else:
                    rejected_messages[source_id] = (strict_message, strict_reasons)
                continue

            if mode == "sync_extrinsics":
                sync_sample: CalibrationSample | None = None
                sync_message = strict_message
                sync_reasons = strict_reasons

                if strict_sample is not None:
                    strict_sample.accepted_for_intrinsics = False
                    sync_sample = strict_sample
                    sync_message = (
                        f"{source_id}: accepted sync candidate "
                        f"(pattern={selected_pattern}, quality={sync_sample.quality_score:.2f}, "
                        f"coverage={sync_sample.coverage_ratio * 100:.1f}%)."
                    )
                    sync_reasons = []
                elif allow_relaxed_sync:
                    relaxed_sample, relaxed_message, relaxed_reasons = self._build_sample_from_detection(
                        source_id=source_id,
                        detection=detection,
                        selected_pattern=selected_pattern,
                        acceptance_mode="synchronized_relaxed",
                    )
                    if relaxed_sample is not None:
                        relaxed_sample.accepted_for_intrinsics = False
                        sync_sample = relaxed_sample
                        sync_message = (
                            f"{source_id}: accepted sync candidate "
                            f"(pattern={selected_pattern}, quality={sync_sample.quality_score:.2f}, "
                            f"coverage={sync_sample.coverage_ratio * 100:.1f}%) | relaxed thresholds."
                        )
                        sync_reasons = []
                    else:
                        sync_message = relaxed_message
                        sync_reasons = relaxed_reasons

                if sync_sample is not None:
                    sync_candidates[source_id] = sync_sample
                    sync_messages[source_id] = sync_message
                else:
                    rejected_messages[source_id] = (sync_message, sync_reasons)
                continue

            if strict_sample is not None:
                strict_candidates[source_id] = strict_sample
                strict_messages[source_id] = strict_message
                continue

            if allow_relaxed_sync:
                relaxed_sample, relaxed_message, relaxed_reasons = self._build_sample_from_detection(
                    source_id=source_id,
                    detection=detection,
                    selected_pattern=selected_pattern,
                    acceptance_mode="synchronized_relaxed",
                )
                if relaxed_sample is not None:
                    relaxed_candidates[source_id] = relaxed_sample
                    relaxed_messages[source_id] = relaxed_message
                    continue
                rejected_messages[source_id] = (relaxed_message, relaxed_reasons)
                continue

            rejected_messages[source_id] = (strict_message, strict_reasons)

        accepted_samples: dict[str, CalibrationSample] = dict(strict_candidates)
        accepted_messages: dict[str, str] = dict(strict_messages)
        capture_group_id: str | None = None
        captured_at_iso = datetime.now().isoformat()

        if mode == "intrinsics":
            accepted_samples = dict(strict_candidates)
            accepted_messages = dict(strict_messages)
        elif mode == "sync_extrinsics":
            accepted_samples = {}
            accepted_messages = {}
            if len(sync_candidates) >= 2:
                accepted_samples = dict(sync_candidates)
                accepted_messages = dict(sync_messages)
        elif len(strict_candidates) + len(relaxed_candidates) >= 2:
            accepted_samples.update(relaxed_candidates)
            accepted_messages.update(relaxed_messages)

        if mode == "sync_extrinsics":
            if len(accepted_samples) >= 2:
                capture_group_id = f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        elif mode == "hybrid":
            if len(accepted_samples) >= 2:
                capture_group_id = f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        feedbacks: dict[str, CalibrationCaptureFeedback] = {}
        for source_id, detection in detections_by_source.items():
            current_count = self.observation_count(source_id, include_sync_only=True)
            sample = accepted_samples.get(source_id)
            if sample is None:
                if mode == "sync_extrinsics" and source_id in sync_candidates:
                    wait_reason = "Waiting for at least one more camera with a synchronized valid view."
                    self._append_unique_diagnostics(detection, [wait_reason])
                    feedbacks[source_id] = CalibrationCaptureFeedback(
                        source_id=source_id,
                        accepted=False,
                        sample_count=current_count,
                        message=f"{source_id}: sync candidate detected but not stored yet ({wait_reason.lower()})",
                        detection=detection,
                    )
                    continue

                if mode == "hybrid" and source_id in relaxed_candidates:
                    wait_reason = "Waiting for at least one more camera with a synchronized valid view."
                    self._append_unique_diagnostics(detection, [wait_reason])
                    feedbacks[source_id] = CalibrationCaptureFeedback(
                        source_id=source_id,
                        accepted=False,
                        sample_count=current_count,
                        message=f"{source_id}: detected but not stored yet ({wait_reason.lower()})",
                        detection=detection,
                    )
                    continue

                rejected_message, rejected_reasons = rejected_messages.get(
                    source_id,
                    (
                        f"{source_id}: sample rejected.",
                        ["Sample rejected."],
                    ),
                )
                self._append_unique_diagnostics(detection, rejected_reasons)
                feedbacks[source_id] = CalibrationCaptureFeedback(
                    source_id=source_id,
                    accepted=False,
                    sample_count=current_count,
                    message=rejected_message,
                    detection=detection,
                )
                continue

            sample.capture_group_id = capture_group_id
            sample_count = self._append_sample(source_id=source_id, sample=sample)
            sync_suffix = ""
            if capture_group_id is not None:
                sync_suffix = f" | synchronized set with {len(accepted_samples)} camera(s)"
            if not sample.accepted_for_intrinsics:
                self._append_unique_diagnostics(
                    detection,
                    ["Accepted for synchronized multi-camera capture with relaxed thresholds."],
                )
            feedbacks[source_id] = CalibrationCaptureFeedback(
                source_id=source_id,
                accepted=True,
                sample_count=sample_count,
                message=accepted_messages[source_id] + sync_suffix,
                detection=detection,
            )

        if capture_group_id is not None:
            self._capture_sets.append(
                CalibrationCaptureSet(
                    capture_group_id=capture_group_id,
                    pattern_type=selected_pattern,
                    samples_by_source=dict(accepted_samples),
                    captured_at_iso=captured_at_iso,
                    sync_metadata=dict(sync_metadata or {}),
                )
            )

        return feedbacks

    def try_add_observation_set(
        self,
        frames_by_source: dict[str, U8Array],
        pattern: Literal["chessboard", "charuco"] | str | None = None,
        allow_relaxed_sync: bool = True,
        workflow_mode: Literal["hybrid", "intrinsics", "sync_extrinsics"] = "hybrid",
        sync_metadata: dict[str, Any] | None = None,
    ) -> dict[str, CalibrationCaptureFeedback]:
        """Capture a synchronized multi-camera observation set when multiple views are valid."""
        selected_pattern = self._normalize_pattern_name(pattern)
        detections = {
            source_id: self.detect_pattern(
                source_id=source_id,
                frame_bgr=frame_bgr,
                pattern=selected_pattern,
            )
            for source_id, frame_bgr in frames_by_source.items()
        }
        return self.try_add_detection_set(
            detections_by_source=detections,
            pattern=selected_pattern,
            allow_relaxed_sync=allow_relaxed_sync,
            workflow_mode=workflow_mode,
            sync_metadata=sync_metadata,
        )

    def add_chessboard_observation(self, source_id: str, frame_bgr: U8Array) -> bool:
        """Backward-compatible helper used by older UI hooks."""
        feedback = self.try_add_observation(source_id, frame_bgr)
        return feedback.accepted

    def sample_statistics(self, source_id: str) -> dict[str, float]:
        """Return quality and coverage stats for a camera's accepted samples."""
        samples = self._samples.get(source_id, [])
        if not samples:
            return {"count": 0.0, "mean_quality": 0.0, "mean_coverage": 0.0}
        qualities = np.array([sample.quality_score for sample in samples], dtype=np.float32)
        coverages = np.array([sample.coverage_ratio for sample in samples], dtype=np.float32)
        return {
            "count": float(len(samples)),
            "mean_quality": float(np.mean(qualities)),
            "mean_coverage": float(np.mean(coverages)),
        }

    def spatial_coverage_metadata(self) -> dict[str, Any]:
        per_camera = {
            source_id: self.spatial_coverage_summary(source_id, include_sync_only=False)
            for source_id in self.sources()
        }
        ratios = [
            float(summary.get("grid_coverage_ratio", 0.0))
            for summary in per_camera.values()
            if int(summary.get("sample_count", 0)) > 0
        ]
        return {
            "grid": {
                "cols": self._spatial_grid_shape[0],
                "rows": self._spatial_grid_shape[1],
                "total_cells": self._spatial_grid_shape[0] * self._spatial_grid_shape[1],
            },
            "min_grid_coverage_ratio": self._min_spatial_grid_coverage_ratio,
            "sample_filter": "intrinsics",
            "overall_grid_coverage_ratio": float(np.mean(ratios)) if ratios else 0.0,
            "per_camera": per_camera,
        }

    def spatial_coverage_summary(
        self,
        source_id: str,
        include_sync_only: bool = False,
        samples: list[CalibrationSample] | None = None,
        include_sample_summaries: bool = True,
        target_samples_per_cell: int | None = None,
    ) -> dict[str, Any]:
        selected_samples = list(samples) if samples is not None else list(self._samples.get(source_id, []))
        if not include_sync_only:
            selected_samples = [sample for sample in selected_samples if sample.accepted_for_intrinsics]

        cols, rows = self._spatial_grid_shape
        total_cells = cols * rows
        hit_counts = np.zeros((rows, cols), dtype=np.int32)
        credited_hit_counts = np.zeros((rows, cols), dtype=np.int32)
        center_hit_counts = np.zeros((rows, cols), dtype=np.int32)
        sample_summaries: list[dict[str, Any]] = []
        centers: list[tuple[float, float]] = []
        image_size = selected_samples[0].image_size if selected_samples else None

        for index, sample in enumerate(selected_samples, start=1):
            cells, center = self._spatial_cells_for_sample(sample)
            credited_cell = self._credited_spatial_cell_for_sample(
                cells=cells,
                center=center,
                image_size=sample.image_size,
                hit_counts=credited_hit_counts,
                target_samples_per_cell=target_samples_per_cell,
            )
            if credited_cell is not None:
                credited_row, credited_col = credited_cell
                credited_hit_counts[credited_row, credited_col] += 1
            if center is not None:
                centers.append(center)
                center_row, center_col = self._point_to_grid_cell(
                    float(center[0]),
                    float(center[1]),
                    sample.image_size,
                )
                center_hit_counts[center_row, center_col] += 1
            for row, col in cells:
                hit_counts[row, col] += 1
            if include_sample_summaries:
                sample_summaries.append(
                    {
                        "index": index,
                        "captured_at_iso": sample.captured_at_iso,
                        "accepted_for_intrinsics": sample.accepted_for_intrinsics,
                        "corner_count": len(self._sample_corner_points(sample)),
                        "board_bbox_px": self._rounded_float_sequence(sample.board_bbox_px),
                        "board_center_px": self._rounded_float_sequence(center),
                        "visited_cells": [[row, col] for row, col in sorted(cells)],
                        "credited_cell": list(credited_cell) if credited_cell is not None else None,
                    }
                )

        visited_cell_indices = [
            [row, col]
            for row in range(rows)
            for col in range(cols)
            if int(hit_counts[row, col]) > 0
        ]
        visited_cells = len(visited_cell_indices)
        credited_visited_cell_indices = [
            [row, col]
            for row in range(rows)
            for col in range(cols)
            if int(credited_hit_counts[row, col]) > 0
        ]
        credited_visited_cells = len(credited_visited_cell_indices)
        center_visited_cell_indices = [
            [row, col]
            for row in range(rows)
            for col in range(cols)
            if int(center_hit_counts[row, col]) > 0
        ]
        center_visited_cells = len(center_visited_cell_indices)
        center_spread_x_px = 0.0
        center_spread_y_px = 0.0
        if centers:
            xs = [center[0] for center in centers]
            ys = [center[1] for center in centers]
            center_spread_x_px = float(max(xs) - min(xs))
            center_spread_y_px = float(max(ys) - min(ys))

        edge_cells = self._edge_grid_cells(cols=cols, rows=rows)
        visited_edge_cells = sum(1 for row, col in edge_cells if int(hit_counts[row, col]) > 0)
        credited_edge_cells = sum(1 for row, col in edge_cells if int(credited_hit_counts[row, col]) > 0)
        edge_coverage_score = (
            float(visited_edge_cells / len(edge_cells))
            if edge_cells
            else 0.0
        )
        credited_edge_coverage_score = (
            float(credited_edge_cells / len(edge_cells))
            if edge_cells
            else 0.0
        )

        corner_cell_hits = {
            "top_left": int(hit_counts[0, 0]) if rows and cols else 0,
            "top_right": int(hit_counts[0, cols - 1]) if rows and cols else 0,
            "bottom_left": int(hit_counts[rows - 1, 0]) if rows and cols else 0,
            "bottom_right": int(hit_counts[rows - 1, cols - 1]) if rows and cols else 0,
        }
        credited_corner_cell_hits = {
            "top_left": int(credited_hit_counts[0, 0]) if rows and cols else 0,
            "top_right": int(credited_hit_counts[0, cols - 1]) if rows and cols else 0,
            "bottom_left": int(credited_hit_counts[rows - 1, 0]) if rows and cols else 0,
            "bottom_right": int(credited_hit_counts[rows - 1, cols - 1]) if rows and cols else 0,
        }

        return {
            "source_id": source_id,
            "grid_cols": cols,
            "grid_rows": rows,
            "sample_count": len(selected_samples),
            "image_size": list(image_size) if image_size is not None else None,
            "visited_cells": visited_cells,
            "total_cells": total_cells,
            "grid_coverage_ratio": float(visited_cells / total_cells) if total_cells else 0.0,
            "center_spread_x_px": center_spread_x_px,
            "center_spread_y_px": center_spread_y_px,
            "edge_coverage_score": edge_coverage_score,
            "corner_cell_hits": corner_cell_hits,
            "cell_hit_counts": hit_counts.astype(int).tolist(),
            "credited_visited_cells": credited_visited_cells,
            "credited_grid_coverage_ratio": float(credited_visited_cells / total_cells) if total_cells else 0.0,
            "credited_edge_coverage_score": credited_edge_coverage_score,
            "credited_corner_cell_hits": credited_corner_cell_hits,
            "credited_cell_hit_counts": credited_hit_counts.astype(int).tolist(),
            "credited_visited_cell_indices": credited_visited_cell_indices,
            "spatial_credit_mode": "one_sample_one_footprint_cell",
            "center_visited_cells": center_visited_cells,
            "center_grid_coverage_ratio": float(center_visited_cells / total_cells) if total_cells else 0.0,
            "center_cell_hit_counts": center_hit_counts.astype(int).tolist(),
            "center_visited_cell_indices": center_visited_cell_indices,
            "visited_cell_indices": visited_cell_indices,
            "samples": sample_summaries,
        }

    def _spatial_cells_for_sample(
        self,
        sample: CalibrationSample,
    ) -> tuple[set[tuple[int, int]], tuple[float, float] | None]:
        cells: set[tuple[int, int]] = set()
        points = self._sample_corner_points(sample)
        for point in points:
            cells.add(self._point_to_grid_cell(float(point[0]), float(point[1]), sample.image_size))

        if sample.board_bbox_px is not None:
            bbox_x, bbox_y, bbox_w, bbox_h = sample.board_bbox_px
            for point_x, point_y in (
                (bbox_x, bbox_y),
                (bbox_x + bbox_w, bbox_y),
                (bbox_x, bbox_y + bbox_h),
                (bbox_x + bbox_w, bbox_y + bbox_h),
            ):
                cells.add(self._point_to_grid_cell(float(point_x), float(point_y), sample.image_size))

        center = sample.board_center_px
        if center is None and points.size:
            _, center = self._board_geometry_from_corners(points.reshape(-1, 1, 2).astype(np.float32))
        if center is not None:
            cells.add(self._point_to_grid_cell(float(center[0]), float(center[1]), sample.image_size))
        return cells, center

    def _spatial_cells_for_detection(
        self,
        detection: ChessboardDetectionResult,
        image_size: tuple[int, int] | None = None,
    ) -> tuple[set[tuple[int, int]], tuple[float, float] | None]:
        cells: set[tuple[int, int]] = set()
        target_image_size = image_size or detection.image_size
        points = (
            detection.corners.reshape(-1, 2)
            if detection.corners is not None
            else np.empty((0, 2), dtype=np.float32)
        )
        for point in points:
            cells.add(self._point_to_grid_cell(float(point[0]), float(point[1]), target_image_size))

        bbox = detection.board_bbox_px
        if bbox is not None:
            bbox_x, bbox_y, bbox_w, bbox_h = bbox
            for point_x, point_y in (
                (bbox_x, bbox_y),
                (bbox_x + bbox_w, bbox_y),
                (bbox_x, bbox_y + bbox_h),
                (bbox_x + bbox_w, bbox_y + bbox_h),
            ):
                cells.add(self._point_to_grid_cell(float(point_x), float(point_y), target_image_size))

        center = detection.board_center_px
        if center is None and points.size:
            _, center = self._board_geometry_from_corners(points.reshape(-1, 1, 2).astype(np.float32))
        if center is not None:
            cells.add(self._point_to_grid_cell(float(center[0]), float(center[1]), target_image_size))
        return cells, center

    def _credited_spatial_cell_for_sample(
        self,
        cells: set[tuple[int, int]],
        center: tuple[float, float] | None,
        image_size: tuple[int, int],
        hit_counts: NDArray[np.int32],
        target_samples_per_cell: int | None = None,
    ) -> tuple[int, int] | None:
        if not cells:
            return None

        cols, rows = self._spatial_grid_shape
        candidates = sorted(cells)
        if target_samples_per_cell is not None:
            target = max(1, int(target_samples_per_cell))
            under_target = [
                (row, col)
                for row, col in candidates
                if int(hit_counts[row, col]) < target
            ]
            if under_target:
                candidates = under_target

        min_count = min(int(hit_counts[row, col]) for row, col in candidates)
        candidates = [
            (row, col)
            for row, col in candidates
            if int(hit_counts[row, col]) == min_count
        ]

        edge_cells = self._edge_grid_cells(cols=cols, rows=rows)
        edge_candidates = [cell for cell in candidates if cell in edge_cells]
        if edge_candidates:
            candidates = edge_candidates

        center_cell: tuple[int, int] | None = None
        if center is not None:
            center_cell = self._point_to_grid_cell(float(center[0]), float(center[1]), image_size)
        mid_row = (rows - 1) * 0.5
        mid_col = (cols - 1) * 0.5

        def sort_key(cell: tuple[int, int]) -> tuple[float, int, int, int]:
            row, col = cell
            distance_from_grid_center = (float(row) - mid_row) ** 2 + (float(col) - mid_col) ** 2
            center_distance = (
                abs(row - center_cell[0]) + abs(col - center_cell[1])
                if center_cell is not None
                else 0
            )
            return (-distance_from_grid_center, center_distance, row, col)

        return min(candidates, key=sort_key)

    def _sample_corner_points(self, sample: CalibrationSample) -> NDArray[np.float32]:
        if sample.corner_points_px:
            return np.array(sample.corner_points_px, dtype=np.float32).reshape(-1, 2)
        return np.array(sample.image_points, dtype=np.float32).reshape(-1, 2)

    def _point_to_grid_cell(
        self,
        x_px: float,
        y_px: float,
        image_size: tuple[int, int],
    ) -> tuple[int, int]:
        width, height = image_size
        cols, rows = self._spatial_grid_shape
        safe_width = max(float(width), 1.0)
        safe_height = max(float(height), 1.0)
        col = int(np.clip(np.floor(x_px * cols / safe_width), 0, cols - 1))
        row = int(np.clip(np.floor(y_px * rows / safe_height), 0, rows - 1))
        return row, col

    def _edge_grid_cells(self, cols: int, rows: int) -> set[tuple[int, int]]:
        edge_cells: set[tuple[int, int]] = set()
        for col in range(cols):
            edge_cells.add((0, col))
            edge_cells.add((rows - 1, col))
        for row in range(rows):
            edge_cells.add((row, 0))
            edge_cells.add((row, cols - 1))
        return edge_cells

    def _rounded_float_sequence(
        self,
        values: tuple[float, ...] | list[float] | None,
    ) -> list[float] | None:
        if values is None:
            return None
        return [round(float(value), 3) for value in values]

    def _spatial_coverage_diagnostics(self, summary: dict[str, Any]) -> list[str]:
        if int(summary.get("sample_count", 0)) <= 0:
            return []

        grid_ratio = float(summary.get("grid_coverage_ratio", 0.0))
        credited_grid_ratio = float(summary.get("credited_grid_coverage_ratio", grid_ratio))
        visited = int(summary.get("visited_cells", 0))
        credited_visited = int(summary.get("credited_visited_cells", visited))
        total = int(summary.get("total_cells", 0))
        center_spread_x = float(summary.get("center_spread_x_px", 0.0))
        center_spread_y = float(summary.get("center_spread_y_px", 0.0))
        edge_score = float(summary.get("edge_coverage_score", 0.0))
        credited_edge_score = float(summary.get("credited_edge_coverage_score", edge_score))
        corner_hits = summary.get("corner_cell_hits", {})
        if not isinstance(corner_hits, dict):
            corner_hits = {}
        credited_corner_hits = summary.get("credited_corner_cell_hits", corner_hits)
        if not isinstance(credited_corner_hits, dict):
            credited_corner_hits = {}

        diagnostics = [
            (
                "Spatial coverage metrics: "
                f"visited_cells={visited}, credited_visited_cells={credited_visited}, "
                f"total_cells={total}, grid_coverage_ratio={grid_ratio:.3f}, "
                f"credited_grid_coverage_ratio={credited_grid_ratio:.3f}, "
                f"center_spread_x_px={center_spread_x:.0f}, "
                f"center_spread_y_px={center_spread_y:.0f}, "
                f"edge_coverage_score={edge_score:.3f}, "
                f"credited_edge_coverage_score={credited_edge_score:.3f}."
            ),
            (
                "Spatial corner cells: "
                f"TL={int(corner_hits.get('top_left', 0))}, "
                f"TR={int(corner_hits.get('top_right', 0))}, "
                f"BL={int(corner_hits.get('bottom_left', 0))}, "
                f"BR={int(corner_hits.get('bottom_right', 0))}."
            ),
            (
                "Credited spatial corner cells: "
                f"TL={int(credited_corner_hits.get('top_left', 0))}, "
                f"TR={int(credited_corner_hits.get('top_right', 0))}, "
                f"BL={int(credited_corner_hits.get('bottom_left', 0))}, "
                f"BR={int(credited_corner_hits.get('bottom_right', 0))}."
            ),
            f"Spatial cell_hit_counts: {summary.get('cell_hit_counts', [])}.",
            f"Credited spatial cell_hit_counts: {summary.get('credited_cell_hit_counts', [])}.",
        ]
        if credited_grid_ratio < self._min_spatial_grid_coverage_ratio:
            diagnostics.append(
                "Insufficient spatial coverage: move the board through corners and edges. "
                f"Credited grid coverage {credited_grid_ratio * 100.0:.1f}% "
                f"< {self._min_spatial_grid_coverage_ratio * 100.0:.1f}%."
            )
        return diagnostics

    def _calibration_quality_summary(
        self,
        reprojection_error: float,
        mean_sample_quality: float,
        sample_count: int,
        spatial_summary: dict[str, Any],
    ) -> dict[str, float]:
        raw_grid_ratio = float(spatial_summary.get("grid_coverage_ratio", 0.0))
        grid_ratio = float(spatial_summary.get("credited_grid_coverage_ratio", raw_grid_ratio))
        raw_edge_score = float(spatial_summary.get("edge_coverage_score", 0.0))
        edge_score = float(spatial_summary.get("credited_edge_coverage_score", raw_edge_score))
        image_size = spatial_summary.get("image_size")
        center_spread_score = 0.0
        if isinstance(image_size, list) and len(image_size) == 2:
            width = max(float(image_size[0]), 1.0)
            height = max(float(image_size[1]), 1.0)
            center_spread_score = float(
                np.clip(
                    0.5
                    * (
                        float(spatial_summary.get("center_spread_x_px", 0.0)) / (width * 0.65)
                        + float(spatial_summary.get("center_spread_y_px", 0.0)) / (height * 0.65)
                    ),
                    0.0,
                    1.0,
                )
            )
        grid_score = float(
            np.clip(grid_ratio / max(self._min_spatial_grid_coverage_ratio, 0.01), 0.0, 1.0)
        )
        spatial_score = float(
            np.clip(0.60 * grid_score + 0.25 * edge_score + 0.15 * center_spread_score, 0.0, 1.0)
        )
        reprojection_score = float(np.clip(np.exp(-max(reprojection_error, 0.0) / 1.2), 0.0, 1.0))
        sample_count_score = float(
            np.clip(sample_count / max(float(self._min_samples_per_camera), 1.0), 0.0, 1.0)
        )
        base_score = float(
            np.clip(
                0.55 * reprojection_score
                + 0.25 * float(np.clip(mean_sample_quality, 0.0, 1.0))
                + 0.20 * sample_count_score,
                0.0,
                1.0,
            )
        )
        quality_score = float(np.clip(base_score * (0.35 + 0.65 * spatial_score), 0.0, 1.0))
        return {
            "score": quality_score,
            "base_score": base_score,
            "spatial_score": spatial_score,
            "grid_score": grid_score,
            "edge_score": edge_score,
            "center_spread_score": center_spread_score,
            "reprojection_score": reprojection_score,
            "sample_count_score": sample_count_score,
            "mean_sample_quality": float(np.clip(mean_sample_quality, 0.0, 1.0)),
        }

    def _normalize_pattern_name(self, pattern: Literal["chessboard", "charuco"] | str | None) -> str:
        selected_pattern = str(pattern or self._default_pattern).lower().strip()
        if selected_pattern not in {"chessboard", "charuco"}:
            return "chessboard"
        return selected_pattern

    def _build_sample_from_detection(
        self,
        source_id: str,
        detection: ChessboardDetectionResult,
        selected_pattern: str,
        acceptance_mode: Literal["intrinsics", "synchronized_relaxed"],
    ) -> tuple[CalibrationSample | None, str, list[str]]:
        current_count = self.observation_count(source_id, include_sync_only=True)
        if not detection.found or detection.corners is None:
            rejection = [f"{selected_pattern} not detected."]
            return None, f"{source_id}: {selected_pattern} not detected.", rejection

        existing_samples = self._samples.get(source_id, [])
        if existing_samples and any(sample.pattern_type != selected_pattern for sample in existing_samples):
            rejection = ["Mixed calibration patterns for one camera are not supported."]
            return None, f"{source_id}: rejected due to mixed pattern usage.", rejection

        size_set = {sample.image_size for sample in existing_samples}
        if size_set and detection.image_size not in size_set:
            rejection = ["Image size mismatch with previous samples."]
            return None, f"{source_id}: rejected due to inconsistent image size {detection.image_size}.", rejection

        rejection_reasons = self._capture_rejection_reasons(
            detection=detection,
            selected_pattern=selected_pattern,
            acceptance_mode=acceptance_mode,
        )
        if rejection_reasons:
            return None, f"{source_id}: sample rejected ({'; '.join(rejection_reasons)}).", rejection_reasons

        if selected_pattern == "charuco" and detection.charuco_ids is None:
            rejection = ["Charuco IDs missing after interpolation."]
            return None, f"{source_id}: sample rejected (Charuco IDs missing).", rejection

        if not self._is_sample_novel(existing_samples, detection):
            rejection = ["Sample too similar to existing captures; move/rotate board more."]
            return None, f"{source_id}: sample rejected (low novelty).", rejection

        object_points = self._object_template.copy()
        charuco_ids: NDArray[np.int32] | None = None
        if selected_pattern == "charuco" and detection.charuco_ids is not None:
            object_points = self._charuco_object_points_for_ids(detection.charuco_ids)
            charuco_ids = detection.charuco_ids.copy()

        corner_points_px = [
            (float(point[0]), float(point[1]))
            for point in detection.corners.reshape(-1, 2)
        ]
        board_bbox_px = detection.board_bbox_px
        board_center_px = detection.board_center_px
        if board_bbox_px is None or board_center_px is None:
            board_bbox_px, board_center_px = self._board_geometry_from_corners(detection.corners)

        sample = CalibrationSample(
            pattern_type=selected_pattern,  # type: ignore[arg-type]
            object_points=object_points,
            image_points=detection.corners.copy(),
            charuco_ids=charuco_ids,
            image_size=detection.image_size,
            quality_score=detection.quality_score,
            coverage_ratio=detection.coverage_ratio,
            sharpness_score=detection.sharpness_score,
            captured_at_iso=datetime.now().isoformat(),
            accepted_for_intrinsics=acceptance_mode == "intrinsics",
            corner_points_px=corner_points_px,
            board_bbox_px=board_bbox_px,
            board_center_px=board_center_px,
        )
        acceptance_suffix = ""
        if acceptance_mode == "synchronized_relaxed":
            acceptance_suffix = " | relaxed synchronized thresholds | sync-only"
        return (
            sample,
            (
                f"{source_id}: accepted sample #{current_count + 1} "
                f"(pattern={selected_pattern}, quality={detection.quality_score:.2f}, "
                f"coverage={detection.coverage_ratio * 100:.1f}%){acceptance_suffix}."
            ),
            [],
        )

    def _append_sample(self, source_id: str, sample: CalibrationSample) -> int:
        samples = self._samples.setdefault(source_id, [])
        samples.append(sample)
        return len(samples)

    def _is_sample_novel(self, samples: list[CalibrationSample], detection: ChessboardDetectionResult) -> bool:
        if not samples or detection.corners is None:
            return True
        current_points = detection.corners.reshape(-1, 2)
        current_centroid = np.mean(current_points, axis=0)
        recent = samples[-10:]
        for sample in recent:
            previous_points = sample.image_points.reshape(-1, 2)
            previous_centroid = np.mean(previous_points, axis=0)
            centroid_dist = float(np.linalg.norm(current_centroid - previous_centroid))
            coverage_delta = abs(detection.coverage_ratio - sample.coverage_ratio)
            if centroid_dist < self._min_sample_novelty_px and coverage_delta < 0.015:
                return False
        return True

    def _capture_rejection_reasons(
        self,
        detection: ChessboardDetectionResult,
        selected_pattern: str,
        acceptance_mode: Literal["intrinsics", "synchronized_relaxed"],
    ) -> list[str]:
        reasons: list[str] = []
        if acceptance_mode == "intrinsics":
            min_quality = self._min_quality_score
            min_coverage = self._min_coverage_ratio
            min_charuco_corners = self._min_charuco_corners
            mode_label = "intrinsics"
        else:
            min_quality = self._sync_min_quality_score
            min_coverage = self._sync_min_coverage_ratio
            min_charuco_corners = max(6, self._min_charuco_corners - 2)
            mode_label = "synchronized multi-camera capture"

        if detection.quality_score < min_quality:
            reasons.append(
                f"Quality too low for {mode_label} ({detection.quality_score:.2f} < {min_quality:.2f})."
            )
        if detection.coverage_ratio < min_coverage:
            reasons.append(
                "Coverage too low for "
                f"{mode_label} ({detection.coverage_ratio * 100:.1f}% < {min_coverage * 100:.1f}%)."
            )
        if selected_pattern == "charuco" and detection.detected_corners < min_charuco_corners:
            reasons.append(
                f"Too few Charuco corners for {mode_label} "
                f"({detection.detected_corners}/{min_charuco_corners})."
            )
        return reasons

    def _append_unique_diagnostics(
        self,
        detection: ChessboardDetectionResult,
        reasons: list[str],
    ) -> None:
        for reason in reasons:
            normalized = reason.strip()
            if normalized and normalized not in detection.diagnostics:
                detection.diagnostics.append(normalized)

    def _charuco_object_points_for_ids(self, ids: NDArray[np.int32]) -> FloatArray:
        board_corners = self._charuco_board_corners()
        if board_corners.size == 0:
            return np.zeros((0, 3), np.float32)
        flat_ids = ids.flatten()
        points: list[np.ndarray] = []
        for corner_id in flat_ids:
            if 0 <= int(corner_id) < board_corners.shape[0]:
                points.append(board_corners[int(corner_id)])
        if not points:
            return np.zeros((0, 3), np.float32)
        return np.array(points, dtype=np.float32)

    def _charuco_board_corners(self) -> FloatArray:
        if self._charuco_board is None:
            return np.zeros((0, 3), np.float32)
        if hasattr(self._charuco_board, "getChessboardCorners"):
            corners = self._charuco_board.getChessboardCorners()
            return np.array(corners, dtype=np.float32).reshape(-1, 3)
        corners = getattr(self._charuco_board, "chessboardCorners", None)
        if corners is None:
            return np.zeros((0, 3), np.float32)
        return np.array(corners, dtype=np.float32).reshape(-1, 3)

    def solve_intrinsics(self) -> CalibrationBundle:
        """Solve intrinsics per camera and include diagnostics/reprojection metrics."""
        cameras: dict[str, CameraCalibration] = {}
        notes: list[str] = []
        used_pattern_types: set[str] = set()
        spatial_coverage_by_camera: dict[str, dict[str, Any]] = {}
        calibration_quality_by_camera: dict[str, dict[str, float]] = {}

        for source_id in self.sources():
            all_samples = self._samples.get(source_id, [])
            samples = [sample for sample in all_samples if sample.accepted_for_intrinsics]
            used_pattern_types.update(sample.pattern_type for sample in samples)
            diagnostics: list[str] = []
            image_sizes = {sample.image_size for sample in all_samples}
            sample_count = len(samples)
            sync_only_count = len(all_samples) - sample_count

            if sync_only_count > 0:
                diagnostics.append(
                    f"Ignoring {sync_only_count} synchronized-only sample(s) accepted with relaxed thresholds."
                )

            if not all_samples:
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status="unsolved",
                    num_samples=0,
                    diagnostics=["No calibration samples captured."],
                    calibrated_at_iso=datetime.now().isoformat(),
                )
                continue

            if sample_count == 0:
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status="insufficient_data",
                    num_samples=0,
                    image_size=next(iter(image_sizes)) if image_sizes else None,
                    diagnostics=diagnostics
                    + ["No intrinsics-grade samples captured yet. Capture larger board views per camera."],
                    calibrated_at_iso=datetime.now().isoformat(),
                )
                continue

            if len(image_sizes) > 1:
                diag = "Inconsistent image sizes across samples."
                diagnostics.append(diag)
                notes.append(f"Camera {source_id}: {diag}")
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status="failed",
                    num_samples=sample_count,
                    image_size=next(iter(image_sizes)),
                    diagnostics=diagnostics,
                    calibrated_at_iso=datetime.now().isoformat(),
                )
                continue

            image_size = next(iter(image_sizes))
            mean_coverage = float(np.mean([sample.coverage_ratio for sample in samples]))
            spatial_summary = self.spatial_coverage_summary(source_id, samples=samples)
            spatial_coverage_by_camera[source_id] = spatial_summary
            for diagnostic in self._spatial_coverage_diagnostics(spatial_summary):
                diagnostics.append(diagnostic)
                if diagnostic.startswith("Insufficient spatial coverage:"):
                    notes.append(f"Camera {source_id}: {diagnostic}")

            if sample_count < self._min_samples_per_camera:
                warning = (
                    f"Too few frames ({sample_count}/{self._min_samples_per_camera}). "
                    "Calibration may be unstable."
                )
                diagnostics.append(warning)
                notes.append(f"Camera {source_id}: {warning}")

            if mean_coverage < self._min_coverage_ratio * 1.3:
                warning = (
                    f"Insufficient coverage (mean {mean_coverage * 100:.1f}%). "
                    "Capture wider board positions."
                )
                diagnostics.append(warning)
                notes.append(f"Camera {source_id}: {warning}")

            if sample_count < 3:
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status="insufficient_data",
                    num_samples=sample_count,
                    image_size=image_size,
                    diagnostics=diagnostics,
                    calibrated_at_iso=datetime.now().isoformat(),
                )
                continue

            pattern_types = {sample.pattern_type for sample in samples}
            if len(pattern_types) > 1:
                diag = "Mixed sample pattern types in one camera set."
                diagnostics.append(diag)
                notes.append(f"Camera {source_id}: {diag}")
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status="failed",
                    num_samples=sample_count,
                    image_size=image_size,
                    diagnostics=diagnostics,
                    calibrated_at_iso=datetime.now().isoformat(),
                )
                continue
            pattern_type = next(iter(pattern_types)) if pattern_types else "chessboard"
            diagnostics.append(f"Pattern: {pattern_type}")

            try:
                if pattern_type == "charuco":
                    if not self._charuco_available or self._charuco_board is None:
                        raise RuntimeError("cv2.aruco unavailable for Charuco calibration.")
                    # Each Charuco sample already stores matched object/image
                    # points (object points resolved from the detected corner
                    # ids), so calibrate with the version-agnostic
                    # cv2.calibrateCamera instead of aruco.calibrateCameraCharuco,
                    # which OpenCV >= 4.7 removed.
                    object_points = [
                        sample.object_points for sample in samples if sample.charuco_ids is not None
                    ]
                    image_points = [
                        sample.image_points for sample in samples if sample.charuco_ids is not None
                    ]
                    if len(image_points) < 3:
                        cameras[source_id] = CameraCalibration(
                            source_id=source_id,
                            status="insufficient_data",
                            num_samples=sample_count,
                            image_size=image_size,
                            diagnostics=diagnostics
                            + ["Need at least 3 valid Charuco captures with interpolated corners."],
                            calibrated_at_iso=datetime.now().isoformat(),
                        )
                        continue
                    _, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
                        object_points,
                        image_points,
                        image_size,
                        None,
                        None,
                    )
                    reprojection_error = self._compute_reprojection_error(
                        object_points=object_points,
                        image_points=image_points,
                        rvecs=rvecs,
                        tvecs=tvecs,
                        camera_matrix=camera_matrix,
                        distortion=dist_coeffs,
                    )
                else:
                    object_points = [sample.object_points for sample in samples]
                    image_points = [sample.image_points for sample in samples]
                    _, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
                        object_points,
                        image_points,
                        image_size,
                        None,
                        None,
                    )
                    reprojection_error = self._compute_reprojection_error(
                        object_points=object_points,
                        image_points=image_points,
                        rvecs=rvecs,
                        tvecs=tvecs,
                        camera_matrix=camera_matrix,
                        distortion=dist_coeffs,
                    )
                mean_sample_quality = float(np.mean([sample.quality_score for sample in samples]))
                quality_summary = self._calibration_quality_summary(
                    reprojection_error=reprojection_error,
                    mean_sample_quality=mean_sample_quality,
                    sample_count=sample_count,
                    spatial_summary=spatial_summary,
                )
                calibration_quality_by_camera[source_id] = quality_summary
                spatial_summary["calibration_quality_score"] = quality_summary["score"]
                spatial_summary["quality_components"] = quality_summary
                diagnostics.append(
                    "Calibration quality score: "
                    f"{quality_summary['score']:.2f} "
                    f"(reprojection score {quality_summary['reprojection_score']:.2f}, "
                    f"spatial score {quality_summary['spatial_score']:.2f})."
                )
                status = "solved_with_warnings" if diagnostics else "solved"
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status=status,
                    num_samples=sample_count,
                    intrinsics=camera_matrix.tolist(),
                    distortion=dist_coeffs.flatten().tolist(),
                    # Intrinsics solve does not determine global camera extrinsics.
                    rotation=None,
                    translation=None,
                    image_size=image_size,
                    reprojection_error=reprojection_error,
                    diagnostics=diagnostics
                    + [
                        "Extrinsics not solved in this step. "
                        "Load/provide world-referenced rotation+translation for real triangulation."
                    ],
                    calibrated_at_iso=datetime.now().isoformat(),
                )
            except Exception as exc:
                failure = f"Calibration failed ({exc})."
                diagnostics.append(failure)
                notes.append(f"Camera {source_id}: {failure}")
                cameras[source_id] = CameraCalibration(
                    source_id=source_id,
                    status="failed",
                    num_samples=sample_count,
                    image_size=image_size,
                    diagnostics=diagnostics,
                    calibrated_at_iso=datetime.now().isoformat(),
                )

        if not cameras:
            notes.append("No observations available. Capture chessboard or Charuco samples first.")

        notes.append("Next step: solve synchronized multi-camera extrinsics from shared calibration captures.")
        notes.append("TODO: Add bundle-adjustment refinement over intrinsics+extrinsics.")
        notes.append("TODO: Add pairwise baseline diagnostics and epipolar residual plots.")
        notes.append(
            "Triangulation assumption: camera rotation/translation must be world-to-camera extrinsics "
            "provided separately from intrinsics solve."
        )

        if len(used_pattern_types) == 1:
            active_pattern = next(iter(used_pattern_types))
        elif used_pattern_types:
            active_pattern = "mixed"
        else:
            active_pattern = self._default_pattern

        if active_pattern == "charuco":
            calibration_board = {
                "type": "charuco",
                "squares": [self._charuco_squares_x, self._charuco_squares_y],
                "square_size_m": self._charuco_square_size_m,
                "marker_size_m": self._charuco_marker_size_m,
            }
        elif active_pattern == "chessboard":
            calibration_board = {
                "type": "chessboard",
                "inner_corners": list(self._board_shape),
                "square_size_m": self._square_size_m,
            }
        else:
            calibration_board = {
                "type": "mixed",
                "chessboard": {
                    "inner_corners": list(self._board_shape),
                    "square_size_m": self._square_size_m,
                },
                "charuco": {
                    "squares": [self._charuco_squares_x, self._charuco_squares_y],
                    "square_size_m": self._charuco_square_size_m,
                    "marker_size_m": self._charuco_marker_size_m,
                },
            }

        spatial_ratios = [
            float(summary.get("grid_coverage_ratio", 0.0))
            for summary in spatial_coverage_by_camera.values()
            if int(summary.get("sample_count", 0)) > 0
        ]
        quality_scores = [
            float(summary.get("score", 0.0))
            for summary in calibration_quality_by_camera.values()
        ]

        bundle = CalibrationBundle(
            cameras=cameras,
            notes=notes,
            metadata=self._attach_metadata_help(
                {
                    "schema_version": CALIBRATION_SCHEMA_VERSION,
                    "active_pattern": active_pattern,
                    "pattern_types": sorted(used_pattern_types),
                    "calibration_board": calibration_board,
                    "sample_collection": self.sample_collection_metadata(),
                    "min_samples_per_camera": self._min_samples_per_camera,
                    "min_quality_score": self._min_quality_score,
                    "min_coverage_ratio": self._min_coverage_ratio,
                    "spatial_coverage": {
                        "grid": {
                            "cols": self._spatial_grid_shape[0],
                            "rows": self._spatial_grid_shape[1],
                            "total_cells": self._spatial_grid_shape[0] * self._spatial_grid_shape[1],
                        },
                        "min_grid_coverage_ratio": self._min_spatial_grid_coverage_ratio,
                        "sample_filter": "intrinsics",
                        "overall_grid_coverage_ratio": (
                            float(np.mean(spatial_ratios)) if spatial_ratios else 0.0
                        ),
                        "per_camera": spatial_coverage_by_camera,
                    },
                    "calibration_quality": {
                        "overall_score": float(np.mean(quality_scores)) if quality_scores else 0.0,
                        "per_camera": calibration_quality_by_camera,
                    },
                    "charuco_available": self._charuco_available and self._charuco_board is not None,
                    "solved_at_iso": datetime.now().isoformat(),
                }
            ),
        )
        self._last_solution = bundle
        return bundle

    def solve_extrinsics(
        self,
        base_bundle: CalibrationBundle | None = None,
        reference_source_id: str | None = None,
    ) -> CalibrationBundle:
        """Solve pairwise extrinsics against a reference camera using synchronized capture sets."""
        working_bundle = copy.deepcopy(base_bundle or self._last_solution or self.solve_intrinsics())
        working_bundle.metadata = dict(working_bundle.metadata)
        cameras = working_bundle.cameras
        solved_at_iso = datetime.now().isoformat()
        notes = self._strip_extrinsics_placeholder_notes(working_bundle.notes)

        solved_intrinsics_ids = [
            source_id
            for source_id, camera in cameras.items()
            if camera.intrinsics is not None and camera.distortion is not None
        ]
        if len(solved_intrinsics_ids) < 2:
            notes.append("Extrinsics solve requires at least two cameras with valid intrinsics.")
            working_bundle.notes = self._dedupe_strings(notes)
            self._last_solution = working_bundle
            return working_bundle

        if not self._capture_sets:
            notes.append("No synchronized calibration capture sets available for extrinsics solve.")
            working_bundle.notes = self._dedupe_strings(notes)
            self._last_solution = working_bundle
            return working_bundle

        reference_id = (
            reference_source_id
            if reference_source_id in solved_intrinsics_ids
            else sorted(solved_intrinsics_ids)[0]
        )
        reference_camera = cameras[reference_id]
        reference_camera.rotation = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        reference_camera.translation = [0.0, 0.0, 0.0]
        reference_camera.status = self._status_with_extrinsics(reference_camera.status, has_warning=False)
        reference_camera.calibrated_at_iso = solved_at_iso
        reference_camera.diagnostics = self._dedupe_strings(
            [
                diag
                for diag in reference_camera.diagnostics
                if not diag.startswith("Extrinsics not solved in this step.")
            ]
            + [f"Reference camera for extrinsics solve: {reference_id}."]
        )

        metadata_extrinsics: dict[str, object] = {
            "reference_source_id": reference_id,
            "solved_at_iso": solved_at_iso,
            reference_id: {
                "rotation": list(reference_camera.rotation),
                "translation": list(reference_camera.translation),
                "stereo_rms": 0.0,
                "baseline_m": 0.0,
                "pair_count": 0,
                "status": "reference_camera",
            },
        }

        solved_pairs = 0
        for source_id in sorted(solved_intrinsics_ids):
            if source_id == reference_id:
                continue

            pair_data = self._collect_stereo_observations(
                reference_source_id=reference_id,
                target_source_id=source_id,
            )
            camera = cameras[source_id]
            if pair_data is None:
                message = (
                    f"No usable synchronized capture sets shared by {reference_id} and {source_id} "
                    "for extrinsics solve."
                )
                camera.diagnostics = self._dedupe_strings(camera.diagnostics + [message])
                if camera.status.startswith("solved"):
                    camera.status = "solved_with_warnings"
                notes.append(f"Camera {source_id}: {message}")
                continue

            object_points, image_points_ref, image_points_target, image_size, pair_notes = pair_data
            reference_matrix = np.array(reference_camera.intrinsics, dtype=np.float64)
            reference_distortion = np.array(reference_camera.distortion, dtype=np.float64).reshape(-1, 1)
            camera_matrix = np.array(camera.intrinsics, dtype=np.float64)
            distortion = np.array(camera.distortion, dtype=np.float64).reshape(-1, 1)

            try:
                retval, _, _, _, _, rotation_matrix, translation_vec, _, _ = cv2.stereoCalibrate(
                    object_points,
                    image_points_ref,
                    image_points_target,
                    reference_matrix.copy(),
                    reference_distortion.copy(),
                    camera_matrix.copy(),
                    distortion.copy(),
                    image_size,
                    criteria=(
                        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                        100,
                        1e-6,
                    ),
                    flags=cv2.CALIB_FIX_INTRINSIC,
                )
            except Exception as exc:
                message = f"Stereo extrinsics solve failed ({exc})."
                camera.diagnostics = self._dedupe_strings(camera.diagnostics + [message])
                if camera.status.startswith("solved"):
                    camera.status = "solved_with_warnings"
                notes.append(f"Camera {source_id}: {message}")
                continue

            baseline_m = float(np.linalg.norm(np.array(translation_vec, dtype=np.float64).reshape(3)))
            solve_summary = (
                f"Extrinsics solved relative to {reference_id}: stereo RMS {float(retval):.4f}, "
                f"baseline {baseline_m:.3f} m, synchronized sets {len(object_points)}."
            )
            has_warning = bool(pair_notes) or float(retval) > 1.2 or len(object_points) < 3

            camera.rotation = np.array(rotation_matrix, dtype=np.float64).reshape(-1).tolist()
            camera.translation = np.array(translation_vec, dtype=np.float64).reshape(-1).tolist()
            camera.status = self._status_with_extrinsics(camera.status, has_warning=has_warning)
            camera.calibrated_at_iso = solved_at_iso
            camera.diagnostics = self._dedupe_strings(
                [
                    diag
                    for diag in camera.diagnostics
                    if not diag.startswith("Extrinsics not solved in this step.")
                ]
                + pair_notes
                + [solve_summary]
            )

            metadata_extrinsics[source_id] = {
                "rotation": list(camera.rotation),
                "translation": list(camera.translation),
                "stereo_rms": float(retval),
                "baseline_m": baseline_m,
                "pair_count": len(object_points),
                "status": camera.status,
            }
            notes.append(f"Camera {source_id}: {solve_summary}")
            solved_pairs += 1

        if solved_pairs == 0:
            notes.append("Extrinsics solve completed without any usable camera pairs.")
        else:
            notes.append(
                f"Extrinsics solved for {solved_pairs + 1} camera(s) with {reference_id} as reference."
            )
        notes.append(
            f"World coordinate frame is anchored to reference camera {reference_id} "
            "(world-to-camera extrinsics stored per camera)."
        )

        working_bundle.metadata["extrinsics"] = metadata_extrinsics
        working_bundle.metadata["extrinsics_reference_source_id"] = reference_id
        working_bundle.metadata["extrinsics_solved_at_iso"] = solved_at_iso
        working_bundle.metadata["sample_collection"] = self.sample_collection_metadata()
        if self._samples:
            spatial_coverage = self.spatial_coverage_metadata()
            existing_spatial = working_bundle.metadata.get("spatial_coverage")
            existing_per_camera = (
                existing_spatial.get("per_camera", {})
                if isinstance(existing_spatial, dict)
                else {}
            )
            if isinstance(existing_per_camera, dict):
                for source_id, summary in spatial_coverage.get("per_camera", {}).items():
                    previous = existing_per_camera.get(source_id, {})
                    if not isinstance(previous, dict):
                        continue
                    for key in ("calibration_quality_score", "quality_components"):
                        if key in previous:
                            summary[key] = previous[key]
            working_bundle.metadata["spatial_coverage"] = spatial_coverage
        self._attach_metadata_help(working_bundle.metadata)
        working_bundle.notes = self._dedupe_strings(notes)
        self._last_solution = working_bundle
        return working_bundle

    def _collect_stereo_observations(
        self,
        reference_source_id: str,
        target_source_id: str,
    ) -> tuple[list[FloatArray], list[FloatArray], list[FloatArray], tuple[int, int], list[str]] | None:
        object_points: list[FloatArray] = []
        image_points_ref: list[FloatArray] = []
        image_points_target: list[FloatArray] = []
        image_size: tuple[int, int] | None = None
        skipped_sets = 0

        for capture_set in self._capture_sets:
            reference_sample = capture_set.samples_by_source.get(reference_source_id)
            target_sample = capture_set.samples_by_source.get(target_source_id)
            if reference_sample is None or target_sample is None:
                continue

            pair = self._build_stereo_observation_pair(
                reference_sample=reference_sample,
                target_sample=target_sample,
            )
            if pair is None:
                skipped_sets += 1
                continue

            pair_object_points, pair_ref_points, pair_target_points = pair
            object_points.append(pair_object_points)
            image_points_ref.append(pair_ref_points)
            image_points_target.append(pair_target_points)
            image_size = reference_sample.image_size

        if not object_points or image_size is None:
            return None

        notes: list[str] = []
        if len(object_points) < 3:
            notes.append(
                f"Only {len(object_points)} synchronized set(s) available; extrinsics may be unstable."
            )
        if skipped_sets > 0:
            notes.append(f"Skipped {skipped_sets} synchronized capture set(s) due to mismatched detections.")
        return object_points, image_points_ref, image_points_target, image_size, notes

    def _build_stereo_observation_pair(
        self,
        reference_sample: CalibrationSample,
        target_sample: CalibrationSample,
    ) -> tuple[FloatArray, FloatArray, FloatArray] | None:
        if reference_sample.pattern_type != target_sample.pattern_type:
            return None

        if reference_sample.pattern_type == "charuco":
            return self._match_charuco_pair(reference_sample=reference_sample, target_sample=target_sample)

        reference_points = reference_sample.image_points.astype(np.float32)
        target_points = target_sample.image_points.astype(np.float32)
        object_points = reference_sample.object_points.astype(np.float32)
        if reference_points.shape[0] != target_points.shape[0]:
            return None
        if object_points.shape[0] != reference_points.shape[0]:
            return None
        if object_points.shape[0] < 4:
            return None
        return object_points, reference_points, target_points

    def _match_charuco_pair(
        self,
        reference_sample: CalibrationSample,
        target_sample: CalibrationSample,
    ) -> tuple[FloatArray, FloatArray, FloatArray] | None:
        if reference_sample.charuco_ids is None or target_sample.charuco_ids is None:
            return None

        reference_ids = reference_sample.charuco_ids.reshape(-1)
        target_ids = target_sample.charuco_ids.reshape(-1)
        reference_points = reference_sample.image_points.reshape(-1, 2)
        target_points = target_sample.image_points.reshape(-1, 2)

        reference_map = {int(corner_id): reference_points[idx] for idx, corner_id in enumerate(reference_ids)}
        target_map = {int(corner_id): target_points[idx] for idx, corner_id in enumerate(target_ids)}
        shared_ids = sorted(set(reference_map.keys()) & set(target_map.keys()))
        if len(shared_ids) < 4:
            return None

        ids_array = np.array(shared_ids, dtype=np.int32).reshape(-1, 1)
        object_points = self._charuco_object_points_for_ids(ids_array)
        if object_points.shape[0] != len(shared_ids):
            return None

        matched_reference = np.array([reference_map[corner_id] for corner_id in shared_ids], dtype=np.float32)
        matched_target = np.array([target_map[corner_id] for corner_id in shared_ids], dtype=np.float32)
        return (
            object_points.astype(np.float32),
            matched_reference.reshape(-1, 1, 2),
            matched_target.reshape(-1, 1, 2),
        )

    def _status_with_extrinsics(self, current_status: str, has_warning: bool) -> str:
        if not current_status.startswith("solved"):
            return current_status
        if has_warning or "warning" in current_status:
            return "solved_with_warnings_extrinsics"
        return "solved_extrinsics"

    def _strip_extrinsics_placeholder_notes(self, notes: list[str]) -> list[str]:
        drop_prefixes = (
            "TODO: Add synchronized multi-camera extrinsics",
            "Next step: solve synchronized multi-camera extrinsics",
            "Triangulation assumption:",
            "World coordinate frame is anchored to reference camera",
            "Extrinsics solved for ",
        )
        return [note for note in notes if not note.startswith(drop_prefixes)]

    def _dedupe_strings(self, items: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            output.append(normalized)
            seen.add(normalized)
        return output

    def undistort_frame(
        self,
        source_id: str,
        frame_bgr: U8Array,
        bundle: CalibrationBundle | None,
    ) -> U8Array:
        """Apply undistortion for preview if intrinsics exist for this camera."""
        if bundle is None:
            return frame_bgr
        camera = bundle.cameras.get(source_id)
        if camera is None or camera.intrinsics is None or camera.distortion is None:
            return frame_bgr

        matrix = np.array(camera.intrinsics, dtype=np.float64)
        distortion = np.array(camera.distortion, dtype=np.float64)
        height, width = frame_bgr.shape[:2]

        # Precompute and cache the rectify maps; rebuilding them every frame
        # (as cv2.undistort does internally) is the dominant cost when
        # undistort preview is enabled. The result is identical to
        # cv2.undistort because we reuse the same camera matrix for the new
        # camera matrix argument.
        signature = (
            matrix.tobytes(),
            distortion.tobytes(),
            int(width),
            int(height),
        )
        with self._undistort_lock:
            cached = self._undistort_map_cache.get(source_id)
            if cached is None or cached[0] != signature:
                map1, map2 = cv2.initUndistortRectifyMap(
                    matrix,
                    distortion,
                    None,
                    matrix,
                    (int(width), int(height)),
                    cv2.CV_16SC2,
                )
                cached = (signature, map1, map2)
                self._undistort_map_cache[source_id] = cached
            _signature, map1, map2 = cached
        return cv2.remap(frame_bgr, map1, map2, interpolation=cv2.INTER_LINEAR)

    def draw_detection_overlay(
        self,
        frame_bgr: U8Array,
        detection: ChessboardDetectionResult,
        accepted: bool | None = None,
        sample_count: int | None = None,
        mirror_x: bool = False,
        spatial_target_samples_per_cell: int | None = None,
        overlay_scale: float = 1.0,
    ) -> U8Array:
        """Render detection and diagnostics overlay for calibration preview.

        Every overlay element is sized proportionally to the frame height (720p
        is the reference) and multiplied by ``overlay_scale``, so the overlay
        keeps the same on-screen size regardless of capture or preview
        resolution while staying user-adjustable.
        """
        rendered = frame_bgr.copy()
        height, width = rendered.shape[:2]
        scale = max(0.1, (height / 720.0) * float(overlay_scale))
        self._draw_spatial_grid_overlay(
            rendered,
            detection,
            mirror_x=mirror_x,
            target_samples_per_cell=spatial_target_samples_per_cell,
            overlay_scale=float(overlay_scale),
        )
        if detection.found and detection.corners is not None:
            self._draw_corner_markers(rendered, detection, scale)

        status_text = "Detected" if detection.found else "Not detected"
        if accepted is True:
            status_text = "Accepted"
        elif accepted is False:
            status_text = "Rejected"

        color = (70, 220, 120) if detection.found else (80, 120, 255)
        if accepted is False:
            color = (60, 100, 255)

        sample_text = f" | samples:{sample_count}" if sample_count is not None else ""
        header_text = f"{detection.source_id}{sample_text} | {detection.pattern_type} | {status_text}"
        metrics_text = (
            f"corners:{detection.detected_corners} | "
            f"quality:{detection.quality_score:.2f} | coverage:{detection.coverage_ratio * 100:.1f}%"
        )
        diagnostics_text = detection.diagnostics[0] if detection.diagnostics else ""

        line_height = 22.0 * scale
        band_height = max(1, int(round(line_height * (3.5 if diagnostics_text else 2.4))))
        canvas = np.zeros((height + band_height, width, 3), dtype=rendered.dtype)
        canvas[band_height:, :] = rendered
        rendered = canvas

        x = int(round(12 * scale))
        header_scale = 0.56 * scale
        metrics_scale = 0.48 * scale
        header_outline = max(2, int(round(4 * scale)))
        header_inner = max(1, int(round(2 * scale)))
        thin_outline = max(1, int(round(3 * scale)))
        thin_inner = max(1, int(round(1 * scale)))

        cv2.putText(
            rendered, header_text, (x, int(round(line_height))),
            cv2.FONT_HERSHEY_SIMPLEX, header_scale, (5, 8, 10), header_outline, cv2.LINE_AA,
        )
        cv2.putText(
            rendered, header_text, (x, int(round(line_height))),
            cv2.FONT_HERSHEY_SIMPLEX, header_scale, color, header_inner, cv2.LINE_AA,
        )
        cv2.putText(
            rendered, metrics_text, (x, int(round(line_height * 2.0))),
            cv2.FONT_HERSHEY_SIMPLEX, metrics_scale, (5, 8, 10), thin_outline, cv2.LINE_AA,
        )
        cv2.putText(
            rendered, metrics_text, (x, int(round(line_height * 2.0))),
            cv2.FONT_HERSHEY_SIMPLEX, metrics_scale, (225, 235, 245), thin_inner, cv2.LINE_AA,
        )
        if diagnostics_text:
            cv2.putText(
                rendered, diagnostics_text, (x, int(round(line_height * 3.0))),
                cv2.FONT_HERSHEY_SIMPLEX, metrics_scale, (5, 8, 10), thin_outline, cv2.LINE_AA,
            )
            cv2.putText(
                rendered, diagnostics_text, (x, int(round(line_height * 3.0))),
                cv2.FONT_HERSHEY_SIMPLEX, metrics_scale, (150, 205, 255), thin_inner, cv2.LINE_AA,
            )
        return rendered

    def _draw_corner_markers(
        self,
        rendered: U8Array,
        detection: ChessboardDetectionResult,
        scale: float,
    ) -> None:
        """Draw detected corners with a resolution-independent marker size."""
        points = np.asarray(detection.corners, dtype=np.float32).reshape(-1, 2)
        if points.size == 0:
            return
        radius = max(2, int(round(4 * scale)))
        line_thickness = max(1, int(round(1.6 * scale)))
        if detection.pattern_type != "charuco":
            polyline = points.astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(rendered, [polyline], False, (0, 165, 255), line_thickness, cv2.LINE_AA)
        for point in points:
            center = (int(round(point[0])), int(round(point[1])))
            cv2.circle(rendered, center, radius, (70, 220, 120), -1, cv2.LINE_AA)
            cv2.circle(rendered, center, radius, (15, 25, 20), max(1, line_thickness // 2), cv2.LINE_AA)

    def _draw_spatial_grid_overlay(
        self,
        rendered: U8Array,
        detection: ChessboardDetectionResult,
        mirror_x: bool = False,
        target_samples_per_cell: int | None = None,
        overlay_scale: float = 1.0,
    ) -> None:
        height, width = rendered.shape[:2]
        cols, rows = self._spatial_grid_shape
        if width <= 0 or height <= 0 or cols <= 0 or rows <= 0:
            return
        scale = max(0.1, (height / 720.0) * float(overlay_scale))
        line_outline = max(1, int(round(3 * scale)))
        line_inner = max(1, int(round(1 * scale)))
        target = max(1, int(target_samples_per_cell or 3))

        summary = self.spatial_coverage_summary(
            detection.source_id,
            include_sync_only=False,
            include_sample_summaries=False,
            target_samples_per_cell=target,
        )
        hit_counts = summary.get("credited_cell_hit_counts", [])
        current_cells: set[tuple[int, int]] = set()
        if detection.found and detection.corners is not None:
            current_cells, _ = self._spatial_cells_for_detection(detection, image_size=(width, height))

        cell_width = width / cols
        cell_height = height / rows
        tint = rendered.copy()
        for row in range(rows):
            for col in range(cols):
                x0 = int(round(col * cell_width))
                y0 = int(round(row * cell_height))
                x1 = int(round((col + 1) * cell_width))
                y1 = int(round((row + 1) * cell_height))
                hit_count = 0
                if isinstance(hit_counts, list) and row < len(hit_counts):
                    row_counts = hit_counts[row]
                    source_col = cols - 1 - col if mirror_x else col
                    if isinstance(row_counts, list) and source_col < len(row_counts):
                        hit_count = int(row_counts[source_col])
                if hit_count > 0:
                    cv2.rectangle(tint, (x0, y0), (x1, y1), self._spatial_cell_tint(hit_count, target), -1)
        rendered[:] = cv2.addWeighted(tint, 0.12, rendered, 0.88, 0.0)

        for row in range(rows):
            for col in range(cols):
                x0 = int(round(col * cell_width))
                y0 = int(round(row * cell_height))
                x1 = int(round((col + 1) * cell_width))
                y1 = int(round((row + 1) * cell_height))

                grid_color = (235, 245, 250)
                if col > 0:
                    cv2.line(rendered, (x0, 0), (x0, height), (25, 30, 35), line_outline, cv2.LINE_AA)
                    cv2.line(rendered, (x0, 0), (x0, height), grid_color, line_inner, cv2.LINE_AA)
                if row > 0:
                    cv2.line(rendered, (0, y0), (width, y0), (25, 30, 35), line_outline, cv2.LINE_AA)
                    cv2.line(rendered, (0, y0), (width, y0), grid_color, line_inner, cv2.LINE_AA)

                hit_count = 0
                if isinstance(hit_counts, list) and row < len(hit_counts):
                    row_counts = hit_counts[row]
                    source_col = cols - 1 - col if mirror_x else col
                    if isinstance(row_counts, list) and source_col < len(row_counts):
                        hit_count = int(row_counts[source_col])
                self._draw_spatial_cell_counter(
                    rendered=rendered,
                    x0=x0,
                    y0=y0,
                    hit_count=hit_count,
                    target=target,
                    cell_width=cell_width,
                    cell_height=cell_height,
                    scale=scale,
                    overlay_scale=float(overlay_scale),
                )
                if (row, col) in current_cells:
                    cv2.rectangle(
                        rendered, (x0 + 1, y0 + 1), (x1 - 1, y1 - 1),
                        (0, 220, 255), max(1, int(round(2 * scale))), cv2.LINE_AA,
                    )

        visited = int(summary.get("credited_visited_cells", 0))
        total = int(summary.get("total_cells", cols * rows))
        grid_ratio = float(summary.get("credited_grid_coverage_ratio", 0.0))
        label = f"coverage grid {visited}/{total} ({grid_ratio * 100.0:.0f}%)"
        label_margin = int(round(12 * scale))
        label_y = max(int(round(22 * scale)), height - label_margin)
        cv2.putText(
            rendered,
            label,
            (label_margin, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58 * scale,
            (5, 8, 10),
            max(2, int(round(5 * scale))),
            cv2.LINE_AA,
        )
        cv2.putText(
            rendered,
            label,
            (label_margin, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58 * scale,
            (235, 245, 255),
            max(1, int(round(2 * scale))),
            cv2.LINE_AA,
        )

    def _spatial_cell_tint(self, hit_count: int, target: int) -> tuple[int, int, int]:
        if hit_count >= target:
            return (60, 185, 80)
        if hit_count >= max(1, int(np.ceil(target * 2.0 / 3.0))):
            return (70, 205, 150)
        return (95, 215, 240)

    def _draw_spatial_cell_counter(
        self,
        rendered: U8Array,
        x0: int,
        y0: int,
        hit_count: int,
        target: int,
        cell_width: float,
        cell_height: float,
        scale: float = 1.0,
        overlay_scale: float = 1.0,
    ) -> None:
        text = f"{hit_count}/{target}"
        font_scale = float(np.clip(min(cell_width, cell_height) / 160.0, 0.50, 0.72)) * float(overlay_scale)
        thickness = max(1, int(round(2 * scale)))
        text_size, baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        pad_x = max(2, int(round(7 * scale)))
        pad_y = max(2, int(round(8 * scale)))
        text_x = x0 + pad_x
        text_y = y0 + pad_y + text_size[1]
        bg_pad = max(2, int(round(4 * scale)))
        cv2.rectangle(
            rendered,
            (max(x0 + 2, text_x - bg_pad), max(y0 + 2, text_y - text_size[1] - bg_pad)),
            (
                min(int(round(x0 + cell_width)) - 2, text_x + text_size[0] + bg_pad),
                min(int(round(y0 + cell_height)) - 2, text_y + baseline + bg_pad),
            ),
            (15, 20, 24),
            -1,
        )
        cv2.putText(
            rendered,
            text,
            (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            thickness + max(2, int(round(3 * scale))),
            cv2.LINE_AA,
        )
        cv2.putText(
            rendered,
            text,
            (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (245, 250, 255),
            thickness,
            cv2.LINE_AA,
        )

    def _build_object_points(self) -> FloatArray:
        cols, rows = self._board_shape
        grid = np.zeros((cols * rows, 3), np.float32)
        grid[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
        return grid * self._square_size_m

    def _compute_reprojection_error(
        self,
        object_points: list[FloatArray],
        image_points: list[FloatArray],
        rvecs: tuple[NDArray[np.float64], ...] | list[NDArray[np.float64]],
        tvecs: tuple[NDArray[np.float64], ...] | list[NDArray[np.float64]],
        camera_matrix: NDArray[np.float64],
        distortion: NDArray[np.float64],
    ) -> float:
        total_error = 0.0
        total_views = 0
        for obj, img, rvec, tvec in zip(object_points, image_points, rvecs, tvecs):
            projected, _ = cv2.projectPoints(obj, rvec, tvec, camera_matrix, distortion)
            error = cv2.norm(img, projected, cv2.NORM_L2) / max(len(projected), 1)
            total_error += float(error)
            total_views += 1
        if total_views == 0:
            return 0.0
        return total_error / total_views
