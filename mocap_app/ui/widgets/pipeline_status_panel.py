from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget


class PipelineStatusPanelWidget(QWidget):
    _METRIC_SPECS = [
        ("cameras_active", "Cameras Active", "0"),
        ("detector_active", "Detector Active", "placeholder_pose"),
        ("calibration_loaded", "Calibration Loaded", "No"),
        ("triangulator_engine", "Triangulator", "calibrated_multiview_triangulator"),
        ("reconstruction_mode", "Reconstruction Mode", "unavailable"),
        ("matched_keypoints", "Matched Keypoints", "0"),
        ("reconstructed_keypoints", "Reconstructed Joints", "0"),
        ("mean_reprojection_error_px", "Mean Reproj Error", "n/a"),
        ("triangulation_status", "Triangulation", "Idle"),
        ("fps", "Current FPS", "0.0"),
        ("capture_latency_ms", "Capture Latency", "n/a"),
        ("detection_ms", "Detection Time", "0.0 ms"),
        ("matching_ms", "Matching Time", "0.0 ms"),
        ("triangulation_ms", "Triangulation Time", "0.0 ms"),
        ("smoothing_ms", "Smoothing Time", "0.0 ms"),
        ("pipeline_ms", "Pipeline Total", "0.0 ms"),
        ("overlay_ms", "Overlay Time", "0.0 ms"),
        ("display_ms", "Qt Display Time", "0.0 ms"),
        ("per_camera_fps", "Per-Camera FPS", "-"),
        ("dropped_input_batches", "Dropped Input Batches", "0"),
    ]
    _SUMMARY_KEYS = {
        "cameras_active",
        "detector_active",
        "calibration_loaded",
        "triangulator_engine",
        "reconstruction_mode",
        "triangulation_status",
        "matched_keypoints",
        "reconstructed_keypoints",
        "mean_reprojection_error_px",
        "fps",
        "capture_latency_ms",
        "per_camera_fps",
        "dropped_input_batches",
    }

    def __init__(self, mode: str = "full") -> None:
        super().__init__()
        self._mode = mode
        self._labels: dict[str, QLabel] = {}

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            """
            QFrame {
                background-color: #ffffff;
                border: 1px solid #d4dde8;
                border-radius: 4px;
            }
            QLabel {
                color: #1f2937;
            }
            """
        )

        form = QFormLayout(frame)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        for key, title, default in self._iter_metric_specs():
            value_label = QLabel(default)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setMinimumWidth(220 if self._mode == "full" else 260)
            self._labels[key] = value_label
            form.addRow(f"{title}:", value_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(frame)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _iter_metric_specs(self) -> list[tuple[str, str, str]]:
        if self._mode != "summary":
            return list(self._METRIC_SPECS)
        return [spec for spec in self._METRIC_SPECS if spec[0] in self._SUMMARY_KEYS]

    def update_metrics(
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
        self._set_label("cameras_active", str(cameras_active))
        self._set_label("detector_active", detector_active)
        self._set_label("calibration_loaded", "Yes" if calibration_loaded else "No")
        self._set_label("triangulator_engine", triangulator_engine)
        self._set_label("reconstruction_mode", reconstruction_mode)
        self._set_label("matched_keypoints", str(matched_keypoints))
        self._set_label("reconstructed_keypoints", str(reconstructed_keypoints))
        self._set_label(
            "mean_reprojection_error_px",
            f"{mean_reprojection_error_px:.3f}px"
            if mean_reprojection_error_px is not None
            else "n/a"
        )
        self._set_label("triangulation_status", triangulation_status)
        self._set_label("fps", f"{fps:.1f}")
        self._set_label(
            "capture_latency_ms",
            f"{capture_latency_ms:.1f} ms" if capture_latency_ms is not None else "n/a"
        )
        self._set_label("detection_ms", f"{detection_ms:.1f} ms")
        self._set_label("matching_ms", f"{matching_ms:.1f} ms")
        self._set_label("triangulation_ms", f"{triangulation_ms:.1f} ms")
        self._set_label("smoothing_ms", f"{smoothing_ms:.1f} ms")
        self._set_label("pipeline_ms", f"{pipeline_ms:.1f} ms")
        self._set_label("overlay_ms", f"{overlay_ms:.1f} ms")
        self._set_label("display_ms", f"{display_ms:.1f} ms")
        fps_map = per_camera_fps or {}
        if fps_map:
            fps_text = ", ".join(f"{source_id}:{value:.1f}" for source_id, value in sorted(fps_map.items()))
            self._set_label("per_camera_fps", fps_text)
        else:
            self._set_label("per_camera_fps", "-")
        self._set_label("dropped_input_batches", str(max(0, dropped_input_batches)))

    def _set_label(self, key: str, value: str) -> None:
        label = self._labels.get(key)
        if label is not None:
            label.setText(value)
