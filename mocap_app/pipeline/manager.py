from __future__ import annotations

import logging
import time

from mocap_app.models.types import CalibrationBundle, FramePacket, PipelineDebugInfo, PipelineResult, Pose2D
from mocap_app.pipeline.detection import PoseDetector
from mocap_app.pipeline.filtering import ExponentialPoseSmoother
from mocap_app.pipeline.matching import CrossCameraMatcher
from mocap_app.pipeline.triangulation import CalibratedTriangulator


LOGGER = logging.getLogger(__name__)


class MocapPipeline:
    def __init__(
        self,
        detector: PoseDetector,
        matcher: CrossCameraMatcher | None = None,
        triangulator: CalibratedTriangulator | None = None,
        smoother: ExponentialPoseSmoother | None = None,
    ) -> None:
        self._detector = detector
        self._matcher = matcher or CrossCameraMatcher()
        self._triangulator = triangulator or CalibratedTriangulator(use_fallback_when_unavailable=False)
        self._smoother = smoother or ExponentialPoseSmoother()

    @property
    def detector_name(self) -> str:
        return self._detector.name

    @property
    def triangulator_name(self) -> str:
        return self._triangulator.name

    def update_calibration(self, bundle: CalibrationBundle | None) -> None:
        self._triangulator.set_calibration(bundle)

    def process(
        self,
        frames: dict[str, FramePacket],
        run_detection: bool = True,
    ) -> PipelineResult:
        if not frames:
            raise ValueError("Cannot process an empty frame batch.")

        t0 = time.perf_counter()
        detection_ms = 0.0
        matching_ms = 0.0
        triangulation_ms = 0.0
        smoothing_ms = 0.0
        processing_notes: list[str] = []

        poses_2d: dict[str, Pose2D] = {}
        matched_keypoints = {}
        tri_result = None

        if run_detection:
            td0 = time.perf_counter()
            for source_id, frame in frames.items():
                try:
                    poses_2d[source_id] = self._detector.detect(frame)
                except Exception as exc:
                    LOGGER.exception("2D detection failed for %s.", source_id)
                    poses_2d[source_id] = self._empty_pose_from_frame(frame)
                    processing_notes.append(f"{source_id}: detector failed ({exc}).")
            detection_ms = (time.perf_counter() - td0) * 1000.0

            tm0 = time.perf_counter()
            matched_keypoints = self._matcher.match(poses_2d)
            matching_ms = (time.perf_counter() - tm0) * 1000.0
        else:
            poses_2d = {source_id: self._empty_pose_from_frame(frame) for source_id, frame in frames.items()}

        frame_index = max(frame.frame_index for frame in frames.values())
        timestamp_sec = max(frame.timestamp_sec for frame in frames.values())

        if run_detection:
            tt0 = time.perf_counter()
            try:
                tri_result = self._triangulator.triangulate(
                    matched_keypoints=matched_keypoints,
                    frames=frames,
                    frame_index=frame_index,
                    timestamp_sec=timestamp_sec,
                )
            except Exception as exc:
                LOGGER.exception("Triangulation stage failed.")
                tri_result = None
                processing_notes.append(f"Triangulation failed ({exc}).")
            triangulation_ms = (time.perf_counter() - tt0) * 1000.0

        pose_3d = tri_result.pose_3d if tri_result is not None else None
        if pose_3d is not None:
            ts0 = time.perf_counter()
            pose_3d = self._smoother.apply(pose_3d)
            smoothing_ms = (time.perf_counter() - ts0) * 1000.0

        notes = list(tri_result.notes) if tri_result is not None else []
        notes.extend(processing_notes)
        if not run_detection:
            notes.append("Detection disabled in current workspace mode.")
            notes.append("Triangulation skipped because detection is disabled.")
        if len(frames) < 2:
            notes.append("Only one camera active: calibrated multi-view triangulation unavailable.")
        if pose_3d is None:
            notes.append("No valid 3D pose reconstructed for this frame.")
        if self._detector.name == "placeholder_pose":
            notes.append("2D detector is placeholder_pose (debug only, not physically trustworthy).")

        pipeline_ms = (time.perf_counter() - t0) * 1000.0
        reconstruction_mode = "disabled"
        if run_detection:
            reconstruction_mode = tri_result.mode if tri_result is not None else "unavailable"

        debug = PipelineDebugInfo(
            detector_name=self._detector.name,
            triangulator_name=self._triangulator.name,
            active_cameras=len(frames),
            matched_keypoints=len(matched_keypoints),
            reconstruction_mode=reconstruction_mode,
            reconstructed_keypoints=tri_result.reconstructed_joints if tri_result is not None else 0,
            mean_reprojection_error_px=tri_result.mean_reprojection_error_px if tri_result is not None else None,
            per_joint_reprojection_error_px=tri_result.per_joint_error_px if tri_result is not None else {},
            detection_ms=detection_ms,
            matching_ms=matching_ms,
            triangulation_ms=triangulation_ms,
            smoothing_ms=smoothing_ms,
            pipeline_ms=pipeline_ms,
            notes=notes,
        )

        return PipelineResult(
            frame_index=frame_index,
            timestamp_sec=timestamp_sec,
            frames=frames,
            poses_2d=poses_2d,
            pose_3d=pose_3d,
            reprojected_keypoints_px=tri_result.reprojected_points_px if tri_result is not None else {},
            debug=debug,
        )

    def shutdown(self) -> None:
        close = getattr(self._detector, "close", None)
        if callable(close):
            close()
        self._smoother.reset()

    def _empty_pose_from_frame(self, frame: FramePacket) -> Pose2D:
        return Pose2D(
            source_id=frame.source_id,
            frame_index=frame.frame_index,
            timestamp_sec=frame.timestamp_sec,
            keypoints=[],
        )
