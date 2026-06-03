from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMainWindow, QMessageBox

from mocap_app.core.config import AppConfig
from mocap_app.io.calibration_io import (
    CalibrationManager,
    CalibrationRepository,
    ChessboardDetectionResult,
)
from mocap_app.io import calibration_export
from mocap_app.io.video_recorder import VideoRecorder
from mocap_app.models.types import (
    CalibrationBoardSettings,
    CalibrationBundle,
    CameraProbeResult,
    CameraSourceConfig,
    FramePacket,
    RuntimeTuning,
)
from mocap_app.ui.widgets.calibration_panel import CalibrationPanelWidget
from mocap_app.workers.calibration_solve_worker import IntrinsicsSolveWorker
from mocap_app.workers.camera_probe_worker import CameraProbeWorker
from mocap_app.workers.capture_worker import LiveCaptureWorker
from mocap_app.workers.detection_worker import CalibrationDetectionWorker
from mocap_app.workers.preview_render_worker import PreviewRenderWorker


LOGGER = logging.getLogger(__name__)
SYNC_SKEW_WARNING_SEC = 0.050
SYNC_SKEW_REJECT_SEC = 0.150
SYNC_WARNING_THROTTLE_SEC = 3.0
# With this many cameras or fewer, prepare the display frame inline on the UI
# thread (lowest latency). Above it, offload to the preview-render worker.
INLINE_PREVIEW_MAX_CAMERAS = 3


class MainWindow(QMainWindow):
    """Calibration-only application shell."""

    # Emitted from the UI thread to hand a detection job to the background
    # detection worker (connected with a queued connection across threads).
    request_detection = Signal(object)
    # Emitted to hand a display-frame prep job to the preview-render worker.
    request_preview_render = Signal(object)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

        self._calibration_repo = CalibrationRepository()
        self._calibration_manager = CalibrationManager()
        self._calibration_path = self._default_calibration_path()
        self._current_calibration_bundle: CalibrationBundle | None = None
        self._calibration_loaded = False
        self._calibration_pattern = self._calibration_manager.default_pattern
        self._latest_calibration_detections: dict[str, ChessboardDetectionResult] = {}
        self._last_calibration_detection_at = 0.0
        self._calibration_detection_interval_sec = 0.25
        self._last_calibration_panel_refresh_at = 0.0
        self._calibration_panel_refresh_interval_sec = 0.35
        self._last_calibration_auto_capture_at = 0.0
        self._last_live_status_refresh_at = 0.0
        self._last_sync_timing_warning_at = 0.0

        self._live_worker: LiveCaptureWorker | None = None
        self._camera_probe_worker: CameraProbeWorker | None = None
        self._intrinsics_solve_worker: IntrinsicsSolveWorker | None = None
        self._detection_thread: QThread | None = None
        self._detection_worker: CalibrationDetectionWorker | None = None
        self._detection_request_in_flight = False
        self._render_thread: QThread | None = None
        self._render_worker: PreviewRenderWorker | None = None
        self._render_request_in_flight = False
        self._video_recorder: VideoRecorder | None = None
        self._last_recording_dir: Path | None = None
        self._active_sources: list[CameraSourceConfig] = []
        self._detected_cameras: list[CameraProbeResult] = []
        self._runtime_tuning = RuntimeTuning()
        self._latest_frames: dict[str, FramePacket] = {}
        self._last_rendered_frame_indices: dict[str, int] = {}
        self._calibration_overlay_cache: dict[str, tuple[tuple[Any, ...], Any, Any]] = {}
        self._active_camera_count = 0

        self._calibration_panel = self._create_calibration_panel(
            default_camera_csv=self._config.default_camera_csv,
            default_fps=self._config.target_fps,
        )
        self._runtime_tuning = self._calibration_panel.runtime_tuning()
        self._calibration_detection_interval_sec = 1.0 / max(
            self._runtime_tuning.calibration_detection_hz,
            0.1,
        )

        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._on_display_tick)

        self._setup_ui()
        self._apply_window_style()
        self._connect_signals()
        self._setup_detection_worker()
        self._setup_preview_render_worker()

        self._calibration_panel.set_pattern_options(
            pattern_names=self._calibration_manager.available_patterns(),
            selected=self._calibration_pattern,
        )
        self._calibration_panel.set_board_settings(self._calibration_manager.board_settings())
        self._calibration_panel.set_spatial_grid_values(*self._calibration_manager.spatial_grid_shape)
        self._calibration_panel.set_workflow_mode("intrinsics")
        self._refresh_threshold_controls_for_mode()

        self._load_existing_calibration()
        self._seed_startup_source_slots()
        self._refresh_live_status(force=True)
        self._refresh_calibration_panel(force=True)
        self._set_display_timer_hz(self._runtime_tuning.preview_fps)

        self.setWindowTitle(self._config.app_name)
        self._apply_initial_window_geometry()
        self._set_status("Ready for camera calibration")
        QTimer.singleShot(250, self._start_initial_camera_probe)

    def _setup_ui(self) -> None:
        self.setCentralWidget(self._calibration_panel)
        self.statusBar().showMessage("Idle")

    def _create_calibration_panel(self, default_camera_csv: str, default_fps: float):
        return CalibrationPanelWidget(
            default_camera_csv=default_camera_csv,
            default_fps=default_fps,
        )

    def _apply_initial_window_geometry(self) -> None:
        self.resize(1500, 920)

    def _apply_window_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #f4f7fb;
            }
            QStatusBar {
                background-color: #e9eef5;
                color: #1f2937;
            }
            """
        )

    def _connect_signals(self) -> None:
        self._calibration_panel.start_live_requested.connect(self._on_start_live)
        self._calibration_panel.stop_live_requested.connect(self._on_stop_live)
        self._calibration_panel.runtime_tuning_changed.connect(self._on_runtime_tuning_changed)
        self._calibration_panel.probe_cameras_requested.connect(self._on_probe_cameras)
        self._calibration_panel.ui_message.connect(self._show_warning)
        self._calibration_panel.capture_requested.connect(self._on_capture_calibration)
        self._calibration_panel.solve_requested.connect(self._on_solve_calibration)
        self._calibration_panel.solve_extrinsics_requested.connect(self._on_solve_extrinsics)
        self._calibration_panel.reset_requested.connect(self._on_reset_calibration_samples)
        self._calibration_panel.new_project_requested.connect(self._on_new_project)
        self._calibration_panel.save_profile_requested.connect(self._on_save_calibration_profile)
        self._calibration_panel.load_profile_requested.connect(self._on_load_calibration_profile)
        self._calibration_panel.undistort_toggled.connect(self._on_undistort_toggle_changed)
        self._calibration_panel.auto_capture_start_requested.connect(self._on_start_auto_capture_from_preview)
        self._calibration_panel.pattern_changed.connect(self._on_calibration_pattern_changed)
        self._calibration_panel.board_settings_applied.connect(self._on_board_settings_applied)
        self._calibration_panel.acceptance_thresholds_changed.connect(self._on_acceptance_thresholds_changed)
        self._calibration_panel.workflow_mode_changed.connect(self._on_calibration_workflow_mode_changed)
        self._calibration_panel.spatial_grid_changed.connect(self._on_spatial_grid_changed)
        if hasattr(self._calibration_panel, "record_toggled"):
            self._calibration_panel.record_toggled.connect(self._on_record_toggled)
        if hasattr(self._calibration_panel, "export_preview_requested"):
            self._calibration_panel.export_preview_requested.connect(self._on_export_preview)
        if hasattr(self._calibration_panel, "export_requested"):
            self._calibration_panel.export_requested.connect(self._on_export_calibration)
        if hasattr(self._calibration_panel, "sources_changed"):
            self._calibration_panel.sources_changed.connect(self._on_panel_sources_changed)
        if hasattr(self._calibration_panel, "preview_options_changed"):
            self._calibration_panel.preview_options_changed.connect(lambda: self._update_calibration_preview(force=True))

    def _set_display_timer_hz(self, hz: float) -> None:
        safe_hz = max(1.0, hz)
        interval_ms = max(8, int(1000.0 / safe_hz))
        self._display_timer.setInterval(interval_ms)
        if not self._display_timer.isActive():
            self._display_timer.start()

    def _setup_detection_worker(self) -> None:
        """Start the background thread that runs pattern detection.

        Detection is the dominant per-frame cost; running it on the UI thread
        froze the Qt event loop every detection cycle. The worker lives for the
        whole session and processes one job at a time (gated by
        ``_detection_request_in_flight``) so requests never queue up.
        """
        self._detection_thread = QThread(self)
        self._detection_worker = CalibrationDetectionWorker(self._calibration_manager)
        self._detection_worker.moveToThread(self._detection_thread)
        self.request_detection.connect(self._detection_worker.run_detection)
        self._detection_worker.result_ready.connect(self._on_detection_result)
        self._detection_thread.start()

    def _shutdown_detection_worker(self) -> None:
        thread = self._detection_thread
        if thread is None:
            return
        thread.quit()
        thread.wait(2000)
        self._detection_thread = None
        self._detection_worker = None
        self._detection_request_in_flight = False

    def _setup_preview_render_worker(self) -> None:
        """Start the background thread that prepares display preview frames.

        Undistort, mirror, downscale and BGR-to-RGB conversion previously ran on
        the UI thread every display tick. Moving them here keeps the event loop
        free for painting and input. One job runs at a time (gated by
        ``_render_request_in_flight``) so frames never queue up.
        """
        self._render_thread = QThread(self)
        self._render_worker = PreviewRenderWorker(self._calibration_manager)
        self._render_worker.moveToThread(self._render_thread)
        self.request_preview_render.connect(self._render_worker.render)
        self._render_worker.rendered.connect(self._on_preview_render_result)
        self._render_thread.start()

    def _shutdown_preview_render_worker(self) -> None:
        thread = self._render_thread
        if thread is None:
            return
        thread.quit()
        thread.wait(2000)
        self._render_thread = None
        self._render_worker = None
        self._render_request_in_flight = False

    def _default_calibration_path(self) -> Path:
        return self._config.calibration_dir / "current_calibration.json"

    def _on_runtime_tuning_changed(self, tuning_obj: object) -> None:
        if not isinstance(tuning_obj, RuntimeTuning):
            return
        self._runtime_tuning = tuning_obj
        self._set_display_timer_hz(tuning_obj.preview_fps)
        self._calibration_detection_interval_sec = 1.0 / max(tuning_obj.calibration_detection_hz, 0.1)
        self._update_calibration_preview(force=True)

    def _start_initial_camera_probe(self) -> None:
        if self._camera_probe_worker is not None:
            return
        probe_max = 10
        panel_probe_max = getattr(self._calibration_panel, "probe_max_index", None)
        if callable(panel_probe_max):
            try:
                probe_max = int(panel_probe_max())
            except (TypeError, ValueError):
                probe_max = 10
        self._on_probe_cameras(probe_max)

    def _stop_camera_probe_worker(self) -> None:
        if self._camera_probe_worker is None:
            return
        self._camera_probe_worker.stop()
        if not self._camera_probe_worker.wait(2500):
            LOGGER.warning("Camera probe worker did not stop in time; forcing termination.")
            self._camera_probe_worker.terminate()
            self._camera_probe_worker.wait(1000)
        self._camera_probe_worker = None
        self._calibration_panel.set_camera_probe_running(False)

    def _on_probe_cameras(self, max_index: int) -> None:
        self._stop_camera_probe_worker()
        worker = CameraProbeWorker(max_index=max_index)
        worker.result_ready.connect(self._on_camera_probe_result)
        worker.error.connect(self._on_worker_error)
        worker.state_changed.connect(lambda state: LOGGER.info("Camera probe state: %s", state))
        worker.finished.connect(self._on_camera_probe_finished)
        self._camera_probe_worker = worker
        self._calibration_panel.set_camera_probe_running(True)
        worker.start()
        self._set_status(f"Scanning cameras 0..{max_index} ...")

    def _on_camera_probe_result(self, payload: object) -> None:
        self._calibration_panel.set_camera_probe_running(False)
        cameras = payload if isinstance(payload, list) else []
        results: list[CameraProbeResult] = []
        for item in cameras:
            if isinstance(item, CameraProbeResult):
                results.append(item)
        self._detected_cameras = sorted(results, key=lambda camera: camera.index)
        self._calibration_panel.set_detected_cameras(results)
        if results:
            self._set_status(f"Detected {len(results)} camera(s).")
            self._seed_startup_source_slots()
        else:
            self._set_status("No cameras detected in probed range.")

    def _on_camera_probe_finished(self) -> None:
        self._calibration_panel.set_camera_probe_running(False)
        self._camera_probe_worker = None

    def _seed_startup_source_slots(self) -> None:
        try:
            sources = self._calibration_panel.current_sources()
        except ValueError:
            sources = [
                CameraSourceConfig(
                    source_id=f"cam{camera.index}",
                    kind="webcam",
                    uri=camera.index,
                    label=f"Webcam {camera.index}",
                )
                for camera in self._detected_cameras[:1]
            ]
        self._active_sources = sources
        self._calibration_panel.set_sources([source.source_id for source in sources])
        self._refresh_calibration_panel(force=True)

    def _load_existing_calibration(self) -> None:
        bundle = self._calibration_repo.load(self._calibration_path)
        if bundle is not None:
            self._apply_board_settings_from_bundle_metadata(bundle)
            self._apply_spatial_grid_from_bundle_metadata(bundle)
        self._set_current_calibration_bundle(bundle)
        if bundle is not None:
            self._set_status(f"Loaded calibration: {self._calibration_path.name}")

    def _set_current_calibration_bundle(self, bundle: CalibrationBundle | None) -> None:
        self._current_calibration_bundle = bundle
        self._calibration_loaded = bundle is not None
        self._refresh_calibration_panel(force=True)

    def _board_settings_from_metadata(self, metadata: dict[str, Any]) -> CalibrationBoardSettings | None:
        try:
            board = metadata.get("calibration_board")
            if isinstance(board, dict):
                active_type = str(board.get("type", "")).lower().strip()
                current = self._calibration_manager.board_settings()
                if active_type == "charuco":
                    squares = board.get("squares", [current.charuco_squares_x, current.charuco_squares_y])
                    return CalibrationBoardSettings(
                        chessboard_cols=current.chessboard_cols,
                        chessboard_rows=current.chessboard_rows,
                        chessboard_square_size_m=current.chessboard_square_size_m,
                        charuco_squares_x=int(squares[0]),
                        charuco_squares_y=int(squares[1]),
                        charuco_square_size_m=float(board.get("square_size_m", current.charuco_square_size_m)),
                        charuco_marker_size_m=float(board.get("marker_size_m", current.charuco_marker_size_m)),
                    )
                if active_type == "chessboard":
                    corners = board.get("inner_corners", [current.chessboard_cols, current.chessboard_rows])
                    return CalibrationBoardSettings(
                        chessboard_cols=int(corners[0]),
                        chessboard_rows=int(corners[1]),
                        chessboard_square_size_m=float(board.get("square_size_m", current.chessboard_square_size_m)),
                        charuco_squares_x=current.charuco_squares_x,
                        charuco_squares_y=current.charuco_squares_y,
                        charuco_square_size_m=current.charuco_square_size_m,
                        charuco_marker_size_m=current.charuco_marker_size_m,
                    )
                if active_type == "mixed":
                    chessboard = board.get("chessboard", {})
                    charuco = board.get("charuco", {})
                    corners = chessboard.get("inner_corners", [9, 6])
                    squares = charuco.get("squares", [5, 7])
                    return CalibrationBoardSettings(
                        chessboard_cols=int(corners[0]),
                        chessboard_rows=int(corners[1]),
                        chessboard_square_size_m=float(chessboard.get("square_size_m", 0.024)),
                        charuco_squares_x=int(squares[0]),
                        charuco_squares_y=int(squares[1]),
                        charuco_square_size_m=float(charuco.get("square_size_m", 0.077)),
                        charuco_marker_size_m=float(charuco.get("marker_size_m", 0.061)),
                    )

            board_shape = metadata.get("board_shape", [9, 6])
            return CalibrationBoardSettings(
                chessboard_cols=int(board_shape[0]),
                chessboard_rows=int(board_shape[1]),
                chessboard_square_size_m=float(metadata.get("square_size_m", 0.024)),
                charuco_squares_x=int(metadata.get("charuco_squares_x", 5)),
                charuco_squares_y=int(metadata.get("charuco_squares_y", 3)),
                charuco_square_size_m=float(metadata.get("charuco_square_size_m", 0.077)),
                charuco_marker_size_m=float(metadata.get("charuco_marker_size_m", 0.061)),
            )
        except (TypeError, ValueError, IndexError):
            return None

    def _apply_board_settings_from_bundle_metadata(self, bundle: CalibrationBundle) -> None:
        settings = self._board_settings_from_metadata(bundle.metadata)
        if settings is None:
            return
        self._calibration_manager.apply_board_settings(settings)
        self._calibration_panel.set_board_settings(self._calibration_manager.board_settings())
        self._calibration_panel.set_pattern_options(
            pattern_names=self._calibration_manager.available_patterns(),
            selected=self._calibration_pattern,
        )

    def _apply_spatial_grid_from_bundle_metadata(self, bundle: CalibrationBundle) -> None:
        try:
            spatial = bundle.metadata.get("spatial_coverage")
            if not isinstance(spatial, dict):
                return
            grid = spatial.get("grid")
            if not isinstance(grid, dict):
                return
            cols = int(grid.get("cols", self._calibration_manager.spatial_grid_shape[0]))
            rows = int(grid.get("rows", self._calibration_manager.spatial_grid_shape[1]))
        except (TypeError, ValueError):
            return
        self._calibration_manager.set_spatial_coverage_grid(cols=cols, rows=rows)
        self._calibration_panel.set_spatial_grid_values(cols, rows)

    def _set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, "Camera Calibration", message)

    def _show_error(self, message: str) -> None:
        LOGGER.error(message)
        QMessageBox.critical(self, "Camera Calibration", message)
        self._set_status(message)

    def _auto_navigate(self, destination: str) -> None:
        """Ask the panel to switch tabs automatically (no-op if unsupported/off)."""
        navigator = getattr(self._calibration_panel, "maybe_auto_navigate", None)
        if callable(navigator):
            navigator(destination)

    def _refresh_live_status(self, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_live_status_refresh_at < 0.5:
            return
        live_active = self._live_worker is not None and self._live_worker.isRunning()
        self._calibration_panel.set_live_status(
            live_active=live_active,
            active_cameras=self._active_camera_count,
        )
        self._last_live_status_refresh_at = now

    def _active_source_ids(self) -> list[str]:
        # Always prefer the configured source order so tiles keep a stable grid
        # position; falling back to sorted frame keys would reorder tiles.
        if self._active_sources:
            return [source.source_id for source in self._active_sources]
        try:
            configured = [source.source_id for source in self._calibration_panel.current_sources()]
        except ValueError:
            configured = []
        if configured:
            return configured
        if self._latest_frames:
            return sorted(self._latest_frames.keys())
        return []

    def _on_panel_sources_changed(self, sources_obj: object) -> None:
        sources = [source for source in sources_obj if isinstance(source, CameraSourceConfig)] if isinstance(sources_obj, list) else []
        live_active = self._live_worker is not None and self._live_worker.isRunning()
        if live_active:
            return
        self._active_sources = sources
        source_ids = {source.source_id for source in sources}
        self._latest_frames = {source_id: frame for source_id, frame in self._latest_frames.items() if source_id in source_ids}
        self._latest_calibration_detections = {
            source_id: detection
            for source_id, detection in self._latest_calibration_detections.items()
            if source_id in source_ids
        }
        self._last_rendered_frame_indices = {
            source_id: frame_index
            for source_id, frame_index in self._last_rendered_frame_indices.items()
            if source_id in source_ids
        }
        self._refresh_calibration_panel(force=True)

    def _refresh_calibration_panel(self, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_calibration_panel_refresh_at < self._calibration_panel_refresh_interval_sec:
            return

        source_ids = self._active_source_ids()
        self._calibration_panel.set_sources(source_ids)
        sample_counts = self._calibration_manager.observations_summary(include_sync_only=False)
        sample_breakdown = self._calibration_manager.observations_breakdown_summary()
        self._calibration_panel.update_camera_status_table(
            source_ids=source_ids,
            sample_counts=sample_counts,
            sample_breakdown=sample_breakdown,
            bundle=self._current_calibration_bundle,
            live_detection=self._latest_calibration_detections,
        )

        mode = self._calibration_workflow_mode()
        warnings: list[str] = []
        if not source_ids:
            warnings.append("Configure at least one camera source before capturing calibration samples.")
        sync_target = self._calibration_panel.auto_capture_max_samples()
        sync_required = sync_target if sync_target > 0 else 3
        for source_id in source_ids:
            if mode == "sync_extrinsics":
                sync_count_for_source = int(sample_breakdown.get(source_id, {}).get("synchronized", 0))
                if sync_count_for_source < sync_required:
                    warnings.append(
                        f"{source_id}: too few synchronized sets ({sync_count_for_source}/{sync_required}). "
                        "Every camera must share views with the others for reliable extrinsics."
                    )
            else:
                count = sample_counts.get(source_id, 0)
                if count < self._calibration_manager.min_samples_per_camera:
                    warnings.append(
                        f"{source_id}: too few frames ({count}/{self._calibration_manager.min_samples_per_camera})."
                    )
        if self._current_calibration_bundle:
            warnings.extend(self._current_calibration_bundle.notes)
        if self._calibration_pattern == "charuco" and "charuco" not in self._calibration_manager.available_patterns():
            warnings.append("Charuco selected but cv2.aruco is unavailable in current OpenCV build.")
        sync_count = self._calibration_manager.synchronized_capture_count()
        if sync_count > 0:
            warnings.append(f"Synchronized capture sets stored: {sync_count}.")
        if mode == "sync_extrinsics":
            warnings.append(
                "Workflow mode: Sync / Extrinsics. Captures are stored only when >=2 cameras see a valid board."
            )
        else:
            warnings.append(
                "Workflow mode: Intrinsics. Captures are stored per camera using the configured intrinsics thresholds."
            )
        warnings.append(
            "Intrinsics thresholds: "
            f"quality >= {self._calibration_manager.min_quality_score:.2f}, "
            f"coverage >= {self._calibration_manager.min_coverage_ratio * 100.0:.1f}%."
        )
        warnings.append(
            "Sync thresholds: "
            f"quality >= {self._calibration_manager.sync_min_quality_score:.2f}, "
            f"coverage >= {self._calibration_manager.sync_min_coverage_ratio * 100.0:.1f}%."
        )
        self._calibration_panel.show_warnings(list(dict.fromkeys(warnings)))
        if self._calibration_panel.auto_capture_enabled():
            self._calibration_panel.set_auto_capture_status(self._auto_capture_idle_text())
        else:
            self._calibration_panel.set_auto_capture_status("Auto capture off.")
        self._last_calibration_panel_refresh_at = now

    def _calibration_workflow_mode(self) -> str:
        return self._calibration_panel.current_workflow_mode()

    def _refresh_threshold_controls_for_mode(self) -> None:
        if self._calibration_workflow_mode() == "sync_extrinsics":
            self._calibration_panel.set_acceptance_threshold_values(
                min_quality=self._calibration_manager.sync_min_quality_score,
                min_coverage_ratio=self._calibration_manager.sync_min_coverage_ratio,
            )
            return
        self._calibration_panel.set_acceptance_threshold_values(
            min_quality=self._calibration_manager.min_quality_score,
            min_coverage_ratio=self._calibration_manager.min_coverage_ratio,
        )

    def _auto_capture_idle_text(self) -> str:
        limit = self._calibration_panel.auto_capture_max_samples()
        limit_text = f" Max {limit}." if limit > 0 else ""
        collection = self._calibration_manager.sample_collection_metadata()
        duration_sec = float(collection.get("duration_sec", 0.0) or 0.0)
        duration_text = f" Collected for {self._format_duration_sec(duration_sec)}." if duration_sec > 0 else ""
        if self._calibration_workflow_mode() == "sync_extrinsics":
            goal_text = ""
            if limit > 0:
                counts = self._synchronized_counts_by_source()
                if counts:
                    progress = ", ".join(f"{sid}:{count}/{limit}" for sid, count in sorted(counts.items()))
                    goal_text = f" Goal: every camera needs {limit} synchronized set(s). Progress {progress}."
                    lagging = sorted(sid for sid, count in counts.items() if count < limit)
                    if lagging:
                        goal_text += f" Still need shared views for: {', '.join(lagging)}."
            return (
                "Auto capture armed (sync mode). Hold the board so it is visible in as many "
                "cameras at once as possible; every camera must share views with the others."
                + goal_text
                + duration_text
            )
        target_text = ""
        if limit > 0:
            target_text = f" Target {self._spatial_target_samples_per_cell()} sample(s) per grid cell."
        return (
            "Auto capture armed (intrinsics mode). Move the board through new per-camera poses."
            + limit_text
            + target_text
            + duration_text
        )

    def _format_duration_sec(self, duration_sec: float) -> str:
        total_sec = max(0, int(round(duration_sec)))
        minutes, seconds = divmod(total_sec, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:d}:{seconds:02d}"

    def _synchronized_counts_by_source(self) -> dict[str, int]:
        """Synchronized-set count per active camera (sets shared with >=1 other camera)."""
        breakdown = self._calibration_manager.observations_breakdown_summary()
        return {
            source_id: int(breakdown.get(source_id, {}).get("synchronized", 0))
            for source_id in self._active_source_ids()
        }

    def _auto_capture_stop_message_if_limit_reached(self) -> str | None:
        limit = self._calibration_panel.auto_capture_max_samples()
        if limit <= 0:
            return None
        if self._calibration_workflow_mode() == "sync_extrinsics":
            source_ids = self._active_source_ids()
            if len(source_ids) < 2:
                return None
            # The target only counts as complete once *every* camera has reached it,
            # so a rig can't finish while one camera never shared a view with the others.
            per_source = self._synchronized_counts_by_source()
            if per_source and all(count >= limit for count in per_source.values()):
                coverage_text = ", ".join(f"{sid}={count}" for sid, count in sorted(per_source.items()))
                return (
                    f"Auto capture stopped: every camera reached {limit} synchronized set(s) "
                    f"({coverage_text})."
                )
            return None

        source_ids = self._active_source_ids()
        if not source_ids:
            return None
        if all(self._source_spatial_grid_complete(source_id) for source_id in source_ids):
            target = self._spatial_target_samples_per_cell()
            coverage_text = ", ".join(
                f"{source_id}=complete"
                for source_id in source_ids
            )
            return (
                f"Auto capture stopped: all overlay grid cells reached "
                f"{target}/{target} ({coverage_text})."
            )
        return None

    def _stop_auto_capture_if_limit_reached(self) -> bool:
        message = self._auto_capture_stop_message_if_limit_reached()
        if message is None:
            return False
        self._calibration_panel.set_auto_capture_enabled(False)
        self._calibration_panel.set_auto_capture_status(message)
        self._calibration_panel.show_feedback(message, success=True)
        self._set_status(message)
        return True

    def _auto_capture_intrinsics_candidates(self, source_ids: list[str]) -> list[str]:
        limit = self._calibration_panel.auto_capture_max_samples()
        if limit <= 0 or self._calibration_workflow_mode() != "intrinsics":
            return list(source_ids)
        return [
            source_id
            for source_id in source_ids
            if not self._source_spatial_grid_complete(source_id)
        ]

    def _source_spatial_grid_complete(self, source_id: str) -> bool:
        if self._calibration_panel.auto_capture_max_samples() <= 0:
            return False
        target = self._spatial_target_samples_per_cell()
        summary = self._calibration_manager.spatial_coverage_summary(
            source_id,
            include_sync_only=False,
            include_sample_summaries=False,
            target_samples_per_cell=target,
        )
        hit_counts = summary.get("credited_cell_hit_counts", [])
        if not isinstance(hit_counts, list) or not hit_counts:
            return False
        for row_counts in hit_counts:
            if not isinstance(row_counts, list) or not row_counts:
                return False
            for hit_count in row_counts:
                if int(hit_count) < target:
                    return False
        return True

    def _update_calibration_preview(self, force: bool = False) -> None:
        if not self._latest_frames:
            return
        now = time.perf_counter()
        overlay_enabled = self._calibration_panel.overlay_enabled()
        use_qt_overlay = self._uses_qt_preview_overlay()
        detection_needed = overlay_enabled or self._calibration_panel.auto_capture_enabled()
        detection_due = detection_needed and (
            force or now - self._last_calibration_detection_at >= self._calibration_detection_interval_sec
        )
        frame_indices = {
            source_id: frame.frame_index
            for source_id, frame in self._latest_frames.items()
        }
        if not force and not detection_due and frame_indices == self._last_rendered_frame_indices:
            return

        sample_counts = self._calibration_manager.observations_summary(include_sync_only=False)
        detections = dict(self._latest_calibration_detections)

        if detection_due and not self._detection_request_in_flight:
            # Hand detection to the background worker and keep rendering with the
            # most recent detections. The result is applied asynchronously in
            # _on_detection_result, which also drives auto-capture. The frames
            # snapshot lets that step pair the detected corners with the exact
            # frames they came from. Undistort happens here (cheap cached remap)
            # so detection sees the same preview frame it did before.
            previews = {
                source_id: self._prepare_calibration_preview_frame(source_id, frame.frame_bgr)
                for source_id, frame in self._latest_frames.items()
            }
            self._last_calibration_detection_at = now
            self._detection_request_in_flight = True
            self.request_detection.emit(
                {
                    "frames": previews,
                    "frames_snapshot": dict(self._latest_frames),
                    "pattern": self._calibration_pattern,
                }
            )

        if not detection_needed and self._latest_calibration_detections:
            self._latest_calibration_detections.clear()
            detections = {}
            self._refresh_calibration_panel(force=True)

        if use_qt_overlay and len(self._latest_frames) <= INLINE_PREVIEW_MAX_CAMERAS:
            # Few cameras: prepare the display frame on the UI thread. The work
            # is small and this avoids the render-worker round-trip latency, so
            # the live view feels immediate. The Qt overlay is drawn by the
            # canvas from the detections/overlay state.
            display_previews = {
                source_id: self._downscale_for_display(
                    self._display_calibration_preview_frame(
                        source_id,
                        self._prepare_calibration_preview_frame(source_id, frame.frame_bgr),
                    )
                )
                for source_id, frame in self._latest_frames.items()
            }
            overlay_states = self._build_preview_overlay_states(detections, sample_counts)
            self._update_preview_panel(display_previews, detections, sample_counts, overlay_states)
            self._last_rendered_frame_indices = frame_indices
        elif use_qt_overlay:
            # Many cameras: offload display prep to the preview-render worker;
            # results are applied in _on_preview_render_result.
            self._submit_preview_render(frame_indices)
        else:
            # Legacy synchronous path: cv2-bakes the overlay onto the frame.
            previews = {
                source_id: self._prepare_calibration_preview_frame(source_id, frame.frame_bgr)
                for source_id, frame in self._latest_frames.items()
            }
            if overlay_enabled:
                for source_id, preview in list(previews.items()):
                    detection = detections.get(source_id)
                    if detection is not None and self._calibration_panel.overlay_enabled_for(source_id):
                        previews[source_id] = self._draw_calibration_preview_overlay(
                            source_id=source_id,
                            frame_bgr=preview,
                            detection=detection,
                            sample_count=sample_counts.get(source_id, 0),
                        )
            display_previews = self._finalize_calibration_preview_frames(
                previews,
                detections,
                overlay_baked=overlay_enabled,
            )
            self._update_preview_panel(display_previews, detections, sample_counts, None)
            self._last_rendered_frame_indices = frame_indices

        if detection_due or force:
            self._refresh_calibration_panel()

    def _on_detection_result(self, payload: object) -> None:
        """Apply detection results produced by the background worker.

        Runs on the UI thread, so all sample capture and manager mutation stay
        single-threaded. Auto-capture uses the frames the detection was computed
        on (``frames_snapshot``) so the stored corners and the capture frames
        always belong to the same instant. The overlay/preview picks up the new
        detections on the next display tick.
        """
        self._detection_request_in_flight = False
        # A result can land just after live capture stopped; _latest_frames is
        # only empty when not live, so drop the stale detection in that case.
        if not self._latest_frames:
            return
        try:
            data = dict(payload)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        detections = data.get("detections") or {}
        self._latest_calibration_detections = detections
        frames_snapshot = data.get("frames_snapshot") or {}
        if self._maybe_auto_capture_calibration(detections, frames=frames_snapshot):
            return
        self._refresh_calibration_panel()

    def _submit_preview_render(self, frame_indices: dict[str, int]) -> None:
        """Hand the latest raw frames + display options to the render worker."""
        if self._render_request_in_flight or self._render_worker is None:
            return
        frames = {source_id: frame.frame_bgr for source_id, frame in self._latest_frames.items()}
        if not frames:
            return
        undistort = {
            source_id: self._calibration_panel.undistort_enabled_for(source_id) for source_id in frames
        }
        mirror = {
            source_id: self._calibration_panel.mirror_preview_enabled_for(source_id)
            for source_id in frames
        }
        self._render_request_in_flight = True
        self._last_rendered_frame_indices = dict(frame_indices)
        self.request_preview_render.emit(
            {
                "frames": frames,
                "undistort": undistort,
                "mirror": mirror,
                "bundle": self._current_calibration_bundle,
                "max_width": int(getattr(self._runtime_tuning, "preview_max_width", 0) or 0),
                "max_height": int(getattr(self._runtime_tuning, "preview_max_height", 0) or 0),
                "frame_indices": dict(frame_indices),
            }
        )

    def _on_preview_render_result(self, payload: object) -> None:
        """Display preview images prepared by the render worker (UI thread)."""
        self._render_request_in_flight = False
        if not self._latest_frames:
            return
        try:
            data = dict(payload)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        images = data.get("images") or {}
        if not images or not hasattr(self._calibration_panel, "update_preview_images"):
            return
        sample_counts = self._calibration_manager.observations_summary(include_sync_only=False)
        detections = dict(self._latest_calibration_detections)
        overlay_states = self._build_preview_overlay_states(detections, sample_counts)
        self._calibration_panel.update_preview_images(
            images, detections, sample_counts, overlay_states
        )

    def _uses_qt_preview_overlay(self) -> bool:
        flag = getattr(self._calibration_panel, "uses_qt_preview_overlay", None)
        return bool(flag()) if callable(flag) else False

    def _update_preview_panel(
        self,
        preview_frames: dict[str, Any],
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
        overlay_states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if overlay_states is not None and self._uses_qt_preview_overlay():
            self._calibration_panel.update_previews(preview_frames, detections, sample_counts, overlay_states)
            return
        self._calibration_panel.update_previews(preview_frames, detections, sample_counts)

    def _build_preview_overlay_states(
        self,
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
        accepted_by_source: dict[str, bool | None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        accepted_by_source = accepted_by_source or {}
        target = self._spatial_target_samples_per_cell()
        states: dict[str, dict[str, Any]] = {}
        for source_id, detection in detections.items():
            try:
                summary = self._calibration_manager.spatial_coverage_summary(
                    source_id,
                    include_sync_only=False,
                    include_sample_summaries=False,
                    target_samples_per_cell=target,
                )
            except Exception:  # noqa: BLE001 - preview metadata must never stop video
                summary = {}
            states[source_id] = {
                "overlay_enabled": self._calibration_panel.overlay_enabled_for(source_id),
                "overlay_scale": self._overlay_scale(),
                "mirror": self._calibration_panel.mirror_preview_enabled_for(source_id),
                "sample_count": int(sample_counts.get(source_id, 0)),
                "accepted": accepted_by_source.get(source_id),
                "target_samples_per_cell": target,
                "grid_shape": tuple(self._calibration_manager.spatial_grid_shape),
                "hit_counts": summary.get("credited_cell_hit_counts", []),
                "visited_cells": int(summary.get("credited_visited_cells", 0) or 0),
                "total_cells": int(summary.get("total_cells", 0) or 0),
                "coverage_ratio": float(summary.get("credited_grid_coverage_ratio", 0.0) or 0.0),
                "detection_found": bool(detection.found),
            }
        return states

    def _prepare_calibration_preview_frame(self, source_id: str, frame_bgr: Any) -> Any:
        if not self._calibration_panel.undistort_enabled_for(source_id):
            return frame_bgr
        return self._calibration_manager.undistort_frame(
            source_id=source_id,
            frame_bgr=frame_bgr,
            bundle=self._current_calibration_bundle,
        )

    def _display_calibration_preview_frame(self, source_id: str, frame_bgr: Any) -> Any:
        if not self._calibration_panel.mirror_preview_enabled_for(source_id):
            return frame_bgr
        return cv2.flip(frame_bgr, 1)

    def _overlay_scale(self) -> float:
        try:
            return max(0.1, float(getattr(self._config, "overlay_scale", 1.0)))
        except (TypeError, ValueError):
            return 1.0

    def _downscale_for_display(self, frame_bgr: Any) -> Any:
        """Shrink a frame to the preview resolution for display only.

        Detection, calibration and recording use the full capture-resolution
        frame; this only reduces the cost of rendering the on-screen preview.
        """
        max_width = int(getattr(self._runtime_tuning, "preview_max_width", 0) or 0)
        max_height = int(getattr(self._runtime_tuning, "preview_max_height", 0) or 0)
        if max_width <= 0 and max_height <= 0:
            return frame_bgr
        height, width = frame_bgr.shape[:2]
        if width <= 0 or height <= 0:
            return frame_bgr
        scale_candidates: list[float] = []
        if max_width > 0:
            scale_candidates.append(max_width / float(width))
        if max_height > 0:
            scale_candidates.append(max_height / float(height))
        scale = min(scale_candidates) if scale_candidates else 1.0
        if scale >= 1.0:
            return frame_bgr
        target_width = max(1, int(round(width * scale)))
        target_height = max(1, int(round(height * scale)))
        return cv2.resize(frame_bgr, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def _finalize_calibration_preview_frames(
        self,
        frames_by_source: dict[str, Any],
        detections: dict[str, ChessboardDetectionResult],
        overlay_baked: bool,
    ) -> dict[str, Any]:
        finalized: dict[str, Any] = {}
        for source_id, frame_bgr in frames_by_source.items():
            if overlay_baked and self._calibration_panel.overlay_enabled_for(source_id) and source_id in detections:
                finalized[source_id] = frame_bgr
            else:
                display = self._display_calibration_preview_frame(source_id, frame_bgr)
                finalized[source_id] = self._downscale_for_display(display)
        return finalized

    def _draw_calibration_preview_overlay(
        self,
        source_id: str,
        frame_bgr: Any,
        detection: ChessboardDetectionResult,
        sample_count: int | None = None,
        accepted: bool | None = None,
    ) -> Any:
        mirror_preview = self._calibration_panel.mirror_preview_enabled_for(source_id)
        display_frame = self._downscale_for_display(
            self._display_calibration_preview_frame(source_id, frame_bgr)
        )
        display_detection = self._display_detection_for_preview(
            detection=detection,
            source_frame_bgr=frame_bgr,
            display_frame_bgr=display_frame,
            mirror_preview=mirror_preview,
        )
        return self._compose_cached_calibration_overlay(
            source_id=source_id,
            display_frame_bgr=display_frame,
            detection=display_detection,
            sample_count=sample_count,
            accepted=accepted,
            mirror_preview=mirror_preview,
        )

    def _compose_cached_calibration_overlay(
        self,
        source_id: str,
        display_frame_bgr: Any,
        detection: ChessboardDetectionResult,
        sample_count: int | None = None,
        accepted: bool | None = None,
        mirror_preview: bool = False,
    ) -> Any:
        key = self._calibration_overlay_cache_key(
            display_frame_bgr=display_frame_bgr,
            detection=detection,
            sample_count=sample_count,
            accepted=accepted,
            mirror_preview=mirror_preview,
        )
        cached = self._calibration_overlay_cache.get(source_id)
        if cached is None or cached[0] != key:
            overlay_bgr, alpha = self._build_calibration_overlay_layer(
                display_frame_bgr=display_frame_bgr,
                detection=detection,
                sample_count=sample_count,
                accepted=accepted,
                mirror_preview=mirror_preview,
            )
            cached = (key, overlay_bgr, alpha)
            self._calibration_overlay_cache[source_id] = cached

        _key, overlay_bgr, alpha = cached
        return self._blend_calibration_overlay(display_frame_bgr, overlay_bgr, alpha)

    def _calibration_overlay_cache_key(
        self,
        display_frame_bgr: Any,
        detection: ChessboardDetectionResult,
        sample_count: int | None,
        accepted: bool | None,
        mirror_preview: bool,
    ) -> tuple[Any, ...]:
        height, width = display_frame_bgr.shape[:2]
        corners_sig: tuple[float, ...] = ()
        if detection.corners is not None:
            corners_sig = tuple(float(value) for value in np.round(detection.corners.reshape(-1), 1))
        bbox_sig = tuple(round(float(value), 1) for value in detection.board_bbox_px or ())
        center_sig = tuple(round(float(value), 1) for value in detection.board_center_px or ())
        diagnostics_sig = tuple(detection.diagnostics[:3])
        return (
            int(width),
            int(height),
            detection.source_id,
            detection.pattern_type,
            bool(detection.found),
            int(detection.detected_corners),
            round(float(detection.quality_score), 3),
            round(float(detection.coverage_ratio), 4),
            round(float(detection.sharpness_score), 3),
            int(sample_count if sample_count is not None else -1),
            accepted,
            bool(mirror_preview),
            self._spatial_target_samples_per_cell(),
            tuple(self._calibration_manager.spatial_grid_shape),
            round(self._overlay_scale(), 3),
            bbox_sig,
            center_sig,
            diagnostics_sig,
            corners_sig,
        )

    def _build_calibration_overlay_layer(
        self,
        display_frame_bgr: Any,
        detection: ChessboardDetectionResult,
        sample_count: int | None,
        accepted: bool | None,
        mirror_preview: bool,
    ) -> tuple[Any, Any]:
        blank = np.zeros_like(display_frame_bgr)
        overlay_bgr = self._calibration_manager.draw_detection_overlay(
            blank,
            detection=detection,
            accepted=accepted,
            sample_count=sample_count,
            mirror_x=mirror_preview,
            spatial_target_samples_per_cell=self._spatial_target_samples_per_cell(),
            overlay_scale=self._overlay_scale(),
        )
        alpha = np.max(overlay_bgr, axis=2).astype(np.float32) / 255.0
        band_height = max(0, int(overlay_bgr.shape[0] - display_frame_bgr.shape[0]))
        if band_height > 0:
            alpha[:band_height, :] = 1.0
        if band_height < alpha.shape[0]:
            frame_overlay = overlay_bgr[band_height:, :, :]
            frame_alpha = alpha[band_height:, :]
            nonzero = np.any(frame_overlay > 0, axis=2)
            frame_alpha[nonzero] = np.maximum(frame_alpha[nonzero], 0.18)
        return overlay_bgr, np.clip(alpha, 0.0, 1.0)

    def _blend_calibration_overlay(self, display_frame_bgr: Any, overlay_bgr: Any, alpha: Any) -> Any:
        height, width = display_frame_bgr.shape[:2]
        band_height = max(0, int(overlay_bgr.shape[0] - height))
        if overlay_bgr.shape[1] != width or overlay_bgr.shape[0] < height:
            return display_frame_bgr

        canvas = np.zeros_like(overlay_bgr)
        canvas[band_height:band_height + height, :width] = display_frame_bgr
        alpha_3 = alpha[:, :, None].astype(np.float32)
        blended = overlay_bgr.astype(np.float32) * alpha_3 + canvas.astype(np.float32) * (1.0 - alpha_3)
        return np.clip(blended, 0, 255).astype(np.uint8)

    def _display_detection_for_preview(
        self,
        detection: ChessboardDetectionResult,
        source_frame_bgr: Any,
        display_frame_bgr: Any,
        mirror_preview: bool,
    ) -> ChessboardDetectionResult:
        transformed = (
            self._mirror_detection_for_preview(detection, source_frame_bgr)
            if mirror_preview
            else detection
        )
        try:
            target_height, target_width = display_frame_bgr.shape[:2]
        except (AttributeError, IndexError, TypeError):
            return transformed
        return self._scale_detection_for_display(
            detection=transformed,
            target_size=(int(target_width), int(target_height)),
        )

    def _scale_detection_for_display(
        self,
        detection: ChessboardDetectionResult,
        target_size: tuple[int, int],
    ) -> ChessboardDetectionResult:
        source_width, source_height = detection.image_size
        target_width, target_height = target_size
        if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
            return detection

        scale_x = float(target_width) / float(source_width)
        scale_y = float(target_height) / float(source_height)
        if scale_x == 1.0 and scale_y == 1.0:
            return detection

        corners = None
        if detection.corners is not None:
            corners = detection.corners.copy()
            corners[..., 0] *= scale_x
            corners[..., 1] *= scale_y

        bbox = detection.board_bbox_px
        scaled_bbox = None
        if bbox is not None:
            x_px, y_px, box_width, box_height = bbox
            scaled_bbox = (
                float(x_px) * scale_x,
                float(y_px) * scale_y,
                float(box_width) * scale_x,
                float(box_height) * scale_y,
            )

        center = detection.board_center_px
        scaled_center = None
        if center is not None:
            scaled_center = (float(center[0]) * scale_x, float(center[1]) * scale_y)

        return ChessboardDetectionResult(
            source_id=detection.source_id,
            found=detection.found,
            image_size=(int(target_width), int(target_height)),
            pattern_type=detection.pattern_type,
            corners=corners,
            charuco_ids=detection.charuco_ids.copy() if detection.charuco_ids is not None else None,
            detected_corners=detection.detected_corners,
            quality_score=detection.quality_score,
            coverage_ratio=detection.coverage_ratio,
            sharpness_score=detection.sharpness_score,
            board_bbox_px=scaled_bbox,
            board_center_px=scaled_center,
            diagnostics=list(detection.diagnostics),
        )

    def _spatial_target_samples_per_cell(self) -> int:
        max_samples = self._calibration_panel.auto_capture_max_samples()
        cols, rows = self._calibration_manager.spatial_grid_shape
        total_cells = max(1, int(cols) * int(rows))
        if max_samples <= 0:
            return 3
        return max(1, (int(max_samples) + total_cells - 1) // total_cells)

    def _detection_needs_spatial_cells(
        self,
        source_id: str,
        detection: ChessboardDetectionResult,
    ) -> bool:
        if self._calibration_panel.auto_capture_max_samples() <= 0:
            return True
        if not detection.found or detection.corners is None:
            return True

        target = self._spatial_target_samples_per_cell()
        cells = self._detection_spatial_cells(detection)
        if not cells:
            return True

        summary = self._calibration_manager.spatial_coverage_summary(
            source_id,
            include_sync_only=False,
            include_sample_summaries=False,
            target_samples_per_cell=target,
        )
        hit_counts = summary.get("credited_cell_hit_counts", [])
        try:
            return any(int(hit_counts[row][col]) < target for row, col in cells)  # type: ignore[index]
        except (TypeError, IndexError, ValueError):
            return True

    def _detection_spatial_cells(self, detection: ChessboardDetectionResult) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        if detection.corners is None:
            return cells

        points = detection.corners.reshape(-1, 2)
        for point in points:
            cells.add(
                self._point_to_spatial_grid_cell(
                    float(point[0]),
                    float(point[1]),
                    detection.image_size,
                )
            )

        bbox = detection.board_bbox_px
        if bbox is not None:
            bbox_x, bbox_y, bbox_w, bbox_h = bbox
            for point_x, point_y in (
                (bbox_x, bbox_y),
                (bbox_x + bbox_w, bbox_y),
                (bbox_x, bbox_y + bbox_h),
                (bbox_x + bbox_w, bbox_y + bbox_h),
            ):
                cells.add(
                    self._point_to_spatial_grid_cell(
                        float(point_x),
                        float(point_y),
                        detection.image_size,
                    )
                )

        center = detection.board_center_px
        if center is None and points.size:
            min_x = float(points[:, 0].min())
            max_x = float(points[:, 0].max())
            min_y = float(points[:, 1].min())
            max_y = float(points[:, 1].max())
            center = (min_x + (max_x - min_x) * 0.5, min_y + (max_y - min_y) * 0.5)
        if center is not None:
            cells.add(
                self._point_to_spatial_grid_cell(
                    float(center[0]),
                    float(center[1]),
                    detection.image_size,
                )
            )
        return cells

    def _detection_center_cell(self, detection: ChessboardDetectionResult) -> tuple[int, int] | None:
        if detection.corners is None:
            return None

        center = detection.board_center_px
        if center is None:
            points = detection.corners.reshape(-1, 2)
            if points.size:
                min_x = float(points[:, 0].min())
                max_x = float(points[:, 0].max())
                min_y = float(points[:, 1].min())
                max_y = float(points[:, 1].max())
                center = (min_x + (max_x - min_x) * 0.5, min_y + (max_y - min_y) * 0.5)
        if center is not None:
            return self._point_to_spatial_grid_cell(float(center[0]), float(center[1]), detection.image_size)
        return None

    def _point_to_spatial_grid_cell(
        self,
        x_px: float,
        y_px: float,
        image_size: tuple[int, int],
    ) -> tuple[int, int]:
        width, height = image_size
        cols, rows = self._calibration_manager.spatial_grid_shape
        safe_width = max(float(width), 1.0)
        safe_height = max(float(height), 1.0)
        col = min(max(int(x_px * cols / safe_width), 0), cols - 1)
        row = min(max(int(y_px * rows / safe_height), 0), rows - 1)
        return row, col

    def _mirror_detection_for_preview(
        self,
        detection: ChessboardDetectionResult,
        frame_bgr: Any,
    ) -> ChessboardDetectionResult:
        try:
            width = int(frame_bgr.shape[1])
        except (AttributeError, IndexError, TypeError):
            return detection

        corners = None
        if detection.corners is not None:
            corners = detection.corners.copy()
            corners[..., 0] = float(width - 1) - corners[..., 0]

        bbox = detection.board_bbox_px
        mirrored_bbox = None
        if bbox is not None:
            x_px, y_px, box_width, box_height = bbox
            mirrored_bbox = (
                max(0.0, float(width) - float(x_px) - float(box_width)),
                float(y_px),
                float(box_width),
                float(box_height),
            )

        center = detection.board_center_px
        mirrored_center = None
        if center is not None:
            mirrored_center = (float(width - 1) - float(center[0]), float(center[1]))

        return ChessboardDetectionResult(
            source_id=detection.source_id,
            found=detection.found,
            image_size=detection.image_size,
            pattern_type=detection.pattern_type,
            corners=corners,
            charuco_ids=detection.charuco_ids.copy() if detection.charuco_ids is not None else None,
            detected_corners=detection.detected_corners,
            quality_score=detection.quality_score,
            coverage_ratio=detection.coverage_ratio,
            sharpness_score=detection.sharpness_score,
            board_bbox_px=mirrored_bbox,
            board_center_px=mirrored_center,
            diagnostics=list(detection.diagnostics),
        )

    def _on_start_live(
        self,
        sources: list[CameraSourceConfig],
        target_fps: float,
    ) -> None:
        self._on_stop_live()
        self._stop_camera_probe_worker()
        self._on_runtime_tuning_changed(self._calibration_panel.runtime_tuning())

        self._active_sources = sources
        self._latest_frames.clear()
        self._latest_calibration_detections.clear()
        self._last_rendered_frame_indices.clear()
        self._last_calibration_detection_at = 0.0
        source_ids = [source.source_id for source in sources]
        self._calibration_panel.set_sources(source_ids)
        self._active_camera_count = len(sources)

        worker = LiveCaptureWorker(
            sources=sources,
            target_fps=self._runtime_tuning.capture_fps if self._runtime_tuning.capture_fps > 0 else target_fps,
            requested_width=self._runtime_tuning.capture_width,
            requested_height=self._runtime_tuning.capture_height,
        )
        worker.batch_ready.connect(self._on_frame_batch)
        worker.state_changed.connect(self._on_live_state_changed)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_live_finished)
        self._live_worker = worker
        worker.start()
        self._refresh_live_status(force=True)
        self._refresh_calibration_panel(force=True)
        self._set_status(
            f"Starting live capture ({len(sources)} sources, "
            f"{self._runtime_tuning.capture_fps:.1f} FPS, "
            f"capture={self._runtime_tuning.capture_width or 'auto'}x{self._runtime_tuning.capture_height or 'auto'}, "
            f"preview<={self._preview_resolution_status_text()})..."
        )

    def _preview_resolution_status_text(self) -> str:
        width = int(getattr(self._runtime_tuning, "preview_max_width", 0) or 0)
        height = int(getattr(self._runtime_tuning, "preview_max_height", 0) or 0)
        if width <= 0 and height <= 0:
            return "auto"
        if height <= 0:
            return f"{width}px wide"
        if width <= 0:
            return f"{height}px high"
        return f"{width}x{height}"

    def _on_live_state_changed(self, state: str) -> None:
        self._refresh_live_status(force=True)
        if state == "live_started":
            self._set_status("Live capture started")
        elif state == "live_stopped":
            self._set_status("Live capture stopped")
        else:
            self._set_status(state)

    def _on_live_finished(self) -> None:
        self._finalize_recording()
        if self._live_worker is not None and not self._live_worker.isRunning():
            self._live_worker = None
        self._active_sources = []
        self._active_camera_count = 0
        self._refresh_live_status(force=True)

    def _on_stop_live(self) -> None:
        self._finalize_recording()
        if self._live_worker is None:
            self._refresh_live_status(force=True)
            return
        self._live_worker.stop()
        if not self._live_worker.wait(3000):
            LOGGER.warning("Live capture worker did not stop in time; forcing termination.")
            self._live_worker.terminate()
            self._live_worker.wait(1000)
        self._live_worker = None
        self._active_sources = []
        self._latest_frames.clear()
        self._latest_calibration_detections.clear()
        self._last_rendered_frame_indices.clear()
        # Drop any in-flight worker gates so the next live session can submit
        # immediately even if a result is still pending for the old frames.
        self._detection_request_in_flight = False
        self._render_request_in_flight = False
        self._active_camera_count = 0
        self._refresh_live_status(force=True)
        self._refresh_calibration_panel(force=True)
        self._set_status("Live capture stopped")

    def _default_recordings_base_dir(self) -> Path:
        # config paths are normalized to the project root, so this stays inside
        # the project regardless of any absolute paths in app_settings.json.
        return self._config.app_root / "recordings"

    def _on_record_toggled(self, enabled: bool) -> None:
        if not enabled:
            self._finalize_recording()
            return
        if self._video_recorder is not None:
            return
        if self._live_worker is None or not self._live_worker.isRunning():
            self._calibration_panel.set_recording_active(False)
            self._show_warning("Start eerst de live weergave voordat je een opname maakt.")
            return

        # No prompt at start: always record into the default "recordings" folder.
        # The save location / rename / delete options are offered when the
        # recording is stopped (see _handle_recording_result).
        base_dir = self._default_recordings_base_dir()
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOGGER.error("Could not create recordings folder: %s", exc)
            self._calibration_panel.set_recording_active(False)
            self._show_error(f"Kon de opnamemap niet aanmaken: {exc}")
            return
        self._last_recording_dir = base_dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = base_dir / f"rec_{timestamp}"
        labels = {source.source_id: (source.label or source.source_id) for source in self._active_sources}
        fps = self._runtime_tuning.capture_fps if self._runtime_tuning.capture_fps > 0 else self._calibration_panel.target_fps()
        try:
            self._video_recorder = VideoRecorder(output_dir=output_dir, fps=fps, labels=labels)
        except OSError as exc:
            LOGGER.error("Could not start recording: %s", exc)
            self._calibration_panel.set_recording_active(False)
            self._show_error(f"Kon de opname niet starten: {exc}")
            return
        self._live_worker.attach_recorder(self._video_recorder)
        self._calibration_panel.set_recording_active(True)
        self._calibration_panel.show_feedback(
            f"Opname gestart (volledige capture-resolutie) -> {output_dir}", success=True
        )
        self._set_status(f"Opname gestart: {output_dir}")

    def _finalize_recording(self) -> None:
        recorder = self._video_recorder
        self._video_recorder = None
        if recorder is None:
            return
        # Stop the worker thread from writing before we release the writers.
        if self._live_worker is not None and hasattr(self._live_worker, "detach_recorder"):
            self._live_worker.detach_recorder()
        self._calibration_panel.set_recording_active(False)
        written = recorder.close()
        if not written:
            self._calibration_panel.show_feedback("Opname gestopt; geen frames opgeslagen.", success=False)
            self._set_status("Opname gestopt (geen frames).")
            return
        self._handle_recording_result(recorder.output_dir, written, recorder.total_frames())

    def _handle_recording_result(self, output_dir: Path, written: dict[str, Path], total_frames: int) -> None:
        files_text = ", ".join(path.name for path in written.values())
        box = QMessageBox(self)
        box.setWindowTitle("Opname voltooid")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"Opname voltooid: {total_frames} frame(s) in {len(written)} bestand(en).\n"
            f"{files_text}\n\nMap: {output_dir}\n\nWat wil je met deze opname doen?"
        )
        keep_button = box.addButton("Bewaren", QMessageBox.ButtonRole.AcceptRole)
        rename_button = box.addButton("Naam aanpassen", QMessageBox.ButtonRole.ActionRole)
        delete_button = box.addButton("Verwijderen", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(keep_button)
        box.exec()
        clicked = box.clickedButton()

        if clicked is delete_button:
            self._delete_recording(output_dir)
            return
        if clicked is rename_button:
            output_dir = self._rename_recording(output_dir) or output_dir

        self._calibration_panel.show_feedback(f"Opname bewaard in {output_dir}", success=True)
        self._set_status(f"Video opgeslagen in {output_dir}")
        self._prompt_open_recording_folder(output_dir)

    def _rename_recording(self, output_dir: Path) -> Path | None:
        new_name, accepted = QInputDialog.getText(
            self,
            "Naam aanpassen",
            "Nieuwe naam voor de opnamemap:",
            text=output_dir.name,
        )
        if not accepted:
            return None
        cleaned = "".join(char for char in new_name if char not in '<>:"/\\|?*').strip()
        if not cleaned or cleaned == output_dir.name:
            return None
        target = output_dir.parent / cleaned
        if target.exists():
            self._show_warning(f"Er bestaat al een map met de naam '{cleaned}'.")
            return None
        try:
            renamed = output_dir.rename(target)
        except OSError as exc:
            self._show_error(f"Kon de opname niet hernoemen: {exc}")
            return None
        return renamed

    def _delete_recording(self, output_dir: Path) -> None:
        confirm = QMessageBox.question(
            self,
            "Opname verwijderen",
            f"Weet je zeker dat je deze opname definitief wilt verwijderen?\n{output_dir}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._calibration_panel.show_feedback(f"Opname bewaard in {output_dir}", success=True)
            self._set_status(f"Video opgeslagen in {output_dir}")
            return
        try:
            shutil.rmtree(output_dir)
        except OSError as exc:
            self._show_error(f"Kon de opname niet verwijderen: {exc}")
            return
        self._calibration_panel.show_feedback("Opname verwijderd.", success=True)
        self._set_status("Opname verwijderd.")

    def _prompt_open_recording_folder(self, folder: Path) -> None:
        reply = QMessageBox.question(
            self,
            "Video opgeslagen",
            f"Video('s) opgeslagen in:\n{folder}\n\nMap openen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_frame_batch(self, batch_obj: object) -> None:
        frames = dict(batch_obj)  # type: ignore[arg-type]
        if not frames:
            return
        incoming_ts = max(frame.timestamp_sec for frame in frames.values())
        if self._latest_frames:
            latest_ts = max(frame.timestamp_sec for frame in self._latest_frames.values())
            if incoming_ts < latest_ts:
                return
        self._latest_frames = frames
        self._active_camera_count = len(frames)
        self._refresh_live_status()
        # Render as soon as a frame arrives (frame-driven) for the lowest
        # latency, instead of waiting for the next display-timer tick.
        self._update_calibration_preview()

    def _build_calibration_preview_frame(
        self,
        source_id: str,
        frame_bgr: Any,
        detection: ChessboardDetectionResult,
        accepted: bool | None = None,
    ) -> Any:
        preview = self._prepare_calibration_preview_frame(source_id, frame_bgr)
        if not self._calibration_panel.overlay_enabled_for(source_id) or self._uses_qt_preview_overlay():
            return self._downscale_for_display(self._display_calibration_preview_frame(source_id, preview))
        rendered = self._draw_calibration_preview_overlay(
            source_id=source_id,
            frame_bgr=preview,
            detection=detection,
            accepted=accepted,
            sample_count=self._calibration_manager.observation_count(source_id, include_sync_only=False),
        )
        return self._downscale_for_display(rendered)

    def _sync_capture_timing_metadata(
        self,
        frames: dict[str, FramePacket],
    ) -> dict[str, Any]:
        source_timestamps: dict[str, float] = {}
        capture_started: dict[str, float] = {}
        capture_completed: dict[str, float] = {}
        batch_ids: set[str] = set()

        for source_id, frame in frames.items():
            timestamp = getattr(frame, "capture_started_sec", None)
            if timestamp is None:
                timestamp = frame.timestamp_sec
            source_timestamps[source_id] = round(float(timestamp), 6)

            started = getattr(frame, "capture_started_sec", None)
            if started is not None:
                capture_started[source_id] = round(float(started), 6)
            completed = getattr(frame, "capture_completed_sec", None)
            if completed is not None:
                capture_completed[source_id] = round(float(completed), 6)
            batch_id = getattr(frame, "batch_id", None)
            if batch_id:
                batch_ids.add(str(batch_id))

        timestamps = list(source_timestamps.values())
        skew_sec = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else 0.0
        return {
            "software_sync": True,
            "timestamp_source": "capture_started_sec",
            "timestamp_skew_ms": round(skew_sec * 1000.0, 3),
            "warning_timestamp_skew_ms": round(SYNC_SKEW_WARNING_SEC * 1000.0, 3),
            "max_allowed_timestamp_skew_ms": round(SYNC_SKEW_REJECT_SEC * 1000.0, 3),
            "batch_ids": sorted(batch_ids),
            "source_timestamps_sec": source_timestamps,
            "capture_started_sec": capture_started,
            "capture_completed_sec": capture_completed,
        }

    def _validate_sync_capture_timing(
        self,
        frames: dict[str, FramePacket],
        auto_trigger: bool,
    ) -> tuple[bool, dict[str, Any]]:
        metadata = self._sync_capture_timing_metadata(frames)
        skew_ms = float(metadata.get("timestamp_skew_ms") or 0.0)
        batch_ids = metadata.get("batch_ids", [])

        if isinstance(batch_ids, list) and len(batch_ids) > 1:
            message = "Sync capture rejected: frames came from different software batches."
            if auto_trigger:
                self._calibration_panel.set_auto_capture_status(message)
            else:
                self._calibration_panel.show_feedback(message, success=False)
                self._set_status(message)
            LOGGER.warning("%s batch_ids=%s", message, batch_ids)
            return False, metadata

        if skew_ms > SYNC_SKEW_REJECT_SEC * 1000.0:
            message = (
                "Sync capture rejected: camera timestamp skew "
                f"{skew_ms:.1f} ms exceeds {SYNC_SKEW_REJECT_SEC * 1000.0:.0f} ms."
            )
            if auto_trigger:
                self._calibration_panel.set_auto_capture_status(message)
            else:
                self._calibration_panel.show_feedback(message, success=False)
                self._set_status(message)
            LOGGER.warning("%s metadata=%s", message, metadata)
            return False, metadata

        if skew_ms > SYNC_SKEW_WARNING_SEC * 1000.0:
            now = time.perf_counter()
            if not auto_trigger or now - self._last_sync_timing_warning_at >= SYNC_WARNING_THROTTLE_SEC:
                message = (
                    "Sync capture warning: camera timestamp skew "
                    f"{skew_ms:.1f} ms. Hold the calibration board still."
                )
                if auto_trigger:
                    self._calibration_panel.set_auto_capture_status(message)
                else:
                    self._set_status(message)
                LOGGER.warning("%s metadata=%s", message, metadata)
                self._last_sync_timing_warning_at = now

        return True, metadata

    def _apply_calibration_capture_feedback(
        self,
        feedback_by_source: dict[str, Any],
        before_sync_sets: int,
        after_sync_sets: int,
        auto_trigger: bool,
        sync_metadata: dict[str, Any] | None = None,
        frames: dict[str, FramePacket] | None = None,
    ) -> bool:
        active_frames = frames if frames is not None else self._latest_frames
        feedback_messages: list[str] = []
        accepted_total = 0
        preview_frames: dict[str, Any] = {}
        detections: dict[str, ChessboardDetectionResult] = {}
        accepted_by_source: dict[str, bool | None] = {}
        sample_counts = self._calibration_manager.observations_summary(include_sync_only=False)

        for source_id, frame in active_frames.items():
            feedback = feedback_by_source.get(source_id)
            if feedback is not None:
                feedback_messages.append(feedback.message)
                if feedback.accepted:
                    accepted_total += 1
                detections[source_id] = feedback.detection
                accepted_by_source[source_id] = bool(feedback.accepted)
                preview_frames[source_id] = self._build_calibration_preview_frame(
                    source_id=source_id,
                    frame_bgr=frame.frame_bgr,
                    detection=feedback.detection,
                    accepted=feedback.accepted,
                )
                continue

            detection = self._latest_calibration_detections.get(source_id)
            if detection is None:
                continue
            detections[source_id] = detection
            preview_frames[source_id] = self._build_calibration_preview_frame(
                source_id=source_id,
                frame_bgr=frame.frame_bgr,
                detection=detection,
                accepted=None,
            )

        if detections:
            self._latest_calibration_detections = detections
        if preview_frames:
            overlay_states = (
                self._build_preview_overlay_states(detections, sample_counts, accepted_by_source)
                if self._uses_qt_preview_overlay()
                else None
            )
            self._update_preview_panel(preview_frames, detections, sample_counts, overlay_states)
        self._refresh_calibration_panel(force=True)

        if accepted_total > 0:
            sync_suffix = ""
            if after_sync_sets > before_sync_sets:
                sync_suffix = f" Created synchronized set #{after_sync_sets}."
                if sync_metadata:
                    skew_ms = float(sync_metadata.get("timestamp_skew_ms") or 0.0)
                    if skew_ms > SYNC_SKEW_WARNING_SEC * 1000.0:
                        sync_suffix += f" Software sync skew {skew_ms:.1f} ms."
            message = f"Accepted {accepted_total} sample(s). " + " | ".join(feedback_messages) + sync_suffix
            self._set_status(message)
            self._calibration_panel.show_feedback(message, success=True)
            if auto_trigger:
                self._calibration_panel.set_auto_capture_status(
                    f"Last auto capture stored {accepted_total} sample(s)."
                )
                self._last_calibration_auto_capture_at = time.perf_counter()
            return True

        if auto_trigger:
            self._calibration_panel.set_auto_capture_status(self._auto_capture_idle_text())
            return False

        message = "No valid calibration samples accepted. " + " | ".join(feedback_messages)
        self._set_status(message)
        self._calibration_panel.show_feedback(message, success=False)
        return False

    def _capture_calibration_samples(
        self,
        auto_trigger: bool,
        detections: dict[str, ChessboardDetectionResult] | None = None,
        frames: dict[str, FramePacket] | None = None,
    ) -> bool:
        # When detection runs on the background worker, ``frames`` is the exact
        # snapshot the detection was computed on so the stored corners and the
        # capture frames belong to the same instant. Manual capture falls back
        # to the latest live frames.
        active_frames = frames if frames is not None else self._latest_frames
        if not active_frames:
            if not auto_trigger:
                self._show_warning("No frames available. Start live capture first.")
            return False

        workflow_mode = self._calibration_workflow_mode()
        before_sync_sets = self._calibration_manager.synchronized_capture_count()
        allow_relaxed_sync = (
            self._calibration_panel.relaxed_sync_enabled() if workflow_mode == "sync_extrinsics" else False
        )
        if workflow_mode == "sync_extrinsics" and len(active_frames) < 2:
            if not auto_trigger:
                self._show_warning("Sync / Extrinsics mode requires at least 2 active camera feeds.")
            return False
        sync_metadata: dict[str, Any] | None = None
        if workflow_mode == "sync_extrinsics":
            timing_ok, sync_metadata = self._validate_sync_capture_timing(
                frames=active_frames,
                auto_trigger=auto_trigger,
            )
            if not timing_ok:
                return False
        active_detections = (
            {
                source_id: detections[source_id]
                for source_id in active_frames
                if detections is not None and source_id in detections
            }
            if detections
            else {}
        )
        if auto_trigger and workflow_mode == "intrinsics":
            allowed_source_ids = set(self._auto_capture_intrinsics_candidates(list(active_frames.keys())))
            if not allowed_source_ids:
                self._stop_auto_capture_if_limit_reached()
                return False
            if active_detections:
                active_detections = {
                    source_id: detection
                    for source_id, detection in active_detections.items()
                    if source_id in allowed_source_ids
                    and self._detection_needs_spatial_cells(source_id, detection)
                }
                if not active_detections:
                    self._calibration_panel.set_auto_capture_status(
                        "Auto capture waiting: current board position only touches grid cells already at target."
                    )
                    return False
        if active_detections:
            feedback_by_source = self._calibration_manager.try_add_detection_set(
                detections_by_source=active_detections,
                pattern=self._calibration_pattern,
                allow_relaxed_sync=allow_relaxed_sync,
                workflow_mode=workflow_mode,
                sync_metadata=sync_metadata,
            )
        else:
            frames_by_source = {
                source_id: frame.frame_bgr
                for source_id, frame in active_frames.items()
            }
            if auto_trigger and workflow_mode == "intrinsics":
                allowed_source_ids = set(self._auto_capture_intrinsics_candidates(list(frames_by_source.keys())))
                frames_by_source = {
                    source_id: frame
                    for source_id, frame in frames_by_source.items()
                    if source_id in allowed_source_ids
                }
                if not frames_by_source:
                    self._stop_auto_capture_if_limit_reached()
                    return False
            feedback_by_source = self._calibration_manager.try_add_observation_set(
                frames_by_source=frames_by_source,
                pattern=self._calibration_pattern,
                allow_relaxed_sync=allow_relaxed_sync,
                workflow_mode=workflow_mode,
                sync_metadata=sync_metadata,
            )
        after_sync_sets = self._calibration_manager.synchronized_capture_count()
        return self._apply_calibration_capture_feedback(
            feedback_by_source=feedback_by_source,
            before_sync_sets=before_sync_sets,
            after_sync_sets=after_sync_sets,
            auto_trigger=auto_trigger,
            sync_metadata=sync_metadata,
            frames=active_frames,
        )

    def _maybe_auto_capture_calibration(
        self,
        detections: dict[str, ChessboardDetectionResult],
        frames: dict[str, FramePacket] | None = None,
    ) -> bool:
        if self._intrinsics_solve_worker is not None:
            return False
        if not self._calibration_panel.auto_capture_enabled():
            return False
        if self._stop_auto_capture_if_limit_reached():
            return False
        now = time.perf_counter()
        if now - self._last_calibration_auto_capture_at < self._calibration_panel.auto_capture_cooldown_sec():
            return False
        captured = self._capture_calibration_samples(
            auto_trigger=True, detections=detections, frames=frames
        )
        if captured:
            self._stop_auto_capture_if_limit_reached()
        return captured

    def _on_capture_calibration(self) -> None:
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback(
                "Intrinsics solve is running; capture is paused until it finishes.",
                success=False,
            )
            return
        detections = self._latest_calibration_detections if self._latest_calibration_detections else None
        self._capture_calibration_samples(auto_trigger=False, detections=detections)

    def _on_start_auto_capture_from_preview(self) -> None:
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback(
                "Wait for the intrinsics solve to finish before starting auto capture.",
                success=False,
            )
            self._calibration_panel.set_auto_capture_enabled(False)
            return
        if not self._latest_frames:
            self._calibration_panel.set_auto_capture_enabled(False)
            self._show_warning("No frames available. Start live capture first.")
            return
        if self._stop_auto_capture_if_limit_reached():
            return

        self._calibration_panel.set_auto_capture_enabled(True)
        self._last_calibration_auto_capture_at = 0.0
        message = self._auto_capture_idle_text()
        self._calibration_panel.set_auto_capture_status(message)
        self._calibration_panel.show_feedback("Auto capture started from preview.", success=True)
        self._set_status("Auto capture started")
        self._update_calibration_preview(force=True)

    def _on_solve_calibration(self) -> None:
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback("Intrinsics solve is already running.", success=False)
            return

        worker = IntrinsicsSolveWorker(calibration_manager=self._calibration_manager)
        worker.result_ready.connect(self._on_intrinsics_solve_result)
        worker.error.connect(self._on_intrinsics_solve_error)
        worker.state_changed.connect(lambda state: LOGGER.info("Intrinsics solve state: %s", state))
        worker.finished.connect(self._on_intrinsics_solve_finished)
        self._intrinsics_solve_worker = worker
        total_samples = sum(self._calibration_manager.observations_summary(include_sync_only=False).values())
        progress_message = f"Solving intrinsics ({total_samples} samples)..."
        self._calibration_panel.set_intrinsics_solve_running(True, progress_message)
        self._calibration_panel.show_feedback(
            f"Solving intrinsics in the background ({total_samples} samples)...",
            success=True,
        )
        self._set_status("Solving intrinsics...")
        worker.start()

    def _on_intrinsics_solve_result(self, bundle_obj: object) -> None:
        if not isinstance(bundle_obj, CalibrationBundle):
            self._on_intrinsics_solve_error("Intrinsics solve returned an unexpected result.")
            return

        bundle = bundle_obj
        self._calibration_repo.save(bundle, self._calibration_path)
        self._set_current_calibration_bundle(bundle)

        solved = [camera for camera in bundle.cameras.values() if camera.status.startswith("solved")]
        mean_reproj = (
            sum(camera.reprojection_error or 0.0 for camera in solved) / len(solved)
            if solved
            else 0.0
        )
        message = (
            f"Calibration solved: {len(solved)}/{len(bundle.cameras)} cameras "
            f"(mean reproj={mean_reproj:.4f}px)."
        )
        self._set_status(message)
        self._calibration_panel.show_feedback(message, success=bool(solved))
        self._refresh_calibration_panel(force=True)
        for note in bundle.notes:
            LOGGER.info("Calibration note: %s", note)
        # For a single-camera rig intrinsics is the whole calibration, so treat a
        # successful intrinsics solve as "finished" and jump to results. Multi-camera
        # rigs still need an extrinsics solve, so they navigate from there instead.
        if solved and len(self._active_source_ids()) < 2:
            self._auto_navigate("results")

    def _on_intrinsics_solve_error(self, message: str) -> None:
        LOGGER.error("Intrinsics solve error: %s", message)
        self._calibration_panel.show_feedback(f"Intrinsics solve failed: {message}", success=False)
        self._set_status(f"Intrinsics solve failed: {message}")

    def _on_intrinsics_solve_finished(self) -> None:
        worker = self._intrinsics_solve_worker
        self._intrinsics_solve_worker = None
        self._calibration_panel.set_intrinsics_solve_running(False)
        self._refresh_calibration_panel(force=True)
        if worker is not None:
            worker.deleteLater()

    def _on_solve_extrinsics(self) -> None:
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback(
                "Wait for the intrinsics solve to finish before solving extrinsics.",
                success=False,
            )
            return
        base_bundle = self._current_calibration_bundle or self._calibration_manager.last_solution()
        if base_bundle is None:
            if not self._calibration_manager.sources():
                self._show_warning("Capture calibration samples first before solving extrinsics.")
                return
            base_bundle = self._calibration_manager.solve_intrinsics()

        active_ids = self._active_source_ids()
        if len(active_ids) >= 2:
            counts = self._synchronized_counts_by_source()
            weak = sorted(sid for sid in active_ids if counts.get(sid, 0) < 3)
            if weak:
                reply = QMessageBox.question(
                    self,
                    "Extrinsics incomplete",
                    "These camera(s) have too few synchronized sets with the others: "
                    f"{', '.join(weak)}.\n\n"
                    "For a reliable extrinsic calibration every camera must have seen the "
                    "board together with the others. Solve anyway? These camera(s) may stay "
                    "unsolved.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self._set_status("Extrinsics solve cancelled: not all cameras share views yet.")
                    return

        reference_source_id = active_ids[0] if active_ids else None
        bundle = self._calibration_manager.solve_extrinsics(
            base_bundle=base_bundle,
            reference_source_id=reference_source_id,
        )
        self._calibration_repo.save(bundle, self._calibration_path)
        self._set_current_calibration_bundle(bundle)

        solved_sources = [
            source_id
            for source_id, camera in bundle.cameras.items()
            if camera.rotation is not None and camera.translation is not None
        ]
        reference_id = str(bundle.metadata.get("extrinsics_reference_source_id", reference_source_id or "-"))
        message = (
            f"Extrinsics solved for {len(solved_sources)}/{len(bundle.cameras)} camera(s) "
            f"with {reference_id} as reference."
        )
        success = len(solved_sources) >= 2
        self._set_status(message)
        self._calibration_panel.show_feedback(message, success=success)
        self._refresh_calibration_panel(force=True)
        for note in bundle.notes[-6:]:
            LOGGER.info("Extrinsics note: %s", note)
        # Extrinsics is the final calibration step: jump to the results tab so the
        # user lands on the calibration outcome (honours the auto-navigate toggle).
        if success:
            self._auto_navigate("results")

    def _on_reset_calibration_samples(self) -> None:
        self._calibration_manager.reset()
        self._latest_calibration_detections.clear()
        self._refresh_calibration_panel(force=True)
        self._calibration_panel.show_feedback("Calibration samples reset.", success=True)
        self._set_status("Calibration samples reset")

    def _build_export_text(self, fmt: str) -> tuple[str | None, str | None, bool]:
        """Return (text, info_message, usable). text is None when no calibration exists.

        JSON is the full internal profile (re-loadable here); TOML is an
        aniposelib/Anipose-compatible calibration usable to import into another
        motion-analysis program.
        """
        bundle = self._current_calibration_bundle or self._calibration_manager.last_solution()
        if bundle is None:
            return None, None, False
        payload = self._calibration_repo.to_payload(bundle)
        if fmt == "json":
            return calibration_export.to_json(payload), None, True
        text, skipped, included = calibration_export.to_motion_capture_toml(payload)
        if included == 0:
            return (
                text,
                "Geen enkele camera heeft opgeloste extrinsics; los extrinsics op voordat je "
                "naar TOML exporteert voor bewegingsanalyse.",
                False,
            )
        if skipped:
            return text, "TOML laat camera's zonder extrinsics weg: " + ", ".join(skipped) + ".", True
        return text, None, True

    def _on_export_preview(self, fmt: str) -> None:
        fmt = (fmt or "toml").lower().strip()
        text, message, _usable = self._build_export_text(fmt)
        if text is None:
            self._calibration_panel.show_export_preview(
                "No solved calibration available yet. Capture samples and calculate intrinsics/extrinsics first."
            )
            return
        self._calibration_panel.show_export_preview(text)
        if message:
            self._calibration_panel.show_feedback(message, success=False)
        self._set_status(f"Calibration preview ({fmt.upper()})")

    def _on_export_calibration(self, fmt: str) -> None:
        fmt = (fmt or "toml").lower().strip()
        text, message, usable = self._build_export_text(fmt)
        if text is None:
            self._show_warning("No solved calibration available to export.")
            return
        if not usable:
            self._show_warning(message or "Calibration is not ready for export.")
            return
        extension = "json" if fmt == "json" else "toml"
        file_filter = "JSON (*.json)" if fmt == "json" else "TOML (*.toml)"
        default_path = self._config.calibration_dir / f"calibration.{extension}"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export calibration",
            str(default_path),
            file_filter,
        )
        if not selected:
            return
        path = Path(selected)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self._show_error(f"Could not export calibration: {exc}")
            return
        success_message = f"Calibration exported to {path}"
        if message:
            success_message += f" ({message})"
        self._calibration_panel.show_feedback(success_message, success=True)
        self._set_status(f"Calibration exported: {path.name}")

    def _on_new_project(self) -> None:
        reply = QMessageBox.question(
            self,
            "New Project",
            (
                "Start a new calibration project?\n\n"
                "This clears captured samples, unloads the active calibration, and prevents the previous "
                "auto-loaded calibration from coming back on restart. Saved profiles stay on disk."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._calibration_manager.reset_all()
        self._latest_calibration_detections.clear()
        self._last_rendered_frame_indices.clear()
        self._current_calibration_bundle = None
        self._calibration_loaded = False
        self._calibration_path = self._default_calibration_path()
        self._last_calibration_detection_at = 0.0

        try:
            if self._calibration_path.exists():
                self._calibration_path.unlink()
        except OSError as exc:
            LOGGER.warning("Could not remove current calibration file %s: %s", self._calibration_path, exc)
            self._calibration_panel.show_feedback(
                "New project started, but the current calibration file could not be removed.",
                success=False,
            )
            self._set_status("New project started; current calibration file still exists")
            self._refresh_calibration_panel(force=True)
            self._update_calibration_preview(force=True)
            self._auto_navigate("cameras")
            return

        self._refresh_calibration_panel(force=True)
        self._update_calibration_preview(force=True)
        self._calibration_panel.show_feedback("New project started. Previous calibration is unloaded.", success=True)
        self._set_status("New calibration project started")
        # A fresh project starts at the camera/calibration step (honours the
        # auto-navigate toggle in advanced settings).
        self._auto_navigate("cameras")

    def _on_save_calibration_profile(self) -> None:
        bundle = self._current_calibration_bundle or self._calibration_manager.last_solution()
        if bundle is None:
            self._show_warning("No solved calibration profile available to save.")
            return
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Calibration Profile",
            str(self._config.calibration_dir / "calibration_profile.json"),
            "Calibration JSON (*.json)",
        )
        if not selected:
            return
        path = Path(selected)
        self._calibration_repo.save(bundle, path)
        self._calibration_panel.show_feedback(f"Calibration profile saved to {path}", success=True)
        self._set_status(f"Calibration profile saved: {path.name}")

    def _on_load_calibration_profile(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Load Calibration Profile",
            str(self._config.calibration_dir),
            "Calibration JSON (*.json)",
        )
        if not selected:
            return
        path = Path(selected)
        bundle = self._calibration_repo.load(path)
        if bundle is None:
            self._show_error(f"Could not load calibration profile: {path}")
            return
        self._apply_spatial_grid_from_bundle_metadata(bundle)
        self._calibration_path = path
        self._set_current_calibration_bundle(bundle)
        self._calibration_panel.show_feedback(f"Loaded calibration profile {path.name}.", success=True)
        self._set_status(f"Calibration profile loaded: {path.name}")

    def _on_undistort_toggle_changed(self, source_id: str, enabled: bool) -> None:
        if enabled and not self._calibration_loaded:
            self._calibration_panel.show_feedback(
                f"{source_id}: undistort enabled but no calibration profile is loaded.",
                success=False,
            )
        self._update_calibration_preview(force=True)

    def _on_calibration_pattern_changed(self, pattern: str) -> None:
        normalized = pattern.lower().strip()
        if normalized not in {"chessboard", "charuco"}:
            normalized = "chessboard"
        self._calibration_pattern = normalized
        self._calibration_panel.show_feedback(f"Calibration pattern set to {normalized}.", success=True)
        self._update_calibration_preview(force=True)

    def _on_board_settings_applied(self, settings_obj: object) -> None:
        if not isinstance(settings_obj, CalibrationBoardSettings):
            return
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback(
                "Wait for the intrinsics solve to finish before changing board settings.",
                success=False,
            )
            return
        if settings_obj.charuco_marker_size_m >= settings_obj.charuco_square_size_m:
            self._show_warning("ChArUco marker size must be smaller than ChArUco square size.")
            return
        if settings_obj == self._calibration_manager.board_settings():
            self._calibration_panel.show_feedback("Board settings already active.", success=True)
            return

        has_existing_work = (
            bool(self._calibration_manager.sources())
            or self._calibration_manager.synchronized_capture_count() > 0
            or self._current_calibration_bundle is not None
        )
        if has_existing_work:
            reply = QMessageBox.question(
                self,
                "Apply Board Settings",
                (
                    "Changing board settings clears captured samples and unloads the active calibration. "
                    "Continue?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._calibration_panel.set_board_settings(self._calibration_manager.board_settings())
                return

        self._calibration_manager.apply_board_settings(settings_obj)
        active_settings = self._calibration_manager.board_settings()
        self._calibration_panel.set_board_settings(active_settings)
        self._calibration_panel.set_pattern_options(
            pattern_names=self._calibration_manager.available_patterns(),
            selected=self._calibration_pattern,
        )
        self._current_calibration_bundle = None
        self._calibration_loaded = False
        self._latest_calibration_detections.clear()
        self._last_rendered_frame_indices.clear()
        self._calibration_path = self._default_calibration_path()
        try:
            if self._calibration_path.exists():
                self._calibration_path.unlink()
        except OSError as exc:
            LOGGER.warning("Could not remove current calibration file %s: %s", self._calibration_path, exc)

        message = (
            "Board settings applied. Samples and active calibration were reset "
            f"(chessboard={active_settings.chessboard_cols}x{active_settings.chessboard_rows}, "
            f"square={active_settings.chessboard_square_size_m * 1000.0:.2f}mm; "
            f"charuco={active_settings.charuco_squares_x}x{active_settings.charuco_squares_y}, "
            f"square={active_settings.charuco_square_size_m * 1000.0:.2f}mm, "
            f"marker={active_settings.charuco_marker_size_m * 1000.0:.2f}mm)."
        )
        self._calibration_panel.show_feedback(message, success=True)
        self._set_status("Board settings applied")
        self._refresh_calibration_panel(force=True)
        self._update_calibration_preview(force=True)

    def _on_calibration_workflow_mode_changed(self, mode: str) -> None:
        normalized = mode.lower().strip()
        if normalized not in {"intrinsics", "sync_extrinsics"}:
            normalized = "intrinsics"
        if normalized == "sync_extrinsics":
            message = (
                "Calibration workflow set to Sync / Extrinsics: only synchronized multi-camera sets are stored."
            )
        else:
            message = (
                "Calibration workflow set to Intrinsics: per-camera samples use the intrinsics thresholds."
            )
        self._refresh_threshold_controls_for_mode()
        self._calibration_panel.show_feedback(message, success=True)
        self._refresh_calibration_panel(force=True)
        self._update_calibration_preview(force=True)

    def _on_acceptance_thresholds_changed(self, min_quality: float, min_coverage_ratio: float) -> None:
        if self._calibration_workflow_mode() == "sync_extrinsics":
            self._calibration_manager.set_sync_acceptance_thresholds(
                min_quality_score=min_quality,
                min_coverage_ratio=min_coverage_ratio,
            )
            message = (
                "Sync thresholds updated: "
                f"quality >= {min_quality:.2f}, coverage >= {min_coverage_ratio * 100.0:.1f}%."
            )
        else:
            self._calibration_manager.set_intrinsics_acceptance_thresholds(
                min_quality_score=min_quality,
                min_coverage_ratio=min_coverage_ratio,
            )
            message = (
                "Intrinsics thresholds updated: "
                f"quality >= {min_quality:.2f}, coverage >= {min_coverage_ratio * 100.0:.1f}%."
            )
        self._calibration_panel.show_feedback(message, success=True)
        self._refresh_calibration_panel(force=True)
        self._update_calibration_preview(force=True)

    def _on_spatial_grid_changed(self, cols: int, rows: int) -> None:
        self._calibration_manager.set_spatial_coverage_grid(cols=cols, rows=rows)
        max_samples = self._calibration_panel.auto_capture_max_samples()
        target = self._spatial_target_samples_per_cell()
        target_text = (
            f"{target} sample(s) per cell"
            if max_samples > 0
            else "3 sample(s) per cell while Max is unlimited"
        )
        self._calibration_panel.show_feedback(
            f"Overlay grid set to {cols}x{rows}; target is {target_text}.",
            success=True,
        )
        self._refresh_calibration_panel(force=True)
        self._update_calibration_preview(force=True)

    def _on_worker_error(self, message: str) -> None:
        LOGGER.error("Worker error: %s", message)
        self._set_status(f"Worker error: {message}")

    def _on_display_tick(self) -> None:
        self._update_calibration_preview()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._intrinsics_solve_worker is not None and self._intrinsics_solve_worker.isRunning():
            QMessageBox.information(
                self,
                "Intrinsics Solve",
                "Intrinsics solve is still running. Wait until it finishes before closing the app.",
            )
            event.ignore()
            return
        self._on_stop_live()
        self._stop_camera_probe_worker()
        self._shutdown_detection_worker()
        self._shutdown_preview_render_worker()
        super().closeEvent(event)
