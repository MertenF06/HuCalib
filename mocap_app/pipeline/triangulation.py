from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from mocap_app.models.types import CalibrationBundle, CameraCalibration, FramePacket, Pose2DKeypoint, Pose3D, Pose3DKeypoint


LOGGER = logging.getLogger(__name__)


F64Array = NDArray[np.float64]


@dataclass(slots=True)
class TriangulationObservation:
    source_id: str
    keypoint_name: str
    point_px: tuple[float, float]
    confidence: float


@dataclass(slots=True)
class CameraProjection:
    source_id: str
    intrinsics: F64Array
    distortion: F64Array
    rotation_matrix: F64Array
    translation_vec: F64Array
    projection_matrix: F64Array
    image_size: tuple[int, int]
    camera_center_world: F64Array


@dataclass(slots=True)
class TriangulationResult:
    pose_3d: Pose3D | None
    reprojected_points_px: dict[str, dict[str, tuple[float, float]]] = field(default_factory=dict)
    per_joint_error_px: dict[str, float] = field(default_factory=dict)
    mode: str = "unavailable"
    reconstructed_joints: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def mean_reprojection_error_px(self) -> float | None:
        if not self.per_joint_error_px:
            return None
        return float(np.mean(np.array(list(self.per_joint_error_px.values()), dtype=np.float64)))


class TriangulationRefiner(Protocol):
    """Hook interface for later non-linear triangulation refinement."""

    def refine(
        self,
        initial_point_world: F64Array,
        observations: list[TriangulationObservation],
        cameras: dict[str, CameraProjection],
    ) -> F64Array:
        ...


class IdentityRefiner:
    """No-op refiner placeholder used before bundle-adjustment integration."""

    def refine(
        self,
        initial_point_world: F64Array,
        observations: list[TriangulationObservation],
        cameras: dict[str, CameraProjection],
    ) -> F64Array:
        return initial_point_world


class _PlaceholderDepthFallback:
    """Simple deterministic fallback used only when calibrated triangulation is unavailable."""

    def triangulate(
        self,
        matched_keypoints: dict[str, dict[str, Pose2DKeypoint]],
        frame_index: int,
        timestamp_sec: float,
    ) -> TriangulationResult:
        keypoints_3d: list[Pose3DKeypoint] = []
        notes: list[str] = []

        for keypoint_name, observations in matched_keypoints.items():
            if not observations:
                continue

            xs = np.array([observation.x for observation in observations.values()], dtype=np.float64)
            ys = np.array([observation.y for observation in observations.values()], dtype=np.float64)
            confidences = np.array([observation.confidence for observation in observations.values()], dtype=np.float64)

            mean_x = float(np.mean(xs))
            mean_y = float(np.mean(ys))
            disparity = float(np.max(xs) - np.min(xs)) if len(xs) > 1 else 0.0
            depth = float(np.clip(1.4 / (disparity + 0.03), 0.25, 4.5))
            confidence = float(np.clip(np.mean(confidences), 0.0, 1.0))

            keypoints_3d.append(
                Pose3DKeypoint(
                    name=keypoint_name,
                    x=(mean_x - 0.5) * 2.0,
                    y=(0.5 - mean_y) * 2.0,
                    z=depth,
                    confidence=confidence,
                )
            )

        if not keypoints_3d:
            notes.append("Fallback mode produced no joints.")
            return TriangulationResult(
                pose_3d=None,
                mode="placeholder_fallback",
                reconstructed_joints=0,
                notes=notes,
            )

        notes.append("Using placeholder fallback because calibrated triangulation is unavailable.")
        return TriangulationResult(
            pose_3d=Pose3D(frame_index=frame_index, timestamp_sec=timestamp_sec, keypoints=keypoints_3d),
            mode="placeholder_fallback",
            reconstructed_joints=len(keypoints_3d),
            notes=notes,
        )


class CalibratedTriangulator:
    """Per-frame calibrated triangulation using camera projection matrices.

    Assumes camera extrinsics represent world-to-camera transforms:
    X_camera = R * X_world + t
    """

    name = "calibrated_multiview_triangulator"

    def __init__(
        self,
        min_views: int = 2,
        min_keypoint_confidence: float = 0.35,
        max_joint_reprojection_error_px: float = 25.0,
        use_fallback_when_unavailable: bool = False,
        refiner: TriangulationRefiner | None = None,
    ) -> None:
        self._min_views = max(2, min_views)
        self._min_keypoint_confidence = min_keypoint_confidence
        self._max_joint_reprojection_error_px = max_joint_reprojection_error_px
        self._use_fallback = use_fallback_when_unavailable
        self._refiner = refiner or IdentityRefiner()
        self._calibration_bundle: CalibrationBundle | None = None
        self._fallback = _PlaceholderDepthFallback()
        self._last_setup_warning_key = ""

    def set_calibration(self, bundle: CalibrationBundle | None) -> None:
        self._calibration_bundle = bundle

    def triangulate(
        self,
        matched_keypoints: dict[str, dict[str, Pose2DKeypoint]],
        frames: dict[str, FramePacket],
        frame_index: int,
        timestamp_sec: float,
    ) -> TriangulationResult:
        cameras, setup_notes = self._build_camera_projection_map(frames=frames)
        if len(cameras) < self._min_views:
            notes = list(setup_notes)
            notes.append(
                f"Calibrated triangulation requires >= {self._min_views} calibrated cameras with extrinsics."
            )
            self._log_setup_once(notes)
            if self._use_fallback:
                fallback = self._fallback.triangulate(
                    matched_keypoints=matched_keypoints,
                    frame_index=frame_index,
                    timestamp_sec=timestamp_sec,
                )
                fallback.notes = notes + fallback.notes
                return fallback
            return TriangulationResult(
                pose_3d=None,
                mode="unavailable",
                reconstructed_joints=0,
                notes=notes,
            )

        keypoints_3d: list[Pose3DKeypoint] = []
        reprojected_points_px: dict[str, dict[str, tuple[float, float]]] = {}
        per_joint_error_px: dict[str, float] = {}
        notes = list(setup_notes)

        for keypoint_name, observations_map in matched_keypoints.items():
            observations = self._build_observations(
                keypoint_name=keypoint_name,
                observations_map=observations_map,
                frames=frames,
                cameras=cameras,
            )
            if len(observations) < self._min_views:
                reason = (
                    f"Skipped joint '{keypoint_name}': only {len(observations)} valid view(s) "
                    f"(need >= {self._min_views})."
                )
                LOGGER.debug(reason)
                notes.append(reason)
                continue

            triangulated = self._triangulate_joint(
                keypoint_name=keypoint_name,
                observations=observations,
                cameras=cameras,
            )
            if triangulated is None:
                reason = f"Skipped joint '{keypoint_name}': triangulation failed."
                LOGGER.debug(reason)
                notes.append(reason)
                continue

            point_world, reproj_map, weighted_error = triangulated
            if not np.all(np.isfinite(point_world)):
                reason = f"Skipped joint '{keypoint_name}': non-finite 3D point."
                LOGGER.debug(reason)
                notes.append(reason)
                continue

            if weighted_error > self._max_joint_reprojection_error_px:
                reason = (
                    f"Skipped joint '{keypoint_name}': reprojection error {weighted_error:.2f}px "
                    f"> {self._max_joint_reprojection_error_px:.2f}px."
                )
                LOGGER.debug(reason)
                notes.append(reason)
                continue

            mean_conf = float(np.mean(np.array([obs.confidence for obs in observations], dtype=np.float64)))
            confidence_from_error = math.exp(-weighted_error / max(self._max_joint_reprojection_error_px, 1.0))
            confidence = float(np.clip(mean_conf * confidence_from_error, 0.0, 1.0))
            point_components = np.asarray(point_world, dtype=np.float64).reshape(-1)

            keypoints_3d.append(
                Pose3DKeypoint(
                    name=keypoint_name,
                    x=float(point_components[0]),
                    y=float(point_components[1]),
                    z=float(point_components[2]),
                    confidence=confidence,
                )
            )
            per_joint_error_px[keypoint_name] = float(weighted_error)

            for source_id, point_px in reproj_map.items():
                reprojected_points_px.setdefault(source_id, {})[keypoint_name] = point_px

        if not keypoints_3d:
            notes.append("No joints reconstructed with calibrated triangulation.")
            if self._use_fallback:
                fallback = self._fallback.triangulate(
                    matched_keypoints=matched_keypoints,
                    frame_index=frame_index,
                    timestamp_sec=timestamp_sec,
                )
                fallback.notes = notes + fallback.notes
                return fallback

            return TriangulationResult(
                pose_3d=None,
                reprojected_points_px=reprojected_points_px,
                per_joint_error_px=per_joint_error_px,
                mode="real_calibrated",
                reconstructed_joints=0,
                notes=notes,
            )

        pose = Pose3D(frame_index=frame_index, timestamp_sec=timestamp_sec, keypoints=keypoints_3d)
        return TriangulationResult(
            pose_3d=pose,
            reprojected_points_px=reprojected_points_px,
            per_joint_error_px=per_joint_error_px,
            mode="real_calibrated",
            reconstructed_joints=len(keypoints_3d),
            notes=notes,
        )

    def _build_camera_projection_map(
        self,
        frames: dict[str, FramePacket],
    ) -> tuple[dict[str, CameraProjection], list[str]]:
        cameras: dict[str, CameraProjection] = {}
        notes: list[str] = []
        bundle = self._calibration_bundle

        if bundle is None:
            notes.append("No calibration bundle loaded.")
            return cameras, notes

        metadata_extrinsics = bundle.metadata.get("extrinsics", {})
        for source_id, frame in frames.items():
            camera = bundle.cameras.get(source_id)
            if camera is None:
                notes.append(f"{source_id}: calibration entry missing.")
                continue

            projection = self._camera_projection_from_calibration(
                source_id=source_id,
                camera=camera,
                frame=frame,
                metadata_extrinsics=metadata_extrinsics if isinstance(metadata_extrinsics, dict) else {},
            )
            if projection is None:
                notes.append(f"{source_id}: missing intrinsics/extrinsics for real triangulation.")
                continue
            cameras[source_id] = projection

        return cameras, notes

    def _camera_projection_from_calibration(
        self,
        source_id: str,
        camera: CameraCalibration,
        frame: FramePacket,
        metadata_extrinsics: dict[str, object],
    ) -> CameraProjection | None:
        if camera.intrinsics is None or camera.distortion is None:
            return None

        rotation_values, translation_values = self._resolve_extrinsics_values(
            source_id=source_id,
            camera=camera,
            metadata_extrinsics=metadata_extrinsics,
        )
        rotation_matrix = self._resolve_rotation_matrix(rotation_values)
        translation_vec = self._resolve_translation_vector(translation_values)
        if rotation_matrix is None or translation_vec is None:
            return None

        frame_height, frame_width = frame.frame_bgr.shape[:2]
        frame_size = (frame_width, frame_height)
        intrinsics = np.array(camera.intrinsics, dtype=np.float64)
        if intrinsics.shape != (3, 3):
            return None

        if camera.image_size and camera.image_size != frame_size:
            intrinsics = self._scale_intrinsics(
                intrinsics=intrinsics,
                from_size=camera.image_size,
                to_size=frame_size,
            )

        distortion = np.array(camera.distortion, dtype=np.float64).reshape(-1, 1)
        projection_matrix = np.hstack([rotation_matrix, translation_vec])
        camera_center_world = -rotation_matrix.T @ translation_vec

        return CameraProjection(
            source_id=source_id,
            intrinsics=intrinsics,
            distortion=distortion,
            rotation_matrix=rotation_matrix,
            translation_vec=translation_vec,
            projection_matrix=projection_matrix,
            image_size=frame_size,
            camera_center_world=camera_center_world,
        )

    def _resolve_extrinsics_values(
        self,
        source_id: str,
        camera: CameraCalibration,
        metadata_extrinsics: dict[str, object],
    ) -> tuple[list[float] | None, list[float] | None]:
        rotation = camera.rotation
        translation = camera.translation
        if rotation is not None and translation is not None:
            return rotation, translation

        raw = metadata_extrinsics.get(source_id)
        if not isinstance(raw, dict):
            return rotation, translation
        raw_rotation = raw.get("rotation")
        raw_translation = raw.get("translation")
        if isinstance(raw_rotation, list) and isinstance(raw_translation, list):
            return raw_rotation, raw_translation
        return rotation, translation

    def _resolve_rotation_matrix(self, rotation: list[float] | None) -> F64Array | None:
        if rotation is None:
            return None
        values = np.array(rotation, dtype=np.float64).flatten()
        if values.size == 3:
            matrix, _ = cv2.Rodrigues(values.reshape(3, 1))
            return matrix
        if values.size == 9:
            return values.reshape(3, 3)
        return None

    def _resolve_translation_vector(self, translation: list[float] | None) -> F64Array | None:
        if translation is None:
            return None
        values = np.array(translation, dtype=np.float64).flatten()
        if values.size < 3:
            return None
        return values[:3].reshape(3, 1)

    def _scale_intrinsics(
        self,
        intrinsics: F64Array,
        from_size: tuple[int, int],
        to_size: tuple[int, int],
    ) -> F64Array:
        from_w, from_h = from_size
        to_w, to_h = to_size
        if from_w <= 0 or from_h <= 0:
            return intrinsics
        scale_x = to_w / from_w
        scale_y = to_h / from_h
        scaled = intrinsics.copy()
        scaled[0, 0] *= scale_x
        scaled[1, 1] *= scale_y
        scaled[0, 2] *= scale_x
        scaled[1, 2] *= scale_y
        return scaled

    def _build_observations(
        self,
        keypoint_name: str,
        observations_map: dict[str, Pose2DKeypoint],
        frames: dict[str, FramePacket],
        cameras: dict[str, CameraProjection],
    ) -> list[TriangulationObservation]:
        observations: list[TriangulationObservation] = []
        for source_id, keypoint in observations_map.items():
            if keypoint.confidence < self._min_keypoint_confidence:
                LOGGER.debug(
                    "Skipping keypoint %s on %s due to low confidence %.2f",
                    keypoint_name,
                    source_id,
                    keypoint.confidence,
                )
                continue

            frame = frames.get(source_id)
            camera = cameras.get(source_id)
            if frame is None or camera is None:
                continue
            height, width = frame.frame_bgr.shape[:2]
            point_px = (float(keypoint.x * width), float(keypoint.y * height))
            observations.append(
                TriangulationObservation(
                    source_id=source_id,
                    keypoint_name=keypoint_name,
                    point_px=point_px,
                    confidence=float(keypoint.confidence),
                )
            )
        return observations

    def _triangulate_joint(
        self,
        keypoint_name: str,
        observations: list[TriangulationObservation],
        cameras: dict[str, CameraProjection],
    ) -> tuple[F64Array, dict[str, tuple[float, float]], float] | None:
        best_point: F64Array | None = None
        best_error = float("inf")
        best_reproj: dict[str, tuple[float, float]] = {}

        for obs_a, obs_b in combinations(observations, 2):
            camera_a = cameras[obs_a.source_id]
            camera_b = cameras[obs_b.source_id]

            # Skip near-degenerate camera pairs with tiny baseline.
            baseline = float(np.linalg.norm(camera_a.camera_center_world - camera_b.camera_center_world))
            if baseline < 1e-4:
                LOGGER.debug(
                    "Skipping pair (%s,%s) for %s due to tiny baseline %.6f",
                    obs_a.source_id,
                    obs_b.source_id,
                    keypoint_name,
                    baseline,
                )
                continue

            point_world = self._triangulate_two_view(obs_a, camera_a, obs_b, camera_b)
            if point_world is None:
                continue
            refined_world = self._refiner.refine(point_world, observations, cameras)
            reproj_map, weighted_error = self._compute_reprojection_errors(
                point_world=refined_world,
                observations=observations,
                cameras=cameras,
            )
            if weighted_error < best_error:
                best_error = weighted_error
                best_point = refined_world
                best_reproj = reproj_map

        if best_point is None:
            return None
        return best_point, best_reproj, best_error

    def _triangulate_two_view(
        self,
        obs_a: TriangulationObservation,
        camera_a: CameraProjection,
        obs_b: TriangulationObservation,
        camera_b: CameraProjection,
    ) -> F64Array | None:
        p_a = np.array([[obs_a.point_px]], dtype=np.float64)
        p_b = np.array([[obs_b.point_px]], dtype=np.float64)

        undist_a = cv2.undistortPoints(p_a, camera_a.intrinsics, camera_a.distortion)
        undist_b = cv2.undistortPoints(p_b, camera_b.intrinsics, camera_b.distortion)

        proj_a = camera_a.projection_matrix
        proj_b = camera_b.projection_matrix

        # cv2.triangulatePoints expects 2xN point arrays.
        x_h = cv2.triangulatePoints(
            proj_a,
            proj_b,
            undist_a.reshape(1, 2).T,
            undist_b.reshape(1, 2).T,
        )
        w = float(x_h[3, 0])
        if abs(w) < 1e-9:
            return None
        point_world = (x_h[:3, 0] / w).reshape(3, 1)
        return point_world

    def _compute_reprojection_errors(
        self,
        point_world: F64Array,
        observations: list[TriangulationObservation],
        cameras: dict[str, CameraProjection],
    ) -> tuple[dict[str, tuple[float, float]], float]:
        reprojections: dict[str, tuple[float, float]] = {}
        weighted_error_sum = 0.0
        weight_sum = 0.0

        point_vector = point_world.reshape(3, 1)
        for obs in observations:
            camera = cameras[obs.source_id]
            projected, _ = cv2.projectPoints(
                point_vector.reshape(1, 3),
                cv2.Rodrigues(camera.rotation_matrix)[0],
                camera.translation_vec,
                camera.intrinsics,
                camera.distortion,
            )
            px = float(projected[0, 0, 0])
            py = float(projected[0, 0, 1])
            reprojections[obs.source_id] = (px, py)
            error = float(np.linalg.norm(np.array([px - obs.point_px[0], py - obs.point_px[1]], dtype=np.float64)))
            weighted_error_sum += error * obs.confidence
            weight_sum += obs.confidence

        weighted_error = weighted_error_sum / max(weight_sum, 1e-8)
        return reprojections, weighted_error

    def _log_setup_once(self, notes: list[str]) -> None:
        key = "|".join(notes[:4])
        if key and key != self._last_setup_warning_key:
            LOGGER.info("Triangulation setup warning: %s", "; ".join(notes))
            self._last_setup_warning_key = key
