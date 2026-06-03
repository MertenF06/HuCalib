from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from mocap_app.models.types import FramePacket, Pose2D, Pose3D
from mocap_app.ui.widgets.camera_grid import CameraGridWidget
from mocap_app.ui.widgets.pipeline_status_panel import PipelineStatusPanelWidget
from mocap_app.ui.widgets.viewer3d import Pose3DViewerWidget


class ReconstructionWorkspaceWidget(QWidget):
    """Reconstruction workspace focused on 3D solving and reprojection debugging."""

    def __init__(self) -> None:
        super().__init__()
        self._camera_grid = CameraGridWidget()
        self._viewer = Pose3DViewerWidget()
        self._status_panel = PipelineStatusPanelWidget(mode="summary")

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self._viewer)
        right_split.addWidget(self._status_panel)
        right_split.setStretchFactor(0, 6)
        right_split.setStretchFactor(1, 4)
        right_split.setChildrenCollapsible(False)
        right_split.setSizes([700, 360])

        root_split = QSplitter(Qt.Orientation.Horizontal)
        root_split.addWidget(self._camera_grid)
        root_split.addWidget(right_split)
        root_split.setStretchFactor(0, 6)
        root_split.setStretchFactor(1, 4)
        root_split.setChildrenCollapsible(False)
        root_split.setSizes([1180, 760])

        self._camera_grid.setMinimumWidth(760)
        right_split.setMinimumWidth(520)
        self._viewer.setMinimumHeight(420)
        self._status_panel.setMinimumHeight(280)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(root_split)

    def set_sources(self, source_ids: list[str]) -> None:
        self._camera_grid.set_sources(source_ids)

    def update_visuals(
        self,
        frames: dict[str, FramePacket],
        poses_2d: dict[str, Pose2D],
        pose_3d: Pose3D | None,
        reprojected_points_px: dict[str, dict[str, tuple[float, float]]],
    ) -> None:
        self._camera_grid.update_batch(
            frames=frames,
            poses_2d=poses_2d,
            reprojected_points_px=reprojected_points_px,
        )
        self._viewer.set_pose(pose_3d)

    def set_reconstruction_metadata(
        self,
        mode: str,
        reconstructed_joints: int,
        mean_reprojection_error_px: float | None,
        triangulation_status: str = "Idle",
    ) -> None:
        self._viewer.set_reconstruction_metadata(
            mode=mode,
            reconstructed_joints=reconstructed_joints,
            mean_reprojection_error_px=mean_reprojection_error_px,
            triangulation_status=triangulation_status,
        )

    def update_status_panel(
        self,
        cameras_active: int,
        detector_active: str,
        calibration_loaded: bool,
        triangulator_engine: str,
        reconstruction_mode: str,
        matched_keypoints: int,
        reconstructed_keypoints: int,
        mean_reprojection_error_px: float | None,
        triangulation_status: str,
        fps: float,
        capture_latency_ms: float | None = None,
        detection_ms: float = 0.0,
        matching_ms: float = 0.0,
        triangulation_ms: float = 0.0,
        smoothing_ms: float = 0.0,
        pipeline_ms: float = 0.0,
        overlay_ms: float = 0.0,
        display_ms: float = 0.0,
        per_camera_fps: dict[str, float] | None = None,
        dropped_input_batches: int = 0,
    ) -> None:
        self._status_panel.update_metrics(
            cameras_active=cameras_active,
            detector_active=detector_active,
            calibration_loaded=calibration_loaded,
            triangulator_engine=triangulator_engine,
            reconstruction_mode=reconstruction_mode,
            matched_keypoints=matched_keypoints,
            reconstructed_keypoints=reconstructed_keypoints,
            mean_reprojection_error_px=mean_reprojection_error_px,
            triangulation_status=triangulation_status,
            fps=fps,
            capture_latency_ms=capture_latency_ms,
            detection_ms=detection_ms,
            matching_ms=matching_ms,
            triangulation_ms=triangulation_ms,
            smoothing_ms=smoothing_ms,
            pipeline_ms=pipeline_ms,
            overlay_ms=overlay_ms,
            display_ms=display_ms,
            per_camera_fps=per_camera_fps,
            dropped_input_batches=dropped_input_batches,
        )
