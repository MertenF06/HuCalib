from __future__ import annotations

from typing import Any, Literal

import cv2
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from mocap_app.io.calibration_io import ChessboardDetectionResult
from mocap_app.models.types import (
    CalibrationBoardSettings,
    CalibrationBundle,
    CameraCalibration,
    CameraProbeResult,
    CameraSourceConfig,
    RuntimeTuning,
)


class CalibrationPreviewTile(QFrame):
    """Single camera tile used by the calibration panel."""

    undistort_toggled = Signal(str, bool)
    start_auto_capture_requested = Signal()

    def __init__(self, source_id: str) -> None:
        super().__init__()
        self._source_id = source_id
        self._popout_window: CalibrationPreviewPopout | None = None
        self._title = QLabel(source_id)
        self._title.setStyleSheet("font-weight: 600; color: #1f2937;")
        self._start_auto_button = QPushButton("Start Auto")
        self._popout_button = QPushButton("Open Window")
        self._undistort_checkbox = QCheckBox("Undistort")
        self._undistort_checkbox.toggled.connect(
            lambda checked: self.undistort_toggled.emit(self._source_id, checked)
        )
        self._image = QLabel("No Frame")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setMinimumSize(280, 190)
        self._image.setStyleSheet("background: #f8fafc; border: 1px solid #cbd5e1; color: #64748b;")
        self._status = QLabel("Awaiting detections")
        self._status.setStyleSheet("color: #475569;")
        self._last_pixmap: QPixmap | None = None

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._start_auto_button)
        header.addWidget(self._popout_button)
        header.addWidget(self._undistort_checkbox)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)
        root.addLayout(header)
        root.addWidget(self._image, stretch=1)
        root.addWidget(self._status)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d4dde8; border-radius: 4px; }")
        self._start_auto_button.clicked.connect(self.start_auto_capture_requested)
        self._popout_button.clicked.connect(self._open_popout_window)
        self.destroyed.connect(self._close_popout_window)

    @property
    def source_id(self) -> str:
        return self._source_id

    def undistort_enabled(self) -> bool:
        return self._undistort_checkbox.isChecked()

    def set_frame(self, frame_bgr, status_message: str) -> None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, c = rgb.shape
        image = QImage(rgb.data, w, h, c * w, QImage.Format.Format_RGB888).copy()
        self._last_pixmap = QPixmap.fromImage(image)
        self._render()
        self._status.setText(status_message)
        if self._popout_window is not None:
            self._popout_window.set_frame(self._last_pixmap, status_message)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._render()
        super().resizeEvent(event)

    def _render(self) -> None:
        if self._last_pixmap is None:
            return
        if (
            self._image.width() >= self._last_pixmap.width()
            and self._image.height() >= self._last_pixmap.height()
        ):
            self._image.setPixmap(self._last_pixmap)
            return
        self._image.setPixmap(
            self._last_pixmap.scaled(
                self._image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def _open_popout_window(self) -> None:
        if self._popout_window is None:
            self._popout_window = CalibrationPreviewPopout(self._source_id, parent=self)
            self._popout_window.closed.connect(self._on_popout_window_closed)
            self._popout_window.start_auto_capture_requested.connect(self.start_auto_capture_requested)
            if self._last_pixmap is not None:
                self._popout_window.set_frame(self._last_pixmap, self._status.text())
        self._popout_window.show()
        self._popout_window.raise_()
        self._popout_window.activateWindow()

    def _on_popout_window_closed(self) -> None:
        self._popout_window = None

    def _close_popout_window(self, *_args: object) -> None:
        if self._popout_window is not None:
            self._popout_window.close()
            self._popout_window = None


class CalibrationPreviewPopout(QWidget):
    """Detached larger view for a single live calibration preview."""

    closed = Signal()
    start_auto_capture_requested = Signal()

    def __init__(self, source_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._source_id = source_id
        self._last_pixmap: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"Camera Feed - {source_id}")
        self.resize(1180, 780)

        self._image = QLabel("No frame yet")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setMinimumSize(760, 520)
        self._image.setStyleSheet("background: #020617; color: #dbeafe;")
        self._start_auto_button = QPushButton("Start Auto Capture")

        self._status = QLabel("Awaiting frame")
        self._status.setStyleSheet("color: #1f2937; padding: 4px;")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(QLabel(source_id))
        header.addStretch(1)
        header.addWidget(self._start_auto_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(header)
        root.addWidget(self._image, stretch=1)
        root.addWidget(self._status)
        self._start_auto_button.clicked.connect(self.start_auto_capture_requested)

    def set_frame(self, pixmap: QPixmap, status_message: str) -> None:
        self._last_pixmap = pixmap
        self._status.setText(status_message)
        self._render()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._render()
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.closed.emit()
        super().closeEvent(event)

    def _render(self) -> None:
        if self._last_pixmap is None:
            return
        if (
            self._image.width() >= self._last_pixmap.width()
            and self._image.height() >= self._last_pixmap.height()
        ):
            self._image.setPixmap(self._last_pixmap)
            return
        self._image.setPixmap(
            self._last_pixmap.scaled(
                self._image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )


class CalibrationPanelWidget(QWidget):
    """Dedicated calibration workflow panel with previews and diagnostics."""

    new_project_requested = Signal()
    start_live_requested = Signal(object, float)
    stop_live_requested = Signal()
    runtime_tuning_changed = Signal(object)
    probe_cameras_requested = Signal(int)
    ui_message = Signal(str)
    capture_requested = Signal()
    solve_requested = Signal()
    solve_extrinsics_requested = Signal()
    reset_requested = Signal()
    save_profile_requested = Signal()
    load_profile_requested = Signal()
    undistort_toggled = Signal(str, bool)
    auto_capture_start_requested = Signal()
    pattern_changed = Signal(str)
    board_settings_applied = Signal(object)
    acceptance_thresholds_changed = Signal(float, float)
    workflow_mode_changed = Signal(str)
    spatial_grid_changed = Signal(int, int)

    def __init__(self, default_camera_csv: str = "0", default_fps: float = 30.0) -> None:
        super().__init__()
        self._tiles: dict[str, CalibrationPreviewTile] = {}
        self._source_order: list[str] = []

        self._camera_input = QLineEdit(default_camera_csv)
        self._camera_input.setPlaceholderText("Example: 0,1")
        self._fps_spin = QDoubleSpinBox()
        self._fps_spin.setRange(1.0, 120.0)
        self._fps_spin.setDecimals(1)
        self._fps_spin.setValue(default_fps)
        self._fps_spin.setSingleStep(1.0)
        self._preview_fps_spin = QDoubleSpinBox()
        self._preview_fps_spin.setRange(1.0, 120.0)
        self._preview_fps_spin.setDecimals(1)
        self._preview_fps_spin.setValue(min(default_fps, 30.0))
        self._preview_fps_spin.setSingleStep(1.0)
        self._preview_resolution_combo = QComboBox()
        self._preview_resolution_combo.addItem("Auto", 0)
        self._preview_resolution_combo.addItem("1920 px", 1920)
        self._preview_resolution_combo.addItem("1280 px", 1280)
        self._preview_resolution_combo.addItem("960 px", 960)
        self._preview_resolution_combo.addItem("640 px", 640)
        self._preview_resolution_combo.setCurrentIndex(4)
        self._capture_resolution_combo = QComboBox()
        self._capture_resolution_combo.addItem("Auto", (0, 0))
        self._capture_resolution_combo.addItem("640 x 480", (640, 480))
        self._capture_resolution_combo.addItem("960 x 540", (960, 540))
        self._capture_resolution_combo.addItem("1280 x 720", (1280, 720))
        self._capture_resolution_combo.addItem("1920 x 1080", (1920, 1080))
        self._capture_resolution_combo.setCurrentIndex(3)
        self._calib_detect_hz_spin = QDoubleSpinBox()
        self._calib_detect_hz_spin.setRange(0.5, 20.0)
        self._calib_detect_hz_spin.setDecimals(1)
        self._calib_detect_hz_spin.setValue(5.0)
        self._calib_detect_hz_spin.setSingleStep(0.5)
        self._chessboard_cols_spin = QSpinBox()
        self._chessboard_cols_spin.setRange(2, 30)
        self._chessboard_cols_spin.setValue(9)
        self._chessboard_rows_spin = QSpinBox()
        self._chessboard_rows_spin.setRange(2, 30)
        self._chessboard_rows_spin.setValue(6)
        self._chessboard_square_mm_spin = QDoubleSpinBox()
        self._chessboard_square_mm_spin.setRange(1.0, 500.0)
        self._chessboard_square_mm_spin.setDecimals(2)
        self._chessboard_square_mm_spin.setValue(24.0)
        self._chessboard_square_mm_spin.setSuffix(" mm")
        self._charuco_squares_x_spin = QSpinBox()
        self._charuco_squares_x_spin.setRange(2, 30)
        self._charuco_squares_x_spin.setValue(5)
        self._charuco_squares_y_spin = QSpinBox()
        self._charuco_squares_y_spin.setRange(2, 30)
        self._charuco_squares_y_spin.setValue(3)
        self._charuco_square_mm_spin = QDoubleSpinBox()
        self._charuco_square_mm_spin.setRange(1.0, 500.0)
        self._charuco_square_mm_spin.setDecimals(2)
        self._charuco_square_mm_spin.setValue(77.0)
        self._charuco_square_mm_spin.setSuffix(" mm")
        self._charuco_marker_mm_spin = QDoubleSpinBox()
        self._charuco_marker_mm_spin.setRange(1.0, 500.0)
        self._charuco_marker_mm_spin.setDecimals(2)
        self._charuco_marker_mm_spin.setValue(61.0)
        self._charuco_marker_mm_spin.setSuffix(" mm")
        self._apply_board_button = QPushButton("Apply Board Settings")
        self._start_live_button = QPushButton("Start Live")
        self._stop_live_button = QPushButton("Stop Live")
        self._probe_button = QPushButton("Detect Cameras")
        self._probe_max_spin = QSpinBox()
        self._probe_max_spin.setRange(1, 20)
        self._probe_max_spin.setValue(10)
        self._probe_max_spin.setSuffix(" max idx")
        self._live_label = QLabel("Live: Off")
        self._cams_label = QLabel("Cameras: 0")
        self._probe_label = QLabel("Camera scan: not run yet.")
        self._probe_label.setWordWrap(True)

        self._capture_button = QPushButton("Capture Valid Sample(s)")
        self._solve_button = QPushButton("Solve Intrinsics")
        self._solve_extrinsics_button = QPushButton("Solve Extrinsics")
        self._reset_button = QPushButton("Reset Samples")
        self._new_project_button = QPushButton("New Project")
        self._save_button = QPushButton("Save Profile")
        self._load_button = QPushButton("Load Profile")
        self._workflow_mode_combo = QComboBox()
        self._workflow_mode_combo.addItem("Intrinsics", "intrinsics")
        self._workflow_mode_combo.addItem("Sync / Extrinsics", "sync_extrinsics")
        self._pattern_combo = QComboBox()
        self._pattern_combo.addItem("Chessboard", "chessboard")
        self._overlay_checkbox = QCheckBox("Show Detection Overlay")
        self._overlay_checkbox.setChecked(True)
        self._mirror_preview_checkbox = QCheckBox("Mirror Preview")
        self._mirror_preview_checkbox.setChecked(False)
        self._spatial_grid_cols_spin = QSpinBox()
        self._spatial_grid_cols_spin.setRange(1, 20)
        self._spatial_grid_cols_spin.setValue(6)
        self._spatial_grid_rows_spin = QSpinBox()
        self._spatial_grid_rows_spin.setRange(1, 20)
        self._spatial_grid_rows_spin.setValue(4)
        self._auto_capture_checkbox = QCheckBox("Auto Capture Valid Samples")
        self._auto_capture_checkbox.setChecked(False)
        self._relaxed_sync_checkbox = QCheckBox("Relax Sync Thresholds")
        self._relaxed_sync_checkbox.setChecked(True)
        self._auto_capture_cooldown_spin = QDoubleSpinBox()
        self._auto_capture_cooldown_spin.setRange(0.1, 10.0)
        self._auto_capture_cooldown_spin.setDecimals(2)
        self._auto_capture_cooldown_spin.setSingleStep(0.01)
        self._auto_capture_cooldown_spin.setValue(0.33)
        self._auto_capture_max_spin = QSpinBox()
        self._auto_capture_max_spin.setRange(0, 1000)
        self._auto_capture_max_spin.setValue(60)
        self._auto_capture_max_spin.setSpecialValueText("No limit")
        self._auto_capture_max_spin.setSuffix(" samples")
        self._sync_quality_spin = QDoubleSpinBox()
        self._sync_quality_spin.setRange(0.0, 1.0)
        self._sync_quality_spin.setDecimals(2)
        self._sync_quality_spin.setSingleStep(0.05)
        self._sync_quality_spin.setValue(0.25)
        self._sync_coverage_spin = QDoubleSpinBox()
        self._sync_coverage_spin.setRange(0.0, 25.0)
        self._sync_coverage_spin.setDecimals(1)
        self._sync_coverage_spin.setSingleStep(0.2)
        self._sync_coverage_spin.setSuffix(" %")
        self._sync_coverage_spin.setValue(1.8)
        self._threshold_quality_label = QLabel("Intrinsics Min Quality")
        self._threshold_coverage_label = QLabel("Intrinsics Min Coverage")
        self._auto_status = QLabel("Auto capture off.")
        self._auto_status.setStyleSheet("color: #475569;")

        for spin in [
            self._chessboard_cols_spin,
            self._chessboard_rows_spin,
            self._charuco_squares_x_spin,
            self._charuco_squares_y_spin,
            self._spatial_grid_cols_spin,
            self._spatial_grid_rows_spin,
            self._probe_max_spin,
        ]:
            spin.setMaximumWidth(86)

        for spin in [
            self._chessboard_square_mm_spin,
            self._charuco_square_mm_spin,
            self._charuco_marker_mm_spin,
            self._auto_capture_cooldown_spin,
            self._auto_capture_max_spin,
            self._sync_quality_spin,
            self._sync_coverage_spin,
        ]:
            spin.setMaximumWidth(118)

        for button in [
            self._start_live_button,
            self._stop_live_button,
            self._probe_button,
            self._apply_board_button,
            self._capture_button,
            self._solve_button,
            self._solve_extrinsics_button,
            self._reset_button,
            self._new_project_button,
            self._save_button,
            self._load_button,
        ]:
            button.setMinimumHeight(34)

        live_controls = QFrame()
        live_controls.setFrameShape(QFrame.Shape.StyledPanel)
        live_form = QFormLayout(live_controls)
        live_form.setContentsMargins(10, 8, 10, 8)
        live_form.setHorizontalSpacing(12)
        live_form.setVerticalSpacing(6)
        live_form.addRow("Sources (CSV)", self._camera_input)
        live_form.addRow("Capture FPS", self._fps_spin)
        live_form.addRow("Capture Resolution", self._capture_resolution_combo)
        live_form.addRow("Preview FPS", self._preview_fps_spin)
        live_form.addRow("Preview Width", self._preview_resolution_combo)
        live_form.addRow("Calibration Detect Hz", self._calib_detect_hz_spin)

        chessboard_shape = QWidget()
        chessboard_shape_layout = QGridLayout(chessboard_shape)
        chessboard_shape_layout.setContentsMargins(0, 0, 0, 0)
        chessboard_shape_layout.setSpacing(6)
        chessboard_shape_layout.addWidget(QLabel("Cols"), 0, 0)
        chessboard_shape_layout.addWidget(self._chessboard_cols_spin, 0, 1)
        chessboard_shape_layout.addWidget(QLabel("Rows"), 0, 2)
        chessboard_shape_layout.addWidget(self._chessboard_rows_spin, 0, 3)
        chessboard_shape_layout.addWidget(QLabel("Square"), 0, 4)
        chessboard_shape_layout.addWidget(self._chessboard_square_mm_spin, 0, 5)
        chessboard_shape_layout.setColumnStretch(6, 1)
        live_form.addRow("Chessboard", chessboard_shape)

        charuco_shape = QWidget()
        charuco_shape_layout = QGridLayout(charuco_shape)
        charuco_shape_layout.setContentsMargins(0, 0, 0, 0)
        charuco_shape_layout.setSpacing(6)
        charuco_shape_layout.addWidget(QLabel("Squares X"), 0, 0)
        charuco_shape_layout.addWidget(self._charuco_squares_x_spin, 0, 1)
        charuco_shape_layout.addWidget(QLabel("Y"), 0, 2)
        charuco_shape_layout.addWidget(self._charuco_squares_y_spin, 0, 3)
        charuco_shape_layout.addWidget(QLabel("Square"), 1, 0)
        charuco_shape_layout.addWidget(self._charuco_square_mm_spin, 1, 1)
        charuco_shape_layout.addWidget(QLabel("Marker"), 1, 2)
        charuco_shape_layout.addWidget(self._charuco_marker_mm_spin, 1, 3)
        charuco_shape_layout.setColumnStretch(4, 1)
        live_form.addRow("ChArUco", charuco_shape)
        live_form.addRow("", self._apply_board_button)

        live_buttons = QHBoxLayout()
        live_buttons.setSpacing(8)
        live_buttons.addWidget(self._start_live_button)
        live_buttons.addWidget(self._stop_live_button)
        live_buttons.addWidget(self._probe_button)
        live_buttons.addWidget(self._probe_max_spin)
        live_buttons.addStretch(1)

        live_status = QHBoxLayout()
        live_status.setSpacing(18)
        live_status.addWidget(self._live_label)
        live_status.addWidget(self._cams_label)
        live_status.addWidget(self._probe_label, stretch=1)

        toolbar_controls = QWidget()
        toolbar = QGridLayout(toolbar_controls)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setHorizontalSpacing(8)
        toolbar.setVerticalSpacing(4)
        toolbar.addWidget(QLabel("Workflow"), 0, 0)
        toolbar.addWidget(self._workflow_mode_combo, 0, 1)
        toolbar.addWidget(QLabel("Pattern"), 0, 2)
        toolbar.addWidget(self._pattern_combo, 0, 3)
        toolbar.addWidget(self._new_project_button, 0, 5)
        toolbar.addWidget(self._save_button, 0, 6)
        toolbar.addWidget(self._load_button, 0, 7)
        toolbar.addWidget(self._capture_button, 1, 0, 1, 2)
        toolbar.addWidget(self._solve_button, 1, 2)
        toolbar.addWidget(self._solve_extrinsics_button, 1, 3)
        toolbar.addWidget(self._reset_button, 1, 4)
        toolbar.setColumnStretch(4, 1)

        auto_controls = QWidget()
        auto_toolbar = QGridLayout(auto_controls)
        auto_toolbar.setContentsMargins(0, 0, 0, 0)
        auto_toolbar.setHorizontalSpacing(8)
        auto_toolbar.setVerticalSpacing(4)
        auto_toolbar.addWidget(self._overlay_checkbox, 0, 0)
        auto_toolbar.addWidget(self._mirror_preview_checkbox, 0, 1)
        auto_toolbar.addWidget(self._auto_capture_checkbox, 0, 2)
        auto_toolbar.addWidget(QLabel("Cooldown"), 0, 3)
        auto_toolbar.addWidget(self._auto_capture_cooldown_spin, 0, 4)
        auto_toolbar.addWidget(QLabel("Max"), 0, 5)
        auto_toolbar.addWidget(self._auto_capture_max_spin, 0, 6)
        auto_toolbar.addWidget(self._relaxed_sync_checkbox, 0, 7)
        auto_toolbar.addWidget(self._threshold_quality_label, 1, 0)
        auto_toolbar.addWidget(self._sync_quality_spin, 1, 1)
        auto_toolbar.addWidget(self._threshold_coverage_label, 1, 2)
        auto_toolbar.addWidget(self._sync_coverage_spin, 1, 3)
        auto_toolbar.addWidget(self._auto_status, 1, 4, 1, 4)
        auto_toolbar.addWidget(QLabel("Overlay Grid"), 2, 0)
        auto_toolbar.addWidget(self._spatial_grid_cols_spin, 2, 1)
        auto_toolbar.addWidget(QLabel("x"), 2, 2)
        auto_toolbar.addWidget(self._spatial_grid_rows_spin, 2, 3)
        auto_toolbar.setColumnStretch(8, 1)

        self._preview_container = QWidget()
        self._preview_layout = QGridLayout(self._preview_container)
        self._preview_layout.setContentsMargins(4, 4, 4, 4)
        self._preview_layout.setSpacing(6)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Camera", "Intrinsics / Total", "Intrinsics Status", "Reproj Error", "Image Size", "Diagnostics"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self._feedback = QLabel("Calibration feedback will appear here.")
        self._feedback.setWordWrap(True)
        self._feedback.setStyleSheet("color: #1f2937;")
        self._solve_progress = QProgressBar()
        self._solve_progress.setRange(0, 0)
        self._solve_progress.setTextVisible(True)
        self._solve_progress.setFormat("Solving intrinsics...")
        self._solve_progress.hide()

        self._warnings = QTextEdit()
        self._warnings.setReadOnly(True)
        self._warnings.setPlaceholderText("Diagnostics warnings and TODO hooks...")

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._table, stretch=2)
        right_layout.addWidget(self._feedback)
        right_layout.addWidget(self._solve_progress)
        right_layout.addWidget(self._warnings, stretch=1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._preview_container)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([1100, 650])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(live_controls)
        root.addLayout(live_buttons)
        root.addLayout(live_status)
        root.addWidget(toolbar_controls)
        root.addWidget(auto_controls)
        root.addWidget(split, stretch=1)

        self._start_live_button.clicked.connect(self._emit_start_live)
        self._stop_live_button.clicked.connect(self.stop_live_requested)
        self._probe_button.clicked.connect(self._emit_probe_cameras)
        self._capture_button.clicked.connect(self.capture_requested)
        self._solve_button.clicked.connect(self.solve_requested)
        self._solve_extrinsics_button.clicked.connect(self.solve_extrinsics_requested)
        self._reset_button.clicked.connect(self.reset_requested)
        self._new_project_button.clicked.connect(self.new_project_requested)
        self._save_button.clicked.connect(self.save_profile_requested)
        self._load_button.clicked.connect(self.load_profile_requested)
        self._pattern_combo.currentIndexChanged.connect(self._emit_pattern_changed)
        self._apply_board_button.clicked.connect(self._emit_board_settings_applied)
        self._workflow_mode_combo.currentIndexChanged.connect(self._emit_workflow_mode_changed)
        self._overlay_checkbox.toggled.connect(self._emit_runtime_tuning_changed)
        self._mirror_preview_checkbox.toggled.connect(self._emit_runtime_tuning_changed)
        self._auto_capture_max_spin.valueChanged.connect(self._emit_runtime_tuning_changed)
        self._spatial_grid_cols_spin.valueChanged.connect(self._emit_spatial_grid_changed)
        self._spatial_grid_rows_spin.valueChanged.connect(self._emit_spatial_grid_changed)
        self._sync_quality_spin.valueChanged.connect(self._emit_acceptance_thresholds_changed)
        self._sync_coverage_spin.valueChanged.connect(self._emit_acceptance_thresholds_changed)
        self._apply_workflow_mode_ui()

        for widget in [
            self._fps_spin,
            self._preview_fps_spin,
            self._capture_resolution_combo,
            self._preview_resolution_combo,
            self._calib_detect_hz_spin,
        ]:
            if isinstance(widget, QDoubleSpinBox):
                widget.valueChanged.connect(self._emit_runtime_tuning_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._emit_runtime_tuning_changed)

        self.setStyleSheet(
            """
            QFrame {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 4px;
            }
            QLabel {
                color: #1f2a37;
            }
            QTableWidget {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d4dde8;
                gridline-color: #d8e0ea;
            }
            QHeaderView::section {
                background: #eef3f9;
                color: #1f2937;
                border: 1px solid #d4dde8;
                padding: 5px;
            }
            QTextEdit {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d4dde8;
            }
            QProgressBar {
                background: #eef3f9;
                color: #1f2937;
                border: 1px solid #cfd8e3;
                border-radius: 4px;
                min-height: 18px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #2563eb;
                border-radius: 3px;
            }
            """
        )
    def set_sources(self, source_ids: list[str]) -> None:
        source_ids = source_ids[:4]
        existing = set(self._tiles.keys())
        requested = set(source_ids)
        if source_ids == self._source_order and existing == requested:
            return

        for source_id in sorted(existing - requested):
            tile = self._tiles.pop(source_id)
            self._preview_layout.removeWidget(tile)
            tile.deleteLater()

        for source_id in source_ids:
            if source_id in self._tiles:
                continue
            tile = CalibrationPreviewTile(source_id)
            tile.undistort_toggled.connect(self.undistort_toggled)
            tile.start_auto_capture_requested.connect(self.auto_capture_start_requested)
            self._tiles[source_id] = tile

        self._source_order = list(source_ids)
        self._rebuild_preview_layout()

    def current_sources(self) -> list[CameraSourceConfig]:
        raw = self._camera_input.text().strip()
        if not raw:
            raise ValueError("Camera CSV is empty. Provide at least one source.")

        source_tokens = [token.strip() for token in raw.split(",") if token.strip()]
        if not source_tokens:
            raise ValueError("No valid camera sources parsed.")
        if len(source_tokens) > 4:
            raise ValueError("Use up to 4 sources for calibration.")
        sources: list[CameraSourceConfig] = []
        for index, token in enumerate(source_tokens):
            if token.isdigit():
                sources.append(
                    CameraSourceConfig(
                        source_id=f"cam{index}",
                        kind="webcam",
                        uri=int(token),
                        label=f"Webcam {token}",
                    )
                )
            else:
                sources.append(
                    CameraSourceConfig(
                        source_id=f"cam{index}",
                        kind="video",
                        uri=token,
                        label=token,
                    )
                )
        return sources

    def target_fps(self) -> float:
        return float(self._fps_spin.value())

    def runtime_tuning(self) -> RuntimeTuning:
        preview_width = int(self._preview_resolution_combo.currentData())
        capture_size = self._capture_resolution_combo.currentData()
        if not isinstance(capture_size, tuple) or len(capture_size) != 2:
            capture_size = (0, 0)
        return RuntimeTuning(
            capture_fps=float(self._fps_spin.value()),
            capture_width=int(capture_size[0]),
            capture_height=int(capture_size[1]),
            preview_fps=float(self._preview_fps_spin.value()),
            preview_max_width=preview_width if preview_width > 0 else 0,
            calibration_detection_hz=float(self._calib_detect_hz_spin.value()),
            overlays_enabled=self._overlay_checkbox.isChecked(),
            detection_capture_enabled=False,
            detection_reconstruction_enabled=False,
            detection_analysis_enabled=False,
        )

    def set_live_status(self, live_active: bool, active_cameras: int) -> None:
        self._live_label.setText(f"Live: {'On' if live_active else 'Off'}")
        self._cams_label.setText(f"Cameras: {active_cameras}")

    def set_camera_probe_running(self, running: bool) -> None:
        self._probe_button.setEnabled(not running)
        self._probe_max_spin.setEnabled(not running)
        self._probe_button.setText("Scanning..." if running else "Detect Cameras")

    def set_detected_cameras(self, cameras: list[CameraProbeResult]) -> None:
        if not cameras:
            self._probe_label.setText("Camera scan: no cameras found.")
            return
        found = sorted(cameras, key=lambda camera: camera.index)
        text_parts = []
        for camera in found:
            resolution = f"{camera.width}x{camera.height}" if camera.width > 0 and camera.height > 0 else "unknown res"
            backend = f" ({camera.backend})" if camera.backend else ""
            text_parts.append(f"{camera.index}: {resolution}{backend}")
        self._probe_label.setText("Camera scan: " + " | ".join(text_parts))
        csv = ",".join(str(camera.index) for camera in found[:4])
        if csv:
            self._camera_input.setText(csv)

    def probe_max_index(self) -> int:
        return int(self._probe_max_spin.value())

    def set_intrinsics_solve_running(self, running: bool, message: str = "Solving intrinsics...") -> None:
        self._solve_progress.setVisible(running)
        self._solve_button.setEnabled(not running)
        self._solve_extrinsics_button.setEnabled(not running)
        self._capture_button.setEnabled(not running)
        self._reset_button.setEnabled(not running)
        self._new_project_button.setEnabled(not running)
        self._load_button.setEnabled(not running)
        self._workflow_mode_combo.setEnabled(not running)
        self._pattern_combo.setEnabled(not running)
        self._auto_capture_checkbox.setEnabled(not running)
        self._auto_capture_max_spin.setEnabled(not running)
        self._spatial_grid_cols_spin.setEnabled(not running)
        self._spatial_grid_rows_spin.setEnabled(not running)
        self._apply_board_button.setEnabled(not running)
        self._sync_quality_spin.setEnabled(not running)
        self._sync_coverage_spin.setEnabled(not running)
        self._relaxed_sync_checkbox.setEnabled((not running) and self.current_workflow_mode() == "sync_extrinsics")
        if running:
            self._solve_progress.setFormat(message)

    def undistort_enabled_for(self, source_id: str) -> bool:
        tile = self._tiles.get(source_id)
        return tile.undistort_enabled() if tile else False

    def current_pattern(self) -> str:
        data = self._pattern_combo.currentData()
        return str(data if data is not None else "chessboard").lower()

    def board_settings(self) -> CalibrationBoardSettings:
        return CalibrationBoardSettings(
            chessboard_cols=int(self._chessboard_cols_spin.value()),
            chessboard_rows=int(self._chessboard_rows_spin.value()),
            chessboard_square_size_m=float(self._chessboard_square_mm_spin.value()) / 1000.0,
            charuco_squares_x=int(self._charuco_squares_x_spin.value()),
            charuco_squares_y=int(self._charuco_squares_y_spin.value()),
            charuco_square_size_m=float(self._charuco_square_mm_spin.value()) / 1000.0,
            charuco_marker_size_m=float(self._charuco_marker_mm_spin.value()) / 1000.0,
        )

    def set_board_settings(self, settings: CalibrationBoardSettings) -> None:
        self._chessboard_cols_spin.setValue(int(settings.chessboard_cols))
        self._chessboard_rows_spin.setValue(int(settings.chessboard_rows))
        self._chessboard_square_mm_spin.setValue(float(settings.chessboard_square_size_m) * 1000.0)
        self._charuco_squares_x_spin.setValue(int(settings.charuco_squares_x))
        self._charuco_squares_y_spin.setValue(int(settings.charuco_squares_y))
        self._charuco_square_mm_spin.setValue(float(settings.charuco_square_size_m) * 1000.0)
        self._charuco_marker_mm_spin.setValue(float(settings.charuco_marker_size_m) * 1000.0)

    def current_workflow_mode(self) -> Literal["intrinsics", "sync_extrinsics"]:
        data = self._workflow_mode_combo.currentData()
        mode = str(data if data is not None else "intrinsics").lower().strip()
        if mode == "sync_extrinsics":
            return "sync_extrinsics"
        return "intrinsics"

    def set_workflow_mode(self, mode: str) -> None:
        target = mode.lower().strip()
        index = self._workflow_mode_combo.findData(target)
        self._workflow_mode_combo.blockSignals(True)
        self._workflow_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._workflow_mode_combo.blockSignals(False)
        self._apply_workflow_mode_ui()

    def auto_capture_enabled(self) -> bool:
        return self._auto_capture_checkbox.isChecked()

    def set_auto_capture_enabled(self, enabled: bool) -> None:
        self._auto_capture_checkbox.setChecked(enabled)

    def overlay_enabled(self) -> bool:
        return self._overlay_checkbox.isChecked()

    def mirror_preview_enabled(self) -> bool:
        return self._mirror_preview_checkbox.isChecked()

    def spatial_grid_values(self) -> tuple[int, int]:
        return int(self._spatial_grid_cols_spin.value()), int(self._spatial_grid_rows_spin.value())

    def set_spatial_grid_values(self, cols: int, rows: int) -> None:
        self._spatial_grid_cols_spin.blockSignals(True)
        self._spatial_grid_rows_spin.blockSignals(True)
        self._spatial_grid_cols_spin.setValue(max(1, int(cols)))
        self._spatial_grid_rows_spin.setValue(max(1, int(rows)))
        self._spatial_grid_cols_spin.blockSignals(False)
        self._spatial_grid_rows_spin.blockSignals(False)

    def auto_capture_cooldown_sec(self) -> float:
        return float(self._auto_capture_cooldown_spin.value())

    def auto_capture_max_samples(self) -> int:
        return int(self._auto_capture_max_spin.value())

    def relaxed_sync_enabled(self) -> bool:
        return self._relaxed_sync_checkbox.isChecked()

    def set_auto_capture_status(self, message: str) -> None:
        self._auto_status.setText(message)

    def set_acceptance_threshold_values(self, min_quality: float, min_coverage_ratio: float) -> None:
        self._sync_quality_spin.blockSignals(True)
        self._sync_coverage_spin.blockSignals(True)
        self._sync_quality_spin.setValue(min_quality)
        self._sync_coverage_spin.setValue(min_coverage_ratio * 100.0)
        self._sync_quality_spin.blockSignals(False)
        self._sync_coverage_spin.blockSignals(False)

    def acceptance_threshold_values(self) -> tuple[float, float]:
        return float(self._sync_quality_spin.value()), float(self._sync_coverage_spin.value()) / 100.0

    def set_pattern_options(self, pattern_names: list[str], selected: str) -> None:
        current = self.current_pattern()
        self._pattern_combo.blockSignals(True)
        self._pattern_combo.clear()
        for name in pattern_names:
            key = name.lower().strip()
            label = "Charuco" if key == "charuco" else "Chessboard"
            self._pattern_combo.addItem(label, key)
        if self._pattern_combo.count() == 0:
            self._pattern_combo.addItem("Chessboard", "chessboard")
        target = selected.lower().strip() if selected else current
        index = self._pattern_combo.findData(target)
        self._pattern_combo.setCurrentIndex(index if index >= 0 else 0)
        self._pattern_combo.blockSignals(False)
        self._emit_pattern_changed()

    def update_previews(
        self,
        preview_frames: dict[str, Any],
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
    ) -> None:
        for source_id, frame_bgr in preview_frames.items():
            tile = self._tiles.get(source_id)
            if tile is None:
                continue
            detection = detections.get(source_id)
            count = sample_counts.get(source_id, 0)
            status = f"Intrinsics={count}"
            if detection is not None and detection.found:
                status += (
                    f" | {detection.pattern_type}"
                    f" | corners={detection.detected_corners}"
                    f" | q={detection.quality_score:.2f}"
                    f" | cov={detection.coverage_ratio * 100:.1f}%"
                )
            elif detection is not None:
                status += f" | {detection.pattern_type} not found"
            tile.set_frame(frame_bgr, status)

    def update_camera_status_table(
        self,
        source_ids: list[str],
        sample_counts: dict[str, int],
        sample_breakdown: dict[str, dict[str, int]],
        bundle: CalibrationBundle | None,
        live_detection: dict[str, ChessboardDetectionResult] | None = None,
    ) -> None:
        self._table.setRowCount(len(source_ids))
        for row, source_id in enumerate(source_ids):
            camera = bundle.cameras.get(source_id) if bundle else None
            breakdown = sample_breakdown.get(source_id, {})
            diagnostics = []
            if camera and camera.diagnostics:
                diagnostics.extend(camera.diagnostics)
            if live_detection and source_id in live_detection:
                diagnostics.extend(live_detection[source_id].diagnostics)
            sync_only = int(breakdown.get("sync_only", 0))
            synchronized = int(breakdown.get("synchronized", 0))
            if sync_only > 0:
                diagnostics.append(f"{sync_only} sync-only sample(s) stored with relaxed thresholds.")
            elif synchronized > 0:
                diagnostics.append(f"{synchronized} synchronized sample(s) stored.")

            self._table.setItem(row, 0, QTableWidgetItem(source_id))
            self._table.setItem(
                row,
                1,
                QTableWidgetItem(
                    f"{sample_counts.get(source_id, 0)} / {int(breakdown.get('total', sample_counts.get(source_id, 0)))}"
                ),
            )
            self._table.setItem(row, 2, QTableWidgetItem(self._status_text(camera)))
            self._table.setItem(
                row,
                3,
                QTableWidgetItem(
                    f"{camera.reprojection_error:.4f}px"
                    if camera and camera.reprojection_error is not None
                    else "-"
                ),
            )
            self._table.setItem(
                row,
                4,
                QTableWidgetItem(
                    f"{camera.image_size[0]}x{camera.image_size[1]}"
                    if camera and camera.image_size
                    else "-"
                ),
            )
            self._table.setItem(row, 5, QTableWidgetItem("; ".join(dict.fromkeys(diagnostics)) or "-"))

    def show_feedback(self, message: str, success: bool) -> None:
        color = "#8df0b0" if success else "#ffd37e"
        self._feedback.setStyleSheet(f"color: {color};")
        self._feedback.setText(message)

    def show_warnings(self, lines: list[str]) -> None:
        self._warnings.setPlainText("\n".join(lines))

    def _status_text(self, camera: CameraCalibration | None) -> str:
        if camera is None:
            return "unsolved"
        return camera.status

    def _rebuild_preview_layout(self) -> None:
        while self._preview_layout.count():
            item = self._preview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        if not self._source_order:
            return

        columns = 1 if len(self._source_order) == 1 else 2
        rows = (len(self._source_order) + columns - 1) // columns

        for col in range(columns):
            self._preview_layout.setColumnStretch(col, 1)
        for row in range(rows):
            self._preview_layout.setRowStretch(row, 1)

        for idx, source_id in enumerate(self._source_order):
            row = idx // columns
            col = idx % columns
            self._preview_layout.addWidget(self._tiles[source_id], row, col)

    def _emit_pattern_changed(self) -> None:
        self.pattern_changed.emit(self.current_pattern())

    def _emit_board_settings_applied(self) -> None:
        self.board_settings_applied.emit(self.board_settings())

    def _emit_acceptance_thresholds_changed(self) -> None:
        quality, coverage_ratio = self.acceptance_threshold_values()
        self.acceptance_thresholds_changed.emit(quality, coverage_ratio)

    def _emit_spatial_grid_changed(self) -> None:
        cols, rows = self.spatial_grid_values()
        self.spatial_grid_changed.emit(cols, rows)

    def _emit_runtime_tuning_changed(self) -> None:
        self.runtime_tuning_changed.emit(self.runtime_tuning())

    def _emit_start_live(self) -> None:
        try:
            sources = self.current_sources()
        except ValueError as exc:
            self.ui_message.emit(str(exc))
            return
        self.start_live_requested.emit(sources, self.target_fps())

    def _emit_probe_cameras(self) -> None:
        self.probe_cameras_requested.emit(int(self._probe_max_spin.value()))

    def _emit_workflow_mode_changed(self) -> None:
        self._apply_workflow_mode_ui()
        self.workflow_mode_changed.emit(self.current_workflow_mode())

    def _apply_workflow_mode_ui(self) -> None:
        mode = self.current_workflow_mode()
        sync_controls_enabled = mode == "sync_extrinsics"
        self._relaxed_sync_checkbox.setEnabled(sync_controls_enabled)

        if mode == "sync_extrinsics":
            self._capture_button.setText("Capture Sync Set(s)")
            self._threshold_quality_label.setText("Sync Min Quality")
            self._threshold_coverage_label.setText("Sync Min Coverage")
        else:
            self._capture_button.setText("Capture Intrinsics Sample(s)")
            self._threshold_quality_label.setText("Intrinsics Min Quality")
            self._threshold_coverage_label.setText("Intrinsics Min Coverage")
