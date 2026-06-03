from __future__ import annotations

import logging
import math
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from mocap_app.models.types import FramePacket, Pose2D, Pose2DKeypoint


LOGGER = logging.getLogger(__name__)
APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASK_MODEL_VARIANT = "full"
TASK_MODEL_DOWNLOAD_URLS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
}


def _coerce_float(value: object, fallback: float = 0.0) -> float:
    """Convert MediaPipe values to plain floats without crashing on ndarray wrappers."""
    if value is None:
        return fallback
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, np.generic):
        return float(value)
    try:
        array = np.asarray(value, dtype=np.float64)
    except Exception:
        try:
            return float(value)  # type: ignore[arg-type]
        except Exception:
            return fallback
    if array.ndim == 0:
        return float(array)
    if array.size == 0:
        return fallback
    return float(array.reshape(-1)[0])


def _expand_candidate_path(path: Path) -> list[Path]:
    if path.is_absolute():
        return [path]
    return [APP_ROOT / path, Path.cwd() / path]


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = os.path.normcase(str(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(path)
    return unique


def _infer_model_variant(path_value: str | os.PathLike[str] | None) -> str | None:
    if path_value is None:
        return None
    lower_name = Path(path_value).name.lower()
    for variant in TASK_MODEL_DOWNLOAD_URLS:
        if variant in lower_name:
            return variant
    return None


def _download_to_path(url: str, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "MocapStudio/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temp_path.open("wb") as output_file:
            shutil.copyfileobj(response, output_file, length=1024 * 1024)
        temp_path.replace(target_path)
    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        raise


class PoseDetector(Protocol):
    @property
    def name(self) -> str:
        ...

    def detect(self, frame: FramePacket) -> Pose2D:
        ...


class PlaceholderPoseDetector:
    """Deterministic fake pose for rapid UI and pipeline validation."""

    name = "placeholder_pose"

    def detect(self, frame: FramePacket) -> Pose2D:
        t = frame.frame_index * 0.1
        sway = 0.08 * math.sin(t)
        bob = 0.03 * math.cos(t * 0.5)
        center_x = 0.5 + sway
        center_y = 0.56 + bob

        keypoints = [
            Pose2DKeypoint("nose", center_x, center_y - 0.22, 0.65),
            Pose2DKeypoint("left_shoulder", center_x - 0.11, center_y - 0.12, 0.70),
            Pose2DKeypoint("right_shoulder", center_x + 0.11, center_y - 0.12, 0.70),
            Pose2DKeypoint("left_elbow", center_x - 0.17, center_y + 0.02, 0.64),
            Pose2DKeypoint("right_elbow", center_x + 0.17, center_y + 0.02, 0.64),
            Pose2DKeypoint("left_wrist", center_x - 0.20, center_y + 0.17, 0.60),
            Pose2DKeypoint("right_wrist", center_x + 0.20, center_y + 0.17, 0.60),
            Pose2DKeypoint("left_hip", center_x - 0.08, center_y + 0.12, 0.72),
            Pose2DKeypoint("right_hip", center_x + 0.08, center_y + 0.12, 0.72),
            Pose2DKeypoint("left_knee", center_x - 0.10, center_y + 0.30, 0.66),
            Pose2DKeypoint("right_knee", center_x + 0.10, center_y + 0.30, 0.66),
            Pose2DKeypoint("left_ankle", center_x - 0.12, center_y + 0.46, 0.61),
            Pose2DKeypoint("right_ankle", center_x + 0.12, center_y + 0.46, 0.61),
        ]

        return Pose2D(
            source_id=frame.source_id,
            frame_index=frame.frame_index,
            timestamp_sec=frame.timestamp_sec,
            keypoints=keypoints,
        )


class MediaPipePoseDetector:
    name = "mediapipe_pose"

    def __init__(self) -> None:
        try:
            import mediapipe as mp  # type: ignore
        except ImportError as exc:
            raise RuntimeError("mediapipe is not installed.") from exc

        if not hasattr(mp, "solutions"):
            raise RuntimeError(
                "Installed mediapipe package does not expose 'mediapipe.solutions'. "
                "This build will use MediaPipe Tasks instead."
            )

        self._mp = mp
        try:
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                smooth_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._landmarks = mp.solutions.pose.PoseLandmark
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize MediaPipe Pose: {exc}") from exc

    def detect(self, frame: FramePacket) -> Pose2D:
        rgb = cv2.cvtColor(frame.frame_bgr, cv2.COLOR_BGR2RGB)
        prediction = self._pose.process(rgb)
        if prediction.pose_landmarks is None:
            return Pose2D(
                source_id=frame.source_id,
                frame_index=frame.frame_index,
                timestamp_sec=frame.timestamp_sec,
                keypoints=[],
            )

        landmark_map = [
            ("nose", self._landmarks.NOSE),
            ("left_shoulder", self._landmarks.LEFT_SHOULDER),
            ("right_shoulder", self._landmarks.RIGHT_SHOULDER),
            ("left_elbow", self._landmarks.LEFT_ELBOW),
            ("right_elbow", self._landmarks.RIGHT_ELBOW),
            ("left_wrist", self._landmarks.LEFT_WRIST),
            ("right_wrist", self._landmarks.RIGHT_WRIST),
            ("left_hip", self._landmarks.LEFT_HIP),
            ("right_hip", self._landmarks.RIGHT_HIP),
            ("left_knee", self._landmarks.LEFT_KNEE),
            ("right_knee", self._landmarks.RIGHT_KNEE),
            ("left_ankle", self._landmarks.LEFT_ANKLE),
            ("right_ankle", self._landmarks.RIGHT_ANKLE),
        ]

        keypoints: list[Pose2DKeypoint] = []
        for name, landmark_id in landmark_map:
            lm = prediction.pose_landmarks.landmark[int(landmark_id)]
            keypoints.append(
                Pose2DKeypoint(
                    name=name,
                    x=_coerce_float(getattr(lm, "x", 0.0)),
                    y=_coerce_float(getattr(lm, "y", 0.0)),
                    confidence=_coerce_float(getattr(lm, "visibility", 0.0)),
                )
            )

        return Pose2D(
            source_id=frame.source_id,
            frame_index=frame.frame_index,
            timestamp_sec=frame.timestamp_sec,
            keypoints=keypoints,
        )

    def close(self) -> None:
        self._pose.close()


class MediaPipeTasksPoseDetector:
    """MediaPipe Tasks-based pose detector for newer mediapipe packages."""

    name = "mediapipe_tasks_pose"

    _LANDMARK_INDEX_BY_NAME = {
        "nose": 0,
        "left_shoulder": 11,
        "right_shoulder": 12,
        "left_elbow": 13,
        "right_elbow": 14,
        "left_wrist": 15,
        "right_wrist": 16,
        "left_hip": 23,
        "right_hip": 24,
        "left_knee": 25,
        "right_knee": 26,
        "left_ankle": 27,
        "right_ankle": 28,
    }

    def __init__(self, model_path: str | None = None) -> None:
        try:
            import mediapipe as mp  # type: ignore
            from mediapipe.tasks.python import vision  # type: ignore
            from mediapipe.tasks.python.core import base_options  # type: ignore
        except Exception as exc:
            raise RuntimeError("MediaPipe Tasks API is not available.") from exc

        self._mp = mp
        self._vision = vision
        self._base_options_module = base_options
        self._resolved_model_path = self._resolve_model_path(model_path)
        LOGGER.info("Using MediaPipe Tasks pose model: %s", self._resolved_model_path)
        self._landmarkers_by_source: dict[str, object] = {}
        self._last_timestamp_ms_by_source: dict[str, int] = {}

    def _build_landmarker(self):
        options = self._vision.PoseLandmarkerOptions(
            base_options=self._base_options_module.BaseOptions(model_asset_path=str(self._resolved_model_path)),
            running_mode=self._vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_segmentation_masks=False,
        )
        return self._vision.PoseLandmarker.create_from_options(options)

    def _resolve_model_path(self, requested_model_path: str | None) -> Path:
        candidates = self._candidate_model_paths(requested_model_path)
        for path in candidates:
            try:
                if path.exists() and path.is_file():
                    return path.resolve()
            except OSError:
                continue

        attempted_paths = [str(path) for path in candidates]
        download_target = self._download_target(requested_model_path)
        download_errors: list[str] = []

        for variant in self._preferred_variants(requested_model_path):
            target_path = download_target
            if target_path is None:
                target_path = APP_ROOT / "models" / f"pose_landmarker_{variant}.task"
            try:
                LOGGER.info("Downloading MediaPipe Tasks pose model '%s' to %s", variant, target_path)
                _download_to_path(TASK_MODEL_DOWNLOAD_URLS[variant], target_path)
                return target_path.resolve()
            except Exception as exc:
                download_errors.append(f"{variant} -> {target_path}: {exc}")

        attempted_message = ", ".join(f"'{path}'" for path in attempted_paths) or "no local paths"
        error_message = (
            "MediaPipe Tasks Pose model not found and automatic download failed. "
            f"Looked in: {attempted_message}. "
            "Set MOCAP_POSE_MODEL_PATH or place a .task model in the project 'models' folder."
        )
        if download_errors:
            error_message = f"{error_message} Download errors: {' | '.join(download_errors)}"
        raise RuntimeError(error_message)

    def _candidate_model_paths(self, requested_model_path: str | None) -> list[Path]:
        candidates: list[Path] = []
        if requested_model_path:
            candidates.extend(_expand_candidate_path(Path(requested_model_path)))
        env_path = os.environ.get("MOCAP_POSE_MODEL_PATH", "").strip()
        if env_path:
            candidates.extend(_expand_candidate_path(Path(env_path)))
        fallback_names = [
            "models/pose_landmarker_full.task",
            "models/pose_landmarker_lite.task",
            "models/pose_landmarker_heavy.task",
            "pose_landmarker_full.task",
            "pose_landmarker_lite.task",
        ]
        for fallback_name in fallback_names:
            candidates.extend(_expand_candidate_path(Path(fallback_name)))
        return _dedupe_paths(candidates)

    def _download_target(self, requested_model_path: str | None) -> Path | None:
        if requested_model_path:
            return _expand_candidate_path(Path(requested_model_path))[0]
        env_path = os.environ.get("MOCAP_POSE_MODEL_PATH", "").strip()
        if env_path:
            return _expand_candidate_path(Path(env_path))[0]
        return None

    def _preferred_variants(self, requested_model_path: str | None) -> list[str]:
        variant_candidates = [
            os.environ.get("MOCAP_POSE_MODEL_VARIANT", "").strip().lower(),
            _infer_model_variant(requested_model_path),
            _infer_model_variant(os.environ.get("MOCAP_POSE_MODEL_PATH", "").strip() or None),
            DEFAULT_TASK_MODEL_VARIANT,
        ]
        ordered: list[str] = []
        for variant in variant_candidates:
            if not variant or variant not in TASK_MODEL_DOWNLOAD_URLS:
                continue
            if variant not in ordered:
                ordered.append(variant)
        for variant in TASK_MODEL_DOWNLOAD_URLS:
            if variant not in ordered:
                ordered.append(variant)
        return ordered

    def _landmarker_for_source(self, source_id: str):
        landmarker = self._landmarkers_by_source.get(source_id)
        if landmarker is not None:
            return landmarker

        LOGGER.debug("Creating MediaPipe Tasks landmarker for source '%s'.", source_id)
        landmarker = self._build_landmarker()
        self._landmarkers_by_source[source_id] = landmarker
        return landmarker

    def detect(self, frame: FramePacket) -> Pose2D:
        rgb = cv2.cvtColor(frame.frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        landmarker = self._landmarker_for_source(frame.source_id)
        timestamp_ms = max(int(round(frame.timestamp_sec * 1000.0)), 0)
        last_timestamp_ms = self._last_timestamp_ms_by_source.get(frame.source_id, -1)
        if timestamp_ms <= last_timestamp_ms:
            timestamp_ms = last_timestamp_ms + 1
        self._last_timestamp_ms_by_source[frame.source_id] = timestamp_ms
        result = landmarker.detect_for_video(image, timestamp_ms)

        if not result.pose_landmarks:
            return Pose2D(
                source_id=frame.source_id,
                frame_index=frame.frame_index,
                timestamp_sec=frame.timestamp_sec,
                keypoints=[],
            )

        landmarks = result.pose_landmarks[0]
        keypoints: list[Pose2DKeypoint] = []
        for name, index in self._LANDMARK_INDEX_BY_NAME.items():
            if index >= len(landmarks):
                continue
            lm = landmarks[index]
            visibility = _coerce_float(getattr(lm, "visibility", 0.0))
            presence = _coerce_float(getattr(lm, "presence", visibility), fallback=visibility)
            confidence = max(visibility, presence)
            keypoints.append(
                Pose2DKeypoint(
                    name=name,
                    x=_coerce_float(getattr(lm, "x", 0.0)),
                    y=_coerce_float(getattr(lm, "y", 0.0)),
                    confidence=confidence,
                )
            )

        return Pose2D(
            source_id=frame.source_id,
            frame_index=frame.frame_index,
            timestamp_sec=frame.timestamp_sec,
            keypoints=keypoints,
        )

    def close(self) -> None:
        for landmarker in self._landmarkers_by_source.values():
            close = getattr(landmarker, "close", None)
            if callable(close):
                close()
        self._landmarkers_by_source.clear()
        self._last_timestamp_ms_by_source.clear()


def create_pose_detector(prefer_mediapipe: bool) -> PoseDetector:
    if prefer_mediapipe:
        try:
            detector = MediaPipePoseDetector()
            LOGGER.info("Using MediaPipe Solutions pose detector.")
            return detector
        except Exception as exc:
            LOGGER.warning(
                "MediaPipe Solutions detector unavailable (%s). Trying MediaPipe Tasks.",
                exc,
            )
            try:
                detector = MediaPipeTasksPoseDetector()
                LOGGER.info("Using MediaPipe Tasks pose detector.")
                return detector
            except Exception as tasks_exc:
                LOGGER.warning(
                    "MediaPipe Tasks detector unavailable (%s). Falling back to placeholder detector.",
                    tasks_exc,
                )
    return PlaceholderPoseDetector()
