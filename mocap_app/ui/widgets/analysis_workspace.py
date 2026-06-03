from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mocap_app.analysis.session_analysis import MetricSeries, SessionAnalysisReport


class MetricPlotWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._series: MetricSeries | None = None
        self._current_frame = 0
        self.setMinimumHeight(260)

    def set_series(self, series: MetricSeries | None) -> None:
        self._series = series
        self.update()

    def set_current_frame(self, frame_index: int) -> None:
        self._current_frame = max(0, frame_index)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        if self._series is None:
            painter.setPen(QColor("#475569"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a session with stored 3D pose data")
            return

        numeric_values = [value for value in self._series.values if value is not None]
        if not numeric_values:
            painter.setPen(QColor("#475569"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No numeric values available for this metric")
            return

        left = 58
        right = self.width() - 18
        top = 32
        bottom = self.height() - 34
        plot_width = max(1, right - left)
        plot_height = max(1, bottom - top)
        min_value = min(numeric_values)
        max_value = max(numeric_values)
        if abs(max_value - min_value) < 1e-6:
            max_value += 0.5
            min_value -= 0.5

        painter.setPen(QPen(QColor("#d8e2ee"), 1))
        painter.drawRect(left, top, plot_width, plot_height)
        for fraction in [0.25, 0.5, 0.75]:
            y = top + plot_height * fraction
            painter.drawLine(left, int(y), right, int(y))

        painter.setPen(QColor("#1f2937"))
        painter.drawText(14, 20, self._series.label)
        painter.drawText(14, top + 8, f"{max_value:.2f}")
        painter.drawText(14, bottom, f"{min_value:.2f}")
        painter.drawText(right - 90, self.height() - 10, self._series.unit)

        painter.setPen(QPen(QColor("#0f766e"), 2))
        previous_point: QPointF | None = None
        frame_count = max(len(self._series.values) - 1, 1)
        value_range = max(max_value - min_value, 1e-6)
        for index, value in enumerate(self._series.values):
            if value is None:
                previous_point = None
                continue
            x = left + (index / frame_count) * plot_width
            y = bottom - ((value - min_value) / value_range) * plot_height
            point = QPointF(x, y)
            if previous_point is not None:
                painter.drawLine(previous_point, point)
            previous_point = point

        marker_x = left + (min(self._current_frame, frame_count) / frame_count) * plot_width
        painter.setPen(QPen(QColor("#dc2626"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(marker_x, top), QPointF(marker_x, bottom))


class AnalysisWorkspaceWidget(QWidget):
    """Session analysis workspace with playback, summary and report export."""

    load_session_requested = Signal()
    play_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    step_backward_requested = Signal()
    step_forward_requested = Signal()
    seek_requested = Signal(int)
    export_report_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._user_scrubbing = False
        self._total_frames = 0
        self._report: SessionAnalysisReport | None = None

        self._load_button = QPushButton("Load Session")
        self._play_button = QPushButton("Play")
        self._pause_button = QPushButton("Pause")
        self._stop_button = QPushButton("Stop")
        self._step_back_button = QPushButton("Step -1")
        self._step_fwd_button = QPushButton("Step +1")
        self._export_button = QPushButton("Export Report")
        self._loop_checkbox = QCheckBox("Loop Playback")
        self._export_button.setEnabled(False)

        for button in [
            self._load_button,
            self._play_button,
            self._pause_button,
            self._stop_button,
            self._step_back_button,
            self._step_fwd_button,
            self._export_button,
        ]:
            button.setMinimumHeight(34)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self._load_button)
        controls.addWidget(self._play_button)
        controls.addWidget(self._pause_button)
        controls.addWidget(self._stop_button)
        controls.addWidget(self._step_back_button)
        controls.addWidget(self._step_fwd_button)
        controls.addWidget(self._export_button)
        controls.addWidget(self._loop_checkbox)
        controls.addStretch(1)

        self._timeline_label = QLabel("Frame 0 / 0")
        self._timeline_label.setStyleSheet("color: #1f2937;")
        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setEnabled(False)

        timeline_card = QFrame()
        timeline_card.setFrameShape(QFrame.Shape.StyledPanel)
        timeline_layout = QVBoxLayout(timeline_card)
        timeline_layout.setContentsMargins(10, 8, 10, 8)
        timeline_layout.setSpacing(6)
        timeline_layout.addWidget(self._timeline_label)
        timeline_layout.addWidget(self._timeline)

        self._summary_label = QLabel(
            "Analysis Summary\n\nLoad a session to compute kinematic summaries and a report."
        )
        self._summary_label.setWordWrap(True)
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._summary_label.setStyleSheet("color: #1f2937;")

        summary_card = QFrame()
        summary_card.setFrameShape(QFrame.Shape.StyledPanel)
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(10, 10, 10, 10)
        summary_layout.addWidget(self._summary_label)

        self._metric_combo = QComboBox()
        self._metric_combo.setEnabled(False)
        self._metric_value_label = QLabel("Current Metric Value: n/a")
        self._metric_value_label.setStyleSheet("color: #334155;")
        plot_toolbar = QHBoxLayout()
        plot_toolbar.addWidget(QLabel("Metric"))
        plot_toolbar.addWidget(self._metric_combo)
        plot_toolbar.addWidget(self._metric_value_label)
        plot_toolbar.addStretch(1)

        self._plot = MetricPlotWidget()
        plot_card = QFrame()
        plot_card.setFrameShape(QFrame.Shape.StyledPanel)
        plot_layout = QVBoxLayout(plot_card)
        plot_layout.setContentsMargins(10, 10, 10, 10)
        plot_layout.setSpacing(8)
        plot_layout.addLayout(plot_toolbar)
        plot_layout.addWidget(self._plot, stretch=1)

        self._report_text = QTextEdit()
        self._report_text.setReadOnly(True)
        self._report_text.setPlaceholderText("A session report will appear here.")

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(summary_card)
        left_layout.addWidget(self._report_text, stretch=1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(plot_card, stretch=1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        split.setSizes([640, 820])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addLayout(controls)
        root.addWidget(timeline_card)
        root.addWidget(split, stretch=1)

        self._load_button.clicked.connect(self.load_session_requested)
        self._play_button.clicked.connect(self.play_requested)
        self._pause_button.clicked.connect(self.pause_requested)
        self._stop_button.clicked.connect(self.stop_requested)
        self._step_back_button.clicked.connect(self.step_backward_requested)
        self._step_fwd_button.clicked.connect(self.step_forward_requested)
        self._export_button.clicked.connect(self.export_report_requested)
        self._timeline.sliderPressed.connect(self._on_slider_pressed)
        self._timeline.sliderReleased.connect(self._on_slider_released)
        self._metric_combo.currentIndexChanged.connect(self._apply_selected_metric)

        self.setStyleSheet(
            """
            QFrame {
                background: #ffffff;
                border: 1px solid #d4dde8;
                border-radius: 4px;
            }
            QTextEdit {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d4dde8;
            }
            """
        )

    def playback_loop_enabled(self) -> bool:
        return self._loop_checkbox.isChecked()

    def set_timeline_limits(self, total_frames: int) -> None:
        self._total_frames = max(0, total_frames)
        max_frame_index = max(self._total_frames - 1, 0)
        self._timeline.setEnabled(self._total_frames > 0)
        self._timeline.setRange(0, max_frame_index)
        self._timeline_label.setText(f"Frame 0 / {max_frame_index}")
        self._plot.set_current_frame(0)
        self._update_metric_value(0)

    def update_playback_progress(self, frame_index: int, total_frames: int) -> None:
        if total_frames > 0 and total_frames != self._total_frames:
            self.set_timeline_limits(total_frames)
        if self._timeline.isEnabled() and not self._user_scrubbing:
            self._timeline.blockSignals(True)
            self._timeline.setValue(max(0, min(frame_index, self._timeline.maximum())))
            self._timeline.blockSignals(False)
        max_frame_index = max((self._total_frames if self._total_frames > 0 else total_frames) - 1, 0)
        self._timeline_label.setText(f"Frame {max(frame_index, 0)} / {max_frame_index}")
        self._plot.set_current_frame(frame_index)
        self._update_metric_value(frame_index)

    def set_analysis_report(self, report: SessionAnalysisReport | None) -> None:
        self._report = report
        self._metric_combo.blockSignals(True)
        self._metric_combo.clear()

        if report is None:
            self._summary_label.setText(
                "Analysis Summary\n\nNo stored 3D pose file was found for this session."
            )
            self._report_text.setPlainText(
                "This session does not contain a recorded pose3d stream yet.\n"
                "Record with valid 3D reconstruction enabled to unlock report generation."
            )
            self._metric_combo.setEnabled(False)
            self._export_button.setEnabled(False)
            self._plot.set_series(None)
            self._metric_value_label.setText("Current Metric Value: n/a")
            self._metric_combo.blockSignals(False)
            return

        self._summary_label.setText(
            "\n".join(
                [
                    "Analysis Summary",
                    "",
                    f"Session: {report.session_id}",
                    f"3D coverage: {report.frames_with_pose} / {report.total_frames} frames ({report.pose_coverage_ratio * 100:.1f}%)",
                    f"Duration: {report.duration_sec:.2f} s",
                    f"Average visible joints: {report.mean_visible_joints:.2f}",
                    f"Average confidence: {report.mean_confidence:.3f}",
                    (
                        f"Body-center path length: {report.center_path_length_m:.3f} m"
                        if report.center_path_length_m is not None
                        else "Body-center path length: n/a"
                    ),
                    (
                        f"Movement volume: {report.movement_volume_m3:.4f} m^3"
                        if report.movement_volume_m3 is not None
                        else "Movement volume: n/a"
                    ),
                ]
            )
        )
        self._report_text.setPlainText(report.to_text())

        for key, series in report.metric_series.items():
            self._metric_combo.addItem(series.label, key)
        self._metric_combo.setEnabled(self._metric_combo.count() > 0)
        self._export_button.setEnabled(True)
        self._metric_combo.blockSignals(False)
        self._apply_selected_metric()

    def current_report(self) -> SessionAnalysisReport | None:
        return self._report

    def _apply_selected_metric(self) -> None:
        if self._report is None:
            self._plot.set_series(None)
            self._metric_value_label.setText("Current Metric Value: n/a")
            return
        key = self._metric_combo.currentData()
        series = self._report.metric_series.get(str(key)) if key is not None else None
        self._plot.set_series(series)
        self._update_metric_value(self._timeline.value())

    def _update_metric_value(self, frame_index: int) -> None:
        if self._report is None:
            self._metric_value_label.setText("Current Metric Value: n/a")
            return
        key = self._metric_combo.currentData()
        series = self._report.metric_series.get(str(key)) if key is not None else None
        if series is None or not series.values:
            self._metric_value_label.setText("Current Metric Value: n/a")
            return
        index = max(0, min(frame_index, len(series.values) - 1))
        value = series.values[index]
        if value is None:
            self._metric_value_label.setText(f"Current Metric Value: frame {index} has no data")
            return
        self._metric_value_label.setText(f"Current Metric Value: {value:.2f} {series.unit}")

    def _on_slider_pressed(self) -> None:
        self._user_scrubbing = True

    def _on_slider_released(self) -> None:
        self._user_scrubbing = False
        if self._timeline.isEnabled():
            self.seek_requested.emit(self._timeline.value())
