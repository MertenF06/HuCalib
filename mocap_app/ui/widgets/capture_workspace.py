from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mocap_app.models.types import CameraProbeResult, CameraSourceConfig, RuntimeTuning
from mocap_app.ui.widgets.camera_grid import CameraGridWidget


class CaptureWorkspaceWidget(QWidget):
    """Capture-first workspace for live operation and recording."""

    start_live_requested = Signal(object, float, bool)
    stop_live_requested = Signal()
    start_recording_requested = Signal()
    stop_recording_requested = Signal()
    runtime_tuning_changed = Signal(object)
    probe_cameras_requested = Signal(int)
    ui_message = Signal(str)

    def __init__(
        self,
        default_camera_csv: str,
        default_fps: float,
        default_use_mediapipe: bool = False,
    ) -> None:
        super().__init__()
        self._camera_grid = CameraGridWidget()

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
        self._mediapipe_checkbox = QCheckBox("Use MediaPipe Detector")
        self._mediapipe_checkbox.setChecked(default_use_mediapipe)
        self._overlay_checkbox = QCheckBox("Draw Overlays")
        self._overlay_checkbox.setChecked(True)
        self._detect_capture_checkbox = QCheckBox("Detect In Capture")
        self._detect_capture_checkbox.setChecked(False)
        self._detect_reconstruction_checkbox = QCheckBox("Detect In Reconstruction")
        self._detect_reconstruction_checkbox.setChecked(True)
        self._detect_analysis_checkbox = QCheckBox("Detect In Analysis")
        self._detect_analysis_checkbox.setChecked(False)

        self._start_live_button = QPushButton("Start Live")
        self._stop_live_button = QPushButton("Stop Live")
        self._start_record_button = QPushButton("Start Recording")
        self._stop_record_button = QPushButton("Stop Recording")
        self._probe_button = QPushButton("Detect Cameras")
        self._probe_max_spin = QSpinBox()
        self._probe_max_spin.setRange(1, 20)
        self._probe_max_spin.setValue(10)
        self._probe_max_spin.setSuffix(" max idx")

        for button in [
            self._start_live_button,
            self._stop_live_button,
            self._start_record_button,
            self._stop_record_button,
            self._probe_button,
        ]:
            button.setMinimumHeight(34)

        controls_card = QFrame()
        controls_card.setFrameShape(QFrame.Shape.StyledPanel)
        controls_form = QFormLayout(controls_card)
        controls_form.setContentsMargins(10, 8, 10, 8)
        controls_form.setHorizontalSpacing(12)
        controls_form.setVerticalSpacing(6)
        controls_form.addRow("Sources (CSV)", self._camera_input)
        controls_form.addRow("Capture FPS", self._fps_spin)
        controls_form.addRow("Capture Resolution", self._capture_resolution_combo)
        controls_form.addRow("Preview FPS", self._preview_fps_spin)
        controls_form.addRow("Preview Width", self._preview_resolution_combo)
        controls_form.addRow("Calibration Detect Hz", self._calib_detect_hz_spin)
        controls_form.addRow("Detector", self._mediapipe_checkbox)
        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(8)
        toggles_row.addWidget(self._overlay_checkbox)
        toggles_row.addWidget(self._detect_capture_checkbox)
        toggles_row.addWidget(self._detect_reconstruction_checkbox)
        toggles_row.addWidget(self._detect_analysis_checkbox)
        toggles_row.addStretch(1)
        controls_form.addRow("Runtime Modes", toggles_row)

        controls_buttons = QHBoxLayout()
        controls_buttons.setSpacing(8)
        controls_buttons.addWidget(self._start_live_button)
        controls_buttons.addWidget(self._stop_live_button)
        controls_buttons.addWidget(self._start_record_button)
        controls_buttons.addWidget(self._stop_record_button)
        controls_buttons.addWidget(self._probe_button)
        controls_buttons.addWidget(self._probe_max_spin)
        controls_buttons.addStretch(1)

        self._live_label = QLabel("Live: Off")
        self._cams_label = QLabel("Cameras: 0")
        self._detector_label = QLabel("Detector: placeholder_pose")
        self._record_label = QLabel("Recording: Off")
        self._probe_label = QLabel("Camera scan: nog niet uitgevoerd.")
        self._probe_label.setWordWrap(True)

        status_card = QFrame()
        status_card.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QGridLayout(status_card)
        status_layout.setContentsMargins(10, 6, 10, 6)
        status_layout.setHorizontalSpacing(18)
        status_layout.setVerticalSpacing(4)
        status_layout.addWidget(self._live_label, 0, 0)
        status_layout.addWidget(self._cams_label, 0, 1)
        status_layout.addWidget(self._detector_label, 0, 2)
        status_layout.addWidget(self._record_label, 0, 3)
        status_layout.addWidget(self._probe_label, 1, 0, 1, 4)
        status_layout.setColumnStretch(4, 1)

        top = QVBoxLayout()
        top.setSpacing(6)
        top.addWidget(controls_card)
        top.addLayout(controls_buttons)
        top.addWidget(status_card)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addLayout(top)
        root.addWidget(self._camera_grid, stretch=1)

        self._start_live_button.clicked.connect(self._emit_start_live)
        self._stop_live_button.clicked.connect(self.stop_live_requested)
        self._start_record_button.clicked.connect(self.start_recording_requested)
        self._stop_record_button.clicked.connect(self.stop_recording_requested)
        self._probe_button.clicked.connect(self._emit_probe_cameras)

        for widget in [
            self._fps_spin,
            self._preview_fps_spin,
            self._capture_resolution_combo,
            self._preview_resolution_combo,
            self._calib_detect_hz_spin,
            self._overlay_checkbox,
            self._detect_capture_checkbox,
            self._detect_reconstruction_checkbox,
            self._detect_analysis_checkbox,
        ]:
            if isinstance(widget, QDoubleSpinBox):
                widget.valueChanged.connect(self._emit_runtime_tuning_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._emit_runtime_tuning_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._emit_runtime_tuning_changed)

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
            """
        )

    @property
    def camera_grid(self) -> CameraGridWidget:
        return self._camera_grid

    def _emit_start_live(self) -> None:
        try:
            sources = self.current_sources()
        except ValueError as exc:
            self.ui_message.emit(str(exc))
            return
        self.start_live_requested.emit(sources, self.target_fps(), self.use_mediapipe())

    def current_sources(self) -> list[CameraSourceConfig]:
        raw = self._camera_input.text().strip()
        if not raw:
            raise ValueError("Camera CSV is empty. Provide at least one source.")

        source_tokens = [token.strip() for token in raw.split(",") if token.strip()]
        if not source_tokens:
            raise ValueError("No valid camera sources parsed.")
        if len(source_tokens) > 4:
            raise ValueError("Use up to 4 sources for a clean capture workspace.")

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
        return self._fps_spin.value()

    def use_mediapipe(self) -> bool:
        return self._mediapipe_checkbox.isChecked()

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
            detection_capture_enabled=self._detect_capture_checkbox.isChecked(),
            detection_reconstruction_enabled=self._detect_reconstruction_checkbox.isChecked(),
            detection_analysis_enabled=self._detect_analysis_checkbox.isChecked(),
        )

    def set_runtime_status(
        self,
        live_active: bool,
        active_cameras: int,
        detector_name: str,
        recording_active: bool,
    ) -> None:
        self._live_label.setText(f"Live: {'On' if live_active else 'Off'}")
        self._cams_label.setText(f"Cameras: {active_cameras}")
        self._detector_label.setText(f"Detector: {detector_name}")
        self._record_label.setText(f"Recording: {'On' if recording_active else 'Off'}")

    def _emit_runtime_tuning_changed(self) -> None:
        self.runtime_tuning_changed.emit(self.runtime_tuning())

    def _emit_probe_cameras(self) -> None:
        self.probe_cameras_requested.emit(int(self._probe_max_spin.value()))

    def set_camera_probe_running(self, running: bool) -> None:
        self._probe_button.setEnabled(not running)
        self._probe_max_spin.setEnabled(not running)
        self._probe_button.setText("Scanning..." if running else "Detect Cameras")

    def set_detected_cameras(self, cameras: list[CameraProbeResult]) -> None:
        if not cameras:
            self._probe_label.setText("Camera scan: geen camera's gevonden.")
            return
        found = sorted(cameras, key=lambda c: c.index)
        text_parts = []
        for cam in found:
            resolution = f"{cam.width}x{cam.height}" if cam.width > 0 and cam.height > 0 else "unknown res"
            backend = f" ({cam.backend})" if cam.backend else ""
            text_parts.append(f"{cam.index}: {resolution}{backend}")
        diagnostics = []
        for cam in found:
            diagnostics.extend(cam.diagnostics)
        diagnostic_text = ""
        if diagnostics:
            diagnostic_text = " | warning: " + " ".join(dict.fromkeys(diagnostics))
        self._probe_label.setText("Camera scan: " + " | ".join(text_parts) + diagnostic_text)
        csv = ",".join(str(cam.index) for cam in found[:4])
        if csv:
            self._camera_input.setText(csv)
