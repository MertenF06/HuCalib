from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from mocap_app.ui.widgets.log_panel import LogPanelWidget
from mocap_app.ui.widgets.pipeline_status_panel import PipelineStatusPanelWidget


class DiagnosticsPanelWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._status_panel = PipelineStatusPanelWidget()
        self._log_panel = LogPanelWidget()

        split = QSplitter()
        split.setOrientation(Qt.Orientation.Vertical)
        split.addWidget(self._status_panel)
        split.addWidget(self._log_panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 260])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(split)

    def append_log_record(self, payload: dict[str, Any]) -> None:
        self._log_panel.append_record(payload)

    def update_pipeline_metrics(
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
