"""Cameras / calibration tab.

Keeps the team's per-camera tile design (live preview, settings popover,
pop-out window, progress bar) and wires the Start / Calculate buttons to
the existing :class:`CalibrationManager` and :class:`IntrinsicsSolveWorker`
that already implement the calibration math.
"""

from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

SYNC_SKEW_WARNING_SEC = 0.050
SYNC_SKEW_REJECT_SEC = 0.150
SYNC_WARNING_THROTTLE_SEC = 3.0


# ----- discovery -----------------------------------------------------------


def discover_cameras(max_index: int = 6) -> list[tuple[int, str]]:
    """Return ``[(index, label)]`` for cameras that opened successfully.

    Done synchronously since the UI calls this rarely (on add-camera).
    """
    found: list[tuple[int, str]] = []
    for index in range(max_index + 1):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            continue
        ok, _ = cap.read()
        cap.release()
        if ok:
            found.append((index, f"Camera {index}"))
    return found


# ----- per-camera capture thread -----------------------------------------


class CameraThread(QtCore.QThread):
    change_pixmap_signal = QtCore.Signal(QtGui.QImage)
    frame_ready = QtCore.Signal(object)  # dict with numpy BGR ndarray + capture timing
    fps_updated = QtCore.Signal(float)
    dropped_updated = QtCore.Signal(int)

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480, fps: int = 30) -> None:
        super().__init__()
        self.camera_index = camera_index
        self.fps = max(1, int(fps))
        self.width = int(width)
        self.height = int(height)
        self._run_flag = True
        self.rotate = 0
        self.mirror = False
        self.exposure = -5
        self._measured_fps = 0.0
        self._dropped = 0

    def run(self) -> None:
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        current_w, current_h = 0, 0
        current_exp: int | None = None
        last_frame_time: float | None = None
        fps_window: list[float] = []

        while self._run_flag:
            start_time = time.time()
            if not cap.isOpened():
                self._dropped += 1
                self.dropped_updated.emit(self._dropped)
                time.sleep(0.1)
                continue

            if current_w != self.width or current_h != self.height:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                current_w, current_h = self.width, self.height

            if current_exp != self.exposure:
                cap.set(cv2.CAP_PROP_EXPOSURE, self.exposure)
                current_exp = self.exposure

            capture_started = time.time()
            ret, frame = cap.read()
            capture_completed = time.time()
            if not ret or frame is None:
                self._dropped += 1
                self.dropped_updated.emit(self._dropped)
                time.sleep(1.0 / max(self.fps, 1))
                continue

            if self.mirror:
                frame = cv2.flip(frame, 1)
            if self.rotate == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif self.rotate == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            elif self.rotate == 270:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

            now = time.time()
            if last_frame_time is not None:
                delta = now - last_frame_time
                if delta > 0:
                    fps_window.append(1.0 / delta)
                    if len(fps_window) > 12:
                        fps_window.pop(0)
                    self._measured_fps = sum(fps_window) / len(fps_window)
                    self.fps_updated.emit(self._measured_fps)
            last_frame_time = now

            self.frame_ready.emit(
                {
                    "frame": frame.copy(),
                    "timestamp_sec": capture_started,
                    "capture_completed_sec": capture_completed,
                }
            )

            rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            qt_img = QtGui.QImage(
                rgb_image.data, w, h, bytes_per_line, QtGui.QImage.Format.Format_RGB888
            )
            self.change_pixmap_signal.emit(qt_img.copy())

            sleep_time = max(1 / self.fps - (time.time() - start_time), 0.001)
            time.sleep(sleep_time)

        cap.release()

    def update_params(self, width: int, height: int, rotate: int, mirror: bool, exposure: int) -> None:
        self.width = int(width)
        self.height = int(height)
        self.rotate = int(rotate)
        self.mirror = bool(mirror)
        self.exposure = int(exposure)

    def stop(self) -> None:
        self._run_flag = False
        self.wait()


# ----- per-camera UI tile -------------------------------------------------


class CameraFrame(QtWidgets.QFrame):
    """Single live-camera tile inside the camera grid."""

    def __init__(self, on_delete_callback, available_cameras: list[tuple[int, str]]) -> None:
        super().__init__()
        self.on_delete_callback = on_delete_callback
        self.thread: CameraThread | None = None
        self.parent_tab: TabCameras | None = None
        self.last_img_frame: QtGui.QImage | None = None
        self.last_bgr_frame: np.ndarray | None = None
        self.intrinsic_captures = 0
        self.is_maximized = False
        self.popout_window: QtWidgets.QDialog | None = None
        self._measured_fps = 0.0
        self._dropped = 0

        self.setObjectName("camera_frame")
        self.setProperty("camera-tile", True)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setMinimumSize(280, 230)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.main_layout.setSpacing(6)

        # Controls
        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.setSpacing(6)

        self.combo_select_cam = QtWidgets.QComboBox()
        self.combo_select_cam.addItem("Geen Camera", -1)
        for cv2_index, label in available_cameras:
            self.combo_select_cam.addItem(label, cv2_index)
        self.combo_select_cam.currentIndexChanged.connect(self.manage_thread)
        controls_layout.addWidget(self.combo_select_cam, stretch=1)

        self.btn_maximize = QtWidgets.QPushButton("⛶")
        self.btn_maximize.setFixedSize(32, 28)
        self.btn_maximize.setToolTip("Vergroot / verklein deze camera")
        self.btn_maximize.clicked.connect(self.toggle_maximize)
        controls_layout.addWidget(self.btn_maximize)

        self.btn_settings = QtWidgets.QPushButton("⚙")
        self.btn_settings.setFixedSize(32, 28)
        self.btn_settings.setCheckable(True)
        self.btn_settings.setToolTip("Camera-instellingen")
        self.btn_settings.clicked.connect(self.toggle_view)
        controls_layout.addWidget(self.btn_settings)

        self.btn_delete = QtWidgets.QPushButton("✕")
        self.btn_delete.setFixedSize(32, 28)
        self.btn_delete.setToolTip("Verwijder deze camera")
        self.btn_delete.clicked.connect(self.full_cleanup)
        controls_layout.addWidget(self.btn_delete)
        self.main_layout.addLayout(controls_layout)

        # Stacked content: video / settings
        self.stacked = QtWidgets.QStackedWidget()

        self.video_label = QtWidgets.QLabel("Selecteer een camera")
        self.video_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.video_label.setProperty("camera-video", True)
        self.video_label.setMinimumHeight(140)

        self.settings_frame = QtWidgets.QFrame()
        self.settings_layout = QtWidgets.QFormLayout(self.settings_frame)
        self.settings_layout.setContentsMargins(6, 6, 6, 6)

        self.spin_width = QtWidgets.QSpinBox()
        self.spin_width.setRange(160, 3840)
        self.spin_width.setValue(640)
        self.spin_height = QtWidgets.QSpinBox()
        self.spin_height.setRange(120, 2160)
        self.spin_height.setValue(480)
        self.combo_rotate = QtWidgets.QComboBox()
        self.combo_rotate.addItems(["0", "90", "180", "270"])
        self.check_mirror = QtWidgets.QCheckBox("Mirror image")
        self.spin_exposure = QtWidgets.QSpinBox()
        self.spin_exposure.setRange(-13, 0)
        self.spin_exposure.setValue(-5)

        self.btn_apply = QtWidgets.QPushButton("Toepassen")
        self.btn_apply.setProperty("accent", True)
        self.btn_apply.clicked.connect(self.apply_settings)

        self.settings_layout.addRow("Breedte (px)", self.spin_width)
        self.settings_layout.addRow("Hoogte (px)", self.spin_height)
        self.settings_layout.addRow("Rotatie (°)", self.combo_rotate)
        self.settings_layout.addRow("Spiegelen", self.check_mirror)
        self.settings_layout.addRow("Exposure", self.spin_exposure)
        self.settings_layout.addRow("", self.btn_apply)

        self.stacked.addWidget(self.video_label)
        self.stacked.addWidget(self.settings_frame)
        self.main_layout.addWidget(self.stacked, stretch=1)

        # Intrinsics progress bar
        self.progress_intrinsics = QtWidgets.QProgressBar()
        self.progress_intrinsics.setRange(0, 100)
        self.progress_intrinsics.setValue(0)
        self.progress_intrinsics.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.update_progress_text()
        self.main_layout.addWidget(self.progress_intrinsics)

    # ----- public helpers ------------------------------------------------

    @property
    def source_id(self) -> str:
        index = self.combo_select_cam.currentData()
        if index is None or index == -1:
            return ""
        return f"cam{int(index)}"

    def measured_fps(self) -> float:
        return float(self._measured_fps)

    def dropped_frames(self) -> int:
        return int(self._dropped)

    def update_progress_text(self) -> None:
        self.progress_intrinsics.setFormat(f"{self.intrinsic_captures}/100")

    def add_intrinsic_capture(self) -> None:
        self.intrinsic_captures += 1
        self.progress_intrinsics.setValue(min(self.intrinsic_captures, 100))
        self.update_progress_text()

    def reset_intrinsic_captures(self) -> None:
        self.intrinsic_captures = 0
        self.progress_intrinsics.setValue(0)
        self.update_progress_text()

    def stop_thread(self) -> None:
        if self.thread is not None:
            self.thread.stop()
            self.thread = None

    # ----- thread / camera management ------------------------------------

    def manage_thread(self) -> None:
        self.stop_thread()
        cv2_index = self.combo_select_cam.currentData()
        if cv2_index is None or cv2_index == -1:
            self.video_label.setText("Selecteer een camera")
            return

        fps_default = 30
        if self.parent_tab is not None:
            fps_default = int(self.parent_tab.ui.spin_cap_fps.value())

        self.thread = CameraThread(
            camera_index=int(cv2_index),
            width=self.spin_width.value(),
            height=self.spin_height.value(),
            fps=fps_default,
        )
        self.apply_settings()
        self.thread.change_pixmap_signal.connect(self.update_frame)
        self.thread.frame_ready.connect(self._on_bgr_frame_ready)
        self.thread.fps_updated.connect(self._on_fps_updated)
        self.thread.dropped_updated.connect(self._on_dropped_updated)
        self.thread.start()

    def apply_settings(self) -> None:
        if self.thread is not None and self.thread.isRunning():
            self.thread.update_params(
                width=self.spin_width.value(),
                height=self.spin_height.value(),
                rotate=int(self.combo_rotate.currentText()),
                mirror=self.check_mirror.isChecked(),
                exposure=self.spin_exposure.value(),
            )

    def toggle_view(self) -> None:
        self.stacked.setCurrentIndex(1 if self.btn_settings.isChecked() else 0)

    def full_cleanup(self) -> None:
        self.stop_thread()
        if self.popout_window is not None:
            self.popout_window.close()
            self.popout_window = None
        self.on_delete_callback(self)

    def toggle_maximize(self) -> None:
        if not self.is_maximized:
            if self.combo_select_cam.currentData() == -1 or self.thread is None:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Geen actieve camera",
                    "Selecteer eerst een werkende camera voordat je deze vergroot.",
                )
                return

            self.is_maximized = True
            self.popout_window = QtWidgets.QDialog(self)
            cam_title = self.combo_select_cam.currentText()
            self.popout_window.setWindowTitle(f"Live Feed - {cam_title}")
            self.popout_window.resize(900, 640)

            layout = QtWidgets.QVBoxLayout(self.popout_window)
            layout.setContentsMargins(0, 0, 0, 0)

            self.popout_window.label_video = QtWidgets.QLabel("Live video stream start...")
            self.popout_window.label_video.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.popout_window.label_video.setProperty("camera-video", True)
            self.popout_window.label_video.setMinimumSize(1, 1)
            layout.addWidget(self.popout_window.label_video)

            def popout_resize_event(event: QtGui.QResizeEvent) -> None:
                if self.last_img_frame is not None:
                    pixmap = QtGui.QPixmap.fromImage(self.last_img_frame)
                    self.popout_window.label_video.setPixmap(
                        pixmap.scaled(
                            self.popout_window.label_video.size(),
                            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                            QtCore.Qt.TransformationMode.FastTransformation,
                        )
                    )
                QtWidgets.QDialog.resizeEvent(self.popout_window, event)

            self.popout_window.resizeEvent = popout_resize_event  # type: ignore[method-assign]
            self.popout_window.rejected.connect(self.window_closed)
            self.popout_window.show()

            if self.parent_tab is not None:
                self.parent_tab.log_to_console(
                    f"Systeem: Camera ({cam_title}) geopend in los venster."
                )
        else:
            if self.popout_window is not None:
                self.popout_window.close()

    # ----- frame handlers ------------------------------------------------

    def update_frame(self, img: QtGui.QImage) -> None:
        self.last_img_frame = img
        pixmap = QtGui.QPixmap.fromImage(img)

        if self.is_maximized and self.popout_window is not None and hasattr(self.popout_window, "label_video"):
            self.popout_window.label_video.setPixmap(
                pixmap.scaled(
                    self.popout_window.label_video.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.FastTransformation,
                )
            )
            if self.video_label.text() != "Gemaximaliseerd...":
                self.video_label.setText("Gemaximaliseerd...")
        elif not self.btn_settings.isChecked():
            self.video_label.setPixmap(
                pixmap.scaled(
                    self.video_label.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.FastTransformation,
                )
            )

    def _on_bgr_frame_ready(self, payload: Any) -> None:
        frame = payload
        timestamp_sec = time.time()
        capture_completed_sec = timestamp_sec
        if isinstance(payload, dict):
            frame = payload.get("frame")
            timestamp_sec = float(payload.get("timestamp_sec", timestamp_sec))
            capture_completed_sec = float(payload.get("capture_completed_sec", capture_completed_sec))
        if frame is None:
            return
        self.last_bgr_frame = frame
        if self.parent_tab is not None:
            self.parent_tab.on_camera_frame(
                self,
                frame,
                timestamp_sec=timestamp_sec,
                capture_completed_sec=capture_completed_sec,
            )

    def _on_fps_updated(self, value: float) -> None:
        self._measured_fps = float(value)

    def _on_dropped_updated(self, value: int) -> None:
        self._dropped = int(value)

    def window_closed(self) -> None:
        self.is_maximized = False
        self.popout_window = None
        self.video_label.setText("Video herstelt...")
        if self.parent_tab is not None:
            self.parent_tab.log_to_console("Systeem: Los venster gesloten.")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        self.setMinimumHeight(int(self.width() * 0.72))
        super().resizeEvent(event)


# ----- main camera tab -----------------------------------------------------


class TabCameras:
    """Camera grid + calibration capture / solve orchestration."""

    DETECTION_INTERVAL_SEC = 0.4
    EXTRINSICS_TICK_SEC = 0.6

    def __init__(self, logic_instance) -> None:
        self.logic = logic_instance
        self.ui = logic_instance.window
        self.camera_frames: list[CameraFrame] = []
        self.extrinsic_captures = 0

        self._intrinsics_active = False
        self._extrinsics_active = False
        self._last_detection_at: dict[str, float] = {}
        self._extrinsics_latest: dict[str, dict[str, Any]] = {}
        self._extrinsics_timer: QtCore.QTimer | None = None
        self._extrinsics_last_tick = 0.0
        self._last_sync_timing_warning_at = 0.0
        self._available_cameras: list[tuple[int, str]] = []

    # ----- backend handles ----------------------------------------------

    @property
    def manager(self):
        return self.logic.calibration_manager

    @property
    def repo(self):
        return self.logic.calibration_repo

    # ----- setup --------------------------------------------------------

    def setup(self) -> None:
        self.main_layout = self.ui.gridLayout_6
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.scroll_content = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout(self.scroll_content)
        self.grid_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.grid_layout.setSpacing(10)
        for i in range(3):
            self.grid_layout.setColumnStretch(i, 1)

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        self.progress_extrinsics = QtWidgets.QProgressBar()
        self.progress_extrinsics.setRange(0, 20)
        self.progress_extrinsics.setValue(0)
        self.progress_extrinsics.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.update_extrinsics_text()
        self.main_layout.addWidget(self.progress_extrinsics)

        self.ui.btn_cap_intrinsics_start.clicked.connect(self.toggle_intrinsics)
        self.ui.btn_cap_extrinsics_start.clicked.connect(self.toggle_extrinsics)
        self.ui.btn_cap_reset_calibration.clicked.connect(self.reset_calibration_buttons)
        self.ui.btn_cap_calculate_intrinsics.clicked.connect(self.calculate_intrinsics)
        self.ui.btn_cap_calculate_extrinsics.clicked.connect(self.calculate_extrinsics)
        self.ui.combo_cap_pattern.currentIndexChanged.connect(self._on_pattern_changed)
        self.ui.spin_cap_fps.valueChanged.connect(self._on_fps_changed)

        self._available_cameras = discover_cameras()
        self.setup_add_button()

        self._extrinsics_timer = QtCore.QTimer(self.ui)
        self._extrinsics_timer.setInterval(int(self.EXTRINSICS_TICK_SEC * 1000))
        self._extrinsics_timer.timeout.connect(self._extrinsics_tick)

    def setup_add_button(self) -> None:
        self.add_frame = QtWidgets.QFrame()
        self.add_frame.setMinimumSize(280, 230)
        layout = QtWidgets.QVBoxLayout(self.add_frame)
        self.btn_plus = QtWidgets.QPushButton("+\nCamera toevoegen")
        self.btn_plus.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.btn_plus.setStyleSheet(
            "QPushButton { font-size: 16pt; font-weight: 600; "
            "border: 2px dashed #94a3b8; color: #475569; background: #ffffff; }"
            "QPushButton:hover { background: #f1f5f9; border-color: #0078D4; color: #0078D4; }"
        )
        self.btn_plus.clicked.connect(self.add_new_camera)
        layout.addWidget(self.btn_plus)
        self.update_grid()

    # ----- camera grid management ---------------------------------------

    def add_new_camera(self) -> None:
        # Re-scan in case the user plugged something in.
        self._available_cameras = discover_cameras()
        unused = [
            (idx, label)
            for idx, label in self._available_cameras
            if not self._is_index_in_use(idx)
        ]
        if not unused:
            QtWidgets.QMessageBox.information(
                self.ui,
                "Geen camera's beschikbaar",
                "Geen extra camera's gevonden of alle gevonden camera's zijn al in gebruik.",
            )
            return

        new_cam = CameraFrame(self.remove_camera, self._available_cameras)
        new_cam.parent_tab = self
        self.camera_frames.append(new_cam)

        first_idx = unused[0][0]
        target_combo_index = next(
            (i for i in range(new_cam.combo_select_cam.count())
             if new_cam.combo_select_cam.itemData(i) == first_idx),
            0,
        )
        new_cam.combo_select_cam.setCurrentIndex(target_combo_index)
        new_cam.manage_thread()
        self.update_grid()

    def _is_index_in_use(self, cv2_index: int) -> bool:
        for frame in self.camera_frames:
            if frame.combo_select_cam.currentData() == cv2_index:
                return True
        return False

    def remove_camera(self, frame_to_remove: CameraFrame) -> None:
        if frame_to_remove not in self.camera_frames:
            return
        source_id = frame_to_remove.source_id
        frame_to_remove.stop_thread()
        self.camera_frames.remove(frame_to_remove)
        if source_id:
            self._extrinsics_latest.pop(source_id, None)
            self._last_detection_at.pop(source_id, None)
        frame_to_remove.setParent(None)
        frame_to_remove.deleteLater()
        self.update_grid()

    def update_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for i, frame in enumerate(self.camera_frames):
            self.grid_layout.addWidget(frame, i // 3, i % 3)
        position = len(self.camera_frames)
        self.grid_layout.addWidget(self.add_frame, position // 3, position % 3)

    # ----- progress UI --------------------------------------------------

    def update_extrinsics_text(self) -> None:
        self.progress_extrinsics.setFormat(
            f"Totale extrinsics progressie: {self.extrinsic_captures}/20"
        )

    def add_extrinsic_capture(self) -> None:
        self.extrinsic_captures += 1
        self.progress_extrinsics.setValue(min(self.extrinsic_captures, 20))
        self.update_extrinsics_text()

    # ----- mode toggles -------------------------------------------------

    def toggle_intrinsics(self) -> None:
        active = self.ui.btn_cap_intrinsics_start.isChecked()
        if active and self._extrinsics_active:
            self.ui.btn_cap_extrinsics_start.setChecked(False)
            self._set_extrinsics_active(False)

        self._set_intrinsics_active(active)

    def toggle_extrinsics(self) -> None:
        active = self.ui.btn_cap_extrinsics_start.isChecked()
        if active and self._intrinsics_active:
            self.ui.btn_cap_intrinsics_start.setChecked(False)
            self._set_intrinsics_active(False)

        self._set_extrinsics_active(active)

    def _set_intrinsics_active(self, active: bool) -> None:
        self._intrinsics_active = active
        self.ui.btn_cap_intrinsics_start.setChecked(active)
        self.ui.btn_cap_intrinsics_start.setText("Stop" if active else "Start")
        if active:
            self.log_to_console("Systeem: Intrinsics capture gestart - beweeg het bord door verschillende posities.")
        else:
            self.log_to_console("Systeem: Intrinsics capture gestopt.")

    def _set_extrinsics_active(self, active: bool) -> None:
        self._extrinsics_active = active
        self.ui.btn_cap_extrinsics_start.setChecked(active)
        self.ui.btn_cap_extrinsics_start.setText("Stop" if active else "Start")
        if active:
            if len([f for f in self.camera_frames if f.thread is not None]) < 2:
                self.log_to_console(
                    "Waarschuwing: Sync extrinsics werkt het beste met 2 of meer actieve camera's."
                )
            self._extrinsics_latest.clear()
            self._extrinsics_last_tick = time.perf_counter()
            if self._extrinsics_timer is not None:
                self._extrinsics_timer.start()
            self.log_to_console("Systeem: Extrinsics capture gestart - houd het bord zichtbaar in meerdere camera's.")
        else:
            if self._extrinsics_timer is not None:
                self._extrinsics_timer.stop()
            self._extrinsics_latest.clear()
            self.log_to_console("Systeem: Extrinsics capture gestopt.")

    def reset_calibration_buttons(self) -> None:
        self._set_intrinsics_active(False)
        self._set_extrinsics_active(False)
        for frame in self.camera_frames:
            frame.reset_intrinsic_captures()
        self.extrinsic_captures = 0
        self.progress_extrinsics.setValue(0)
        self.update_extrinsics_text()

        self.manager.reset()
        self.logic.current_bundle = None
        self.log_to_console("Systeem: Alle calibration samples gewist.")
        self.logic.refresh_results()

    # ----- frame handlers -----------------------------------------------

    def on_camera_frame(
        self,
        frame: CameraFrame,
        frame_bgr: np.ndarray,
        timestamp_sec: float | None = None,
        capture_completed_sec: float | None = None,
    ) -> None:
        source_id = frame.source_id
        if not source_id:
            return

        if self._extrinsics_active:
            capture_started = float(timestamp_sec if timestamp_sec is not None else time.time())
            capture_completed = float(
                capture_completed_sec if capture_completed_sec is not None else capture_started
            )
            self._extrinsics_latest[source_id] = {
                "frame": frame_bgr,
                "timestamp_sec": capture_started,
                "capture_completed_sec": capture_completed,
            }

        if not self._intrinsics_active:
            return

        now = time.perf_counter()
        if now - self._last_detection_at.get(source_id, 0.0) < self.DETECTION_INTERVAL_SEC:
            return
        self._last_detection_at[source_id] = now

        pattern = self._current_pattern()
        try:
            feedback_by_source = self.manager.try_add_observation_set(
                frames_by_source={source_id: frame_bgr},
                pattern=pattern,
                allow_relaxed_sync=False,
                workflow_mode="intrinsics",
            )
        except Exception as exc:  # noqa: BLE001
            self.log_to_console(f"Fout tijdens intrinsics detectie ({source_id}): {exc}")
            return

        feedback = feedback_by_source.get(source_id)
        if feedback is None:
            return
        if feedback.accepted:
            frame.add_intrinsic_capture()
            self.log_to_console(
                f"{source_id}: sample geaccepteerd "
                f"(quality={feedback.detection.quality_score:.2f}, "
                f"coverage={feedback.detection.coverage_ratio * 100:.1f}%)."
            )

    def _extrinsics_tick(self) -> None:
        if not self._extrinsics_active:
            return
        latest = dict(self._extrinsics_latest)
        if len(latest) < 2:
            return
        timestamps = [
            float(payload.get("timestamp_sec", 0.0))
            for payload in latest.values()
            if isinstance(payload, dict)
        ]
        skew_sec = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else 0.0
        if skew_sec > SYNC_SKEW_REJECT_SEC:
            self._log_sync_timing_message(
                "Sync extrinsics set geweigerd: camera timestamp-skew "
                f"{skew_sec * 1000.0:.1f} ms > {SYNC_SKEW_REJECT_SEC * 1000.0:.0f} ms."
            )
            return
        if skew_sec > SYNC_SKEW_WARNING_SEC:
            self._log_sync_timing_message(
                "Waarschuwing: sync extrinsics timestamp-skew "
                f"{skew_sec * 1000.0:.1f} ms; houd het bord stil."
            )
        frames = {
            source_id: payload["frame"]
            for source_id, payload in latest.items()
            if isinstance(payload, dict) and payload.get("frame") is not None
        }
        if len(frames) < 2:
            return
        sync_metadata = {
            "software_sync": True,
            "timestamp_source": "capture_thread_timestamp_sec",
            "timestamp_skew_ms": round(skew_sec * 1000.0, 3),
            "warning_timestamp_skew_ms": round(SYNC_SKEW_WARNING_SEC * 1000.0, 3),
            "max_allowed_timestamp_skew_ms": round(SYNC_SKEW_REJECT_SEC * 1000.0, 3),
            "source_timestamps_sec": {
                source_id: round(float(payload.get("timestamp_sec", 0.0)), 6)
                for source_id, payload in latest.items()
                if isinstance(payload, dict)
            },
            "capture_completed_sec": {
                source_id: round(float(payload.get("capture_completed_sec", 0.0)), 6)
                for source_id, payload in latest.items()
                if isinstance(payload, dict)
            },
        }
        pattern = self._current_pattern()
        try:
            feedback = self.manager.try_add_observation_set(
                frames_by_source=frames,
                pattern=pattern,
                allow_relaxed_sync=True,
                workflow_mode="sync_extrinsics",
                sync_metadata=sync_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            self.log_to_console(f"Fout tijdens extrinsics detectie: {exc}")
            return

        accepted_sources = [src for src, fb in feedback.items() if fb.accepted]
        if accepted_sources:
            self.add_extrinsic_capture()
            self.log_to_console(
                f"Sync extrinsics set toegevoegd ({len(accepted_sources)} camera's): "
                + ", ".join(accepted_sources)
            )

    def _log_sync_timing_message(self, message: str) -> None:
        now = time.perf_counter()
        if now - self._last_sync_timing_warning_at < SYNC_WARNING_THROTTLE_SEC:
            return
        self._last_sync_timing_warning_at = now
        self.log_to_console(message)

    # ----- calculate actions --------------------------------------------

    def calculate_intrinsics(self) -> None:
        if self.logic.intrinsics_worker is not None:
            self.log_to_console("Intrinsics solve al actief.")
            return

        sources = self.manager.sources()
        if not sources:
            QtWidgets.QMessageBox.information(
                self.ui,
                "Geen samples",
                "Verzamel eerst intrinsics samples voordat je solve aanroept.",
            )
            return

        self.ui.btn_cap_calculate_intrinsics.setEnabled(False)
        self.ui.btn_cap_calculate_extrinsics.setEnabled(False)

        from mocap_app.workers.calibration_solve_worker import IntrinsicsSolveWorker
        worker = IntrinsicsSolveWorker(calibration_manager=self.manager)
        worker.result_ready.connect(self._on_intrinsics_result)
        worker.error.connect(self._on_intrinsics_error)
        worker.finished.connect(self._on_intrinsics_finished)
        self.logic.intrinsics_worker = worker
        self.logic.tab_diagnostics.begin_intrinsics_timer()

        total_samples = sum(
            self.manager.observations_summary(include_sync_only=False).values()
        )
        self.log_to_console(f"Systeem: Intrinsics solve gestart ({total_samples} samples)...")
        worker.start()

    def _on_intrinsics_result(self, bundle_obj: Any) -> None:
        from mocap_app.models.types import CalibrationBundle
        if not isinstance(bundle_obj, CalibrationBundle):
            self.log_to_console("Onverwacht resultaat van intrinsics solve.")
            return
        self.logic.current_bundle = bundle_obj
        self.repo.save(bundle_obj, self.logic.calibration_path)
        solved = [c for c in bundle_obj.cameras.values() if c.status.startswith("solved")]
        mean_err = (
            sum(c.reprojection_error or 0.0 for c in solved) / len(solved)
            if solved
            else 0.0
        )
        self.log_to_console(
            f"Systeem: Intrinsics opgelost - {len(solved)}/{len(bundle_obj.cameras)} camera's "
            f"(gem. reproj={mean_err:.4f} px)."
        )
        self.logic.refresh_results()

    def _on_intrinsics_error(self, message: str) -> None:
        self.log_to_console(f"Fout bij intrinsics solve: {message}")

    def _on_intrinsics_finished(self) -> None:
        worker = self.logic.intrinsics_worker
        self.logic.intrinsics_worker = None
        self.ui.btn_cap_calculate_intrinsics.setEnabled(True)
        self.ui.btn_cap_calculate_extrinsics.setEnabled(True)
        self.logic.tab_diagnostics.end_intrinsics_timer()
        if worker is not None:
            worker.deleteLater()

    def calculate_extrinsics(self) -> None:
        base_bundle = self.logic.current_bundle or self.manager.last_solution()
        if base_bundle is None:
            if not self.manager.sources():
                QtWidgets.QMessageBox.information(
                    self.ui,
                    "Geen samples",
                    "Verzamel eerst intrinsics samples en solve intrinsics voor extrinsics.",
                )
                return
            self.log_to_console("Systeem: Intrinsics niet opgelost - probeer eerst Calculate Intrinsics.")
            return

        active_source_ids = [f.source_id for f in self.camera_frames if f.source_id]
        reference = active_source_ids[0] if active_source_ids else None
        self.logic.tab_diagnostics.begin_extrinsics_timer()
        try:
            bundle = self.manager.solve_extrinsics(
                base_bundle=base_bundle,
                reference_source_id=reference,
            )
        except Exception as exc:  # noqa: BLE001
            self.logic.tab_diagnostics.end_extrinsics_timer()
            self.log_to_console(f"Fout bij extrinsics solve: {exc}")
            return
        self.logic.tab_diagnostics.end_extrinsics_timer()

        self.logic.current_bundle = bundle
        self.repo.save(bundle, self.logic.calibration_path)
        solved = [
            source_id
            for source_id, cam in bundle.cameras.items()
            if cam.rotation is not None and cam.translation is not None
        ]
        self.log_to_console(
            f"Systeem: Extrinsics opgelost - {len(solved)}/{len(bundle.cameras)} camera's "
            f"(referentie {reference})."
        )
        self.logic.refresh_results()

    # ----- misc helpers -------------------------------------------------

    def _current_pattern(self) -> str:
        data = self.ui.combo_cap_pattern.currentData()
        text = str(data if data is not None else self.ui.combo_cap_pattern.currentText())
        text = text.lower().strip()
        return "charuco" if "char" in text else "chessboard"

    def _on_pattern_changed(self, _index: int) -> None:
        self.log_to_console(f"Systeem: Patroon ingesteld op {self._current_pattern()}.")

    def _on_fps_changed(self, value: int) -> None:
        for frame in self.camera_frames:
            if frame.thread is not None:
                frame.thread.fps = max(1, int(value))

    def log_to_console(self, text: str) -> None:
        self.logic.log_to_console(text)

    def capture_intrinsics_for_camera(self, cam_idx: int) -> None:
        active_frames = [
            f for f in self.camera_frames if f.combo_select_cam.currentData() != -1
        ]
        if not active_frames:
            self.log_to_console("Systeem: Er zijn momenteel geen actieve camera's geopend.")
            return
        if 0 <= cam_idx < len(active_frames):
            target = active_frames[cam_idx]
            target.add_intrinsic_capture()
            self.log_to_console(
                f"Systeem: Intrinsics capture toegevoegd aan camera {cam_idx} "
                f"({target.combo_select_cam.currentText()})."
            )
        else:
            self.log_to_console(
                f"Fout: camera index {cam_idx} bestaat niet "
                f"(0..{len(active_frames) - 1})."
            )
