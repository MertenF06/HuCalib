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
from mocap_app.workers.calibration_solve_worker import (
    ExtrinsicsSolveWorker,
    IntrinsicsSolveWorker,
)
from mocap_app.workers.camera_probe_worker import CameraProbeWorker
from mocap_app.workers.capture_worker import LiveCaptureWorker
from mocap_app.workers.detection_worker import CalibrationDetectionWorker
from mocap_app.workers.preview_render_worker import PreviewRenderWorker, resize_for_preview
from mocap_app.workers.recording_finalize_worker import RecordingFinalizeWorker


LOGGER = logging.getLogger(__name__)
SYNC_SKEW_WARNING_SEC = 0.050
SYNC_SKEW_REJECT_SEC = 0.150
SYNC_WARNING_THROTTLE_SEC = 3.0
# With this many cameras or fewer, prepare the display frame inline on the UI
# thread (lowest latency). Above it, offload to the preview-render worker.
INLINE_PREVIEW_MAX_CAMERAS = 2


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
        # Where the next "Nieuw Project" lands. Defaults to the standard
        # calibration folder; the user can change it via the Home screen.
        self._new_project_dir: Path = self._config.calibration_dir
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
        self._extrinsics_solve_worker: ExtrinsicsSolveWorker | None = None
        # Wall-clock start of each background solve, used to report the compute
        # time per stage on the diagnostics page.
        self._intrinsics_solve_started_at: float | None = None
        self._extrinsics_solve_started_at: float | None = None
        self._extrinsics_reference_hint: str | None = None
        self._extrinsics_solve_ok = False
        # Auto chain: extrinsics capture can complete while the intrinsics solve is
        # still running in the background; defer the extrinsics solve until then.
        self._pending_auto_extrinsics_solve = False
        # Show the "cameras have different resolutions" popup at most once per live
        # session (reset on each live (re)start).
        self._resolution_mismatch_prompted = False
        self._detection_thread: QThread | None = None
        self._detection_worker: CalibrationDetectionWorker | None = None
        self._detection_request_in_flight = False
        self._render_thread: QThread | None = None
        self._render_worker: PreviewRenderWorker | None = None
        self._render_request_in_flight = False
        self._video_recorder: VideoRecorder | None = None
        self._last_recording_dir: Path | None = None
        self._recording_finalize_worker: RecordingFinalizeWorker | None = None
        # True while the single "Start kalibratie" button drives the fully
        # automatic intrinsics -> solve -> extrinsics -> solve -> results chain.
        self._auto_calibration_active = False
        self._last_intrinsics_solve_ok = False
        # Measured live frame rate (EMA of frame-batch arrival intervals), shown
        # as "Huidige FPS" on the diagnostics page.
        self._fps_meter_last_ts = 0.0
        self._fps_meter_value = 0.0
        self._fps_meter_last_push = 0.0
        # Cache for the per-camera overlay state so it isn't recomputed on every
        # preview frame (only when detections/sample counts/flags actually change).
        self._preview_overlay_states_cache: dict[str, dict[str, Any]] | None = None
        self._preview_overlay_states_key: tuple[Any, ...] | None = None
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
        self._load_threshold_controls()

        self._load_existing_calibration()
        self._seed_startup_source_slots()
        self._refresh_live_status(force=True)
        self._refresh_calibration_panel(force=True)
        self._set_display_timer_hz(self._runtime_tuning.preview_fps)

        self.setWindowTitle(self._config.app_name)
        self._apply_initial_window_geometry()
        self._set_status("Klaar om te kalibreren")
        QTimer.singleShot(250, self._start_initial_camera_probe)

    def _setup_ui(self) -> None:
        self.setCentralWidget(self._calibration_panel)
        self.statusBar().showMessage("Idle")

    def _create_calibration_panel(self, default_camera_csv: str, default_fps: float):
        raise NotImplementedError("Subclasses must provide a calibration panel implementation.")

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
        if hasattr(self._calibration_panel, "start_calibration_requested"):
            self._calibration_panel.start_calibration_requested.connect(self._on_start_calibration_run)
        if hasattr(self._calibration_panel, "stop_calibration_requested"):
            self._calibration_panel.stop_calibration_requested.connect(self._on_stop_calibration_run)
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
        self._set_status(f"Camera's zoeken 0..{max_index} ...")

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
            self._set_status(f"{len(results)} camera('s) gevonden.")
            self._open_all_cameras_and_go_live()
        else:
            self._set_status("Geen camera's gevonden. Sluit een camera aan en zoek opnieuw.")

    def _open_all_cameras_and_go_live(self) -> None:
        """Open every detected camera as a source and start live view.

        Runs on every startup probe regardless of the auto-navigation toggle, so
        the user lands on a running multi-camera live preview without any manual
        steps. Falls back to the single-slot seeding if the panel can't open all.
        """
        open_all = getattr(self._calibration_panel, "open_all_detected_cameras", None)
        sources = open_all() if callable(open_all) else []
        if not sources:
            # Older panels / no detection: keep the previous single-slot behaviour.
            self._seed_startup_source_slots()
            return
        self._active_sources = sources
        self._on_start_live(sources, self._calibration_panel.target_fps())

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
            self._set_status(f"Kalibratie geladen: {self._calibration_path.name}")

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
            # Add/remove a camera while live is running: restart capture with the
            # new set so the change takes effect immediately instead of being
            # ignored until the next manual stop. Restarting routes through
            # _on_start_live -> _on_stop_live, which also finalizes any recording.
            current_ids = {source.source_id for source in self._active_sources}
            new_ids = {source.source_id for source in sources}
            if new_ids == current_ids:
                # Same cameras (e.g. a rename echo): nothing to reopen.
                self._active_sources = sources
                return
            if not sources:
                self._set_status("Laatste camera verwijderd — live gestopt.")
                self._on_stop_live()
                return
            self._set_status("Camera's gewijzigd — live opnieuw starten...")
            self._on_start_live(sources, self._calibration_panel.target_fps())
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

    def _load_threshold_controls(self) -> None:
        """Populate the (separate) intrinsics and extrinsics threshold spinboxes
        from the calibration manager. Both pairs are always shown."""
        self._calibration_panel.set_acceptance_threshold_values(
            intrinsics_quality=self._calibration_manager.min_quality_score,
            intrinsics_coverage_ratio=self._calibration_manager.min_coverage_ratio,
            extrinsics_quality=self._calibration_manager.sync_min_quality_score,
            extrinsics_coverage_ratio=self._calibration_manager.sync_min_coverage_ratio,
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
                    # A camera with no path to the reference would stay unsolved even
                    # at full quota, so flag those explicitly.
                    connectivity = self._extrinsics_connectivity()
                    reference_id = self._extrinsics_reference_id()
                    unconnected = sorted(
                        sid
                        for sid in counts
                        if connectivity.get(sid, {}).get("state") == "none" and sid != reference_id
                    )
                    if unconnected:
                        goal_text += (
                            f" Not yet connected to reference {reference_id}: "
                            f"{', '.join(unconnected)} — show the board to these together with "
                            "an already-connected camera."
                        )
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

    def _extrinsics_reference_id(self) -> str | None:
        """Reference camera the extrinsics solve will anchor on (first active source)."""
        ids = self._active_source_ids()
        return ids[0] if ids else None

    def _extrinsics_connectivity(self) -> dict[str, dict[str, Any]]:
        """Per-camera connectivity to the reference over the synchronized-set graph."""
        return self._calibration_manager.extrinsics_connectivity(
            self._extrinsics_reference_id(),
            source_ids=self._active_source_ids(),
        )

    def _connectivity_text(self, info: dict[str, Any], reference_id: str | None) -> str:
        """Short, user-facing description of a camera's link to the reference."""
        state = str(info.get("state", ""))
        via = info.get("via")
        ref = reference_id or "referentie"
        if state == "reference":
            return "referentiecamera"
        if state == "none":
            return f"niet verbonden met {ref}"
        if state == "indirect":
            return f"verbonden via {via}" if via else "verbonden via een andere camera"
        return ""  # direct: the filling/green bar already says it

    def _preview_sample_counts(self) -> dict[str, int]:
        """Per-camera sample count for the active mode, shown on the tile progress
        bars: intrinsic observations in intrinsics mode, synchronized sets in
        extrinsics mode, so the two budgets never bleed into each other."""
        if self._calibration_workflow_mode() == "sync_extrinsics":
            return self._synchronized_counts_by_source()
        return self._calibration_manager.observations_summary(include_sync_only=False)

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
            if not (per_source and all(count >= limit for count in per_source.values())):
                return None
            # Reaching the per-camera quota is not enough: a camera can hit it purely
            # through a bridge that never connects to the reference and would still
            # come out unsolved. Keep capturing until every camera is reachable from
            # the reference (directly or via a chain), matching the extrinsics solve.
            connectivity = self._extrinsics_connectivity()
            unconnected = sorted(
                sid for sid in source_ids if connectivity.get(sid, {}).get("state") == "none"
            )
            if unconnected:
                return None
            coverage_text = ", ".join(f"{sid}={count}" for sid, count in sorted(per_source.items()))
            return (
                f"Auto capture stopped: every camera reached {limit} synchronized set(s) "
                f"and is connected to the reference ({coverage_text})."
            )

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
        completed_mode = self._calibration_workflow_mode()
        self._calibration_panel.set_auto_capture_enabled(False)
        self._calibration_panel.set_auto_capture_status(message)
        self._calibration_panel.show_feedback(message, success=True)
        self._set_status(message)
        if completed_mode == "intrinsics":
            if self._auto_calibration_active:
                # Fully automatic chain: start the intrinsics solve in the
                # background and, for multi-camera rigs, advance to extrinsics
                # capture right away so synchronized sets can be collected in
                # parallel instead of waiting for the solve to finish.
                self._on_solve_calibration()
                if len(self._active_source_ids()) >= 2:
                    enter_extrinsics = getattr(self._calibration_panel, "enter_extrinsics_mode", None)
                    if callable(enter_extrinsics):
                        enter_extrinsics()
                        self._set_status(
                            "Intrinsics berekenen op de achtergrond — leg alvast extrinsics vast..."
                        )
                else:
                    self._set_status("Intrinsics compleet — automatisch berekenen...")
            else:
                # Once every camera has its full set of intrinsic samples,
                # auto-advance to the extrinsics capture mode (manual solve).
                self._maybe_auto_advance_to_extrinsics()
        elif completed_mode == "sync_extrinsics" and self._auto_calibration_active:
            # Fully automatic chain: solve extrinsics in the background (jumps to
            # Results when it finishes). If the intrinsics solve is still running,
            # the extrinsics solve needs its result, so defer until it finishes
            # (handled in _advance_auto_chain_after_intrinsics).
            if self._intrinsics_solve_worker is not None:
                self._pending_auto_extrinsics_solve = True
                self._set_status(
                    "Extrinsics vastgelegd — wachten op de intrinsics-berekening voor de extrinsics-solve..."
                )
            else:
                self._set_status("Extrinsics compleet — automatisch berekenen...")
                if not self._on_solve_extrinsics(prompt_on_incomplete=False):
                    self._finish_auto_calibration_chain()
        return True

    def _on_start_calibration_run(self) -> None:
        """Start the fully automatic calibration chain (single Start button)."""
        self._auto_calibration_active = True
        if self._live_worker is None or not self._live_worker.isRunning():
            try:
                sources = self._calibration_panel.current_sources()
            except ValueError:
                sources = self._active_sources
            if sources:
                self._on_start_live(sources, self._calibration_panel.target_fps())
        enter_intrinsics = getattr(self._calibration_panel, "enter_intrinsics_mode", None)
        if callable(enter_intrinsics):
            enter_intrinsics()
        self._set_status("Automatische kalibratie gestart (intrinsics).")
        self._calibration_panel.show_feedback(
            "Automatische kalibratie gestart — beweeg het bord door het beeld.", success=True
        )

    def _on_stop_calibration_run(self) -> None:
        """Stop the automatic chain. Auto-capture stops; live keeps running."""
        self._auto_calibration_active = False
        self._pending_auto_extrinsics_solve = False
        self._calibration_panel.set_auto_capture_enabled(False)
        self._set_status("Automatische kalibratie gestopt.")

    def _finish_auto_calibration_chain(self) -> None:
        """Clear the chain state and reset the Start/Stop button."""
        self._auto_calibration_active = False
        self._pending_auto_extrinsics_solve = False
        setter = getattr(self._calibration_panel, "set_calibration_run_active", None)
        if callable(setter):
            setter(False)

    def _maybe_auto_advance_to_extrinsics(self) -> None:
        """Switch from intrinsics to extrinsics capture mode when auto-navigation
        is on. No-op for panels that don't support it or when the toggle is off."""
        if not getattr(self._calibration_panel, "auto_navigation_enabled", lambda: False)():
            return
        enter_extrinsics = getattr(self._calibration_panel, "enter_extrinsics_mode", None)
        if callable(enter_extrinsics):
            enter_extrinsics()
            self._set_status("Intrinsics compleet — automatisch overgeschakeld naar Extrinsics.")

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

        sample_counts = self._preview_sample_counts()
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
        sample_counts = self._preview_sample_counts()
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
        # The overlay state only depends on the detection found-flag, sample
        # counts, grid/target and per-tile flags - not on the (per-frame changing)
        # corner positions, which reach the canvas via the detection object. So it
        # can be cached and reused across frames, sparing the per-frame, per-camera
        # spatial_coverage_summary computation that otherwise drives preview lag
        # with several cameras and the overlay on. The accepted-flash path (manual
        # capture) bypasses the cache since its accepted markers are one-off.
        if accepted_by_source:
            return self._compute_preview_overlay_states(detections, sample_counts, accepted_by_source)
        key = self._preview_overlay_states_signature(detections, sample_counts)
        if key == self._preview_overlay_states_key and self._preview_overlay_states_cache is not None:
            return self._preview_overlay_states_cache
        states = self._compute_preview_overlay_states(detections, sample_counts, None)
        self._preview_overlay_states_key = key
        self._preview_overlay_states_cache = states
        return states

    def _preview_overlay_states_signature(
        self,
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
    ) -> tuple[Any, ...]:
        # Per-tile connectivity tint depends on the reference (first active source)
        # and the synchronized-set graph, neither of which is fully captured by the
        # raw counts above (e.g. the reference can change without a count change), so
        # fold it into the cache key in extrinsics mode.
        connectivity_key: tuple[Any, ...] = ()
        if self._calibration_workflow_mode() == "sync_extrinsics":
            connectivity = self._extrinsics_connectivity()
            connectivity_key = tuple(
                (sid, connectivity.get(sid, {}).get("state"), connectivity.get(sid, {}).get("via"))
                for sid in sorted(connectivity)
            )
        return (
            self._spatial_target_samples_per_cell(),
            tuple(self._calibration_manager.spatial_grid_shape),
            self._calibration_workflow_mode(),
            round(self._overlay_scale(), 3),
            connectivity_key,
            tuple(
                (
                    source_id,
                    bool(detection.found),
                    int(sample_counts.get(source_id, 0)),
                    self._calibration_panel.overlay_enabled_for(source_id),
                    self._calibration_panel.mirror_preview_enabled_for(source_id),
                )
                for source_id, detection in detections.items()
            ),
        )

    def _compute_preview_overlay_states(
        self,
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
        accepted_by_source: dict[str, bool | None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        accepted_by_source = accepted_by_source or {}
        target = self._spatial_target_samples_per_cell()
        extrinsics_mode = self._calibration_workflow_mode() == "sync_extrinsics"
        # In extrinsics mode colour each tile's progress bar by how the camera links
        # to the reference (green=direct & quota met, amber=only via a bridge,
        # red=no path), so a full bar can no longer hide an unsolvable camera.
        connectivity: dict[str, dict[str, Any]] = {}
        reference_id: str | None = None
        if extrinsics_mode:
            reference_id = self._extrinsics_reference_id()
            connectivity = self._extrinsics_connectivity()
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
                # The coverage grid is intrinsics-only; hide it in extrinsics mode.
                "show_grid": not extrinsics_mode,
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
            if extrinsics_mode:
                info = connectivity.get(source_id, {"state": "none", "via": None})
                states[source_id]["connectivity"] = str(info.get("state", "none"))
                states[source_id]["connectivity_text"] = self._connectivity_text(info, reference_id)
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
        """Render a frame at the configured preview resolution for display only.

        The frame is scaled (down *or* up) to fit the preview box while keeping
        its aspect ratio, so every camera's preview lands on the same configured
        resolution immediately - regardless of its native capture resolution.
        Detection, calibration and recording use the full capture-resolution
        frame; this only affects the on-screen preview.
        """
        max_width = int(getattr(self._runtime_tuning, "preview_max_width", 0) or 0)
        max_height = int(getattr(self._runtime_tuning, "preview_max_height", 0) or 0)
        return resize_for_preview(frame_bgr, max_width, max_height)

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
        # A new live session: re-check whether the cameras deliver matching sizes.
        self._resolution_mismatch_prompted = False
        self._reset_measured_fps()
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
        worker.finished.connect(lambda w=worker: self._on_live_finished(w))
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
            self._set_status("Live weergave gestart")
        elif state == "live_stopped":
            self._set_status("Live weergave gestopt")
        else:
            self._set_status(state)

    def _on_live_finished(self, worker: "LiveCaptureWorker | None" = None) -> None:
        # A restart launches a new worker before the old one's finished signal is
        # delivered (it is queued onto the UI thread). If a different worker is now
        # the active one, this is that stale signal and must not tear down the fresh
        # session. When _live_worker is None the stop was deliberate, so proceed.
        if worker is not None and self._live_worker is not None and worker is not self._live_worker:
            return
        self._finalize_recording()
        if self._live_worker is not None and not self._live_worker.isRunning():
            self._live_worker = None
        self._active_sources = []
        self._active_camera_count = 0
        self._reset_measured_fps()
        self._refresh_live_status(force=True)
        # Live stopping cancels an in-progress automatic calibration chain so the
        # Start/Stop button doesn't stay stuck in the "running" state.
        if self._auto_calibration_active:
            self._finish_auto_calibration_chain()

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
        self._reset_measured_fps()
        self._refresh_live_status(force=True)
        self._refresh_calibration_panel(force=True)
        self._set_status("Live weergave gestopt")

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

        output_dir = recorder.output_dir
        total_frames = recorder.total_frames()
        # If the real capture rate drifted from the nominal fps, the clips need a
        # re-encode so they play back at real-time speed. That can be slow, so run
        # it on a background thread and only show the result dialog once the files
        # are finalized (otherwise the user could rename the folder mid-encode).
        if recorder.needs_frame_rate_correction():
            self._calibration_panel.show_feedback(
                "Opname verwerken (framerate corrigeren)...", success=True
            )
            self._set_status("Opname verwerken (framerate corrigeren)...")
            worker = RecordingFinalizeWorker(recorder, written)
            self._recording_finalize_worker = worker
            worker.finished_ok.connect(
                lambda: self._on_recording_finalized(output_dir, written, total_frames)
            )
            worker.error.connect(
                lambda message: self._on_recording_finalize_error(
                    message, output_dir, written, total_frames
                )
            )
            worker.finished.connect(worker.deleteLater)
            worker.start()
            return

        self._handle_recording_result(output_dir, written, total_frames)

    def _on_recording_finalized(
        self, output_dir: Path, written: dict[str, Path], total_frames: int
    ) -> None:
        self._recording_finalize_worker = None
        self._handle_recording_result(output_dir, written, total_frames)

    def _on_recording_finalize_error(
        self, message: str, output_dir: Path, written: dict[str, Path], total_frames: int
    ) -> None:
        self._recording_finalize_worker = None
        LOGGER.error("Recording frame-rate correction failed: %s", message)
        # The (uncorrected) clips still exist, so let the user keep/rename/delete
        # them rather than losing the recording over a re-encode failure.
        self._handle_recording_result(output_dir, written, total_frames)

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
        self._update_measured_fps()
        self._maybe_warn_resolution_mismatch()
        self._refresh_live_status()
        # Render as soon as a frame arrives (frame-driven) for the lowest
        # latency, instead of waiting for the next display-timer tick.
        self._update_calibration_preview()

    def _maybe_warn_resolution_mismatch(self) -> None:
        """Warn once when the active cameras deliver different frame resolutions.

        A camera that cannot honour the requested capture resolution silently falls
        back to its maximum, so a rig can end up mixing e.g. 1080p and 720p. That is
        valid for calibration, but we surface it with an option to make every camera
        use the same (smallest delivered) resolution.
        """
        if self._resolution_mismatch_prompted:
            return
        active = self._active_source_ids()
        if len(active) < 2:
            return
        sizes: dict[str, tuple[int, int]] = {}
        for source_id in active:
            frame = self._latest_frames.get(source_id)
            if frame is None:
                return  # wait until every active camera has delivered a frame
            height, width = frame.frame_bgr.shape[:2]
            sizes[source_id] = (int(width), int(height))
        if len(set(sizes.values())) < 2:
            return
        self._resolution_mismatch_prompted = True
        self._show_resolution_mismatch_dialog(sizes)

    def _show_resolution_mismatch_dialog(self, sizes: dict[str, tuple[int, int]]) -> None:
        target_w, target_h = min(sizes.values(), key=lambda size: size[0] * size[1])
        detail = ", ".join(
            f"{source_id}={width}x{height}" for source_id, (width, height) in sorted(sizes.items())
        )
        box = QMessageBox(self)
        box.setWindowTitle("Verschillende cameraresoluties")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Niet alle camera's leveren dezelfde resolutie.")
        box.setInformativeText(
            f"{detail}\n\n"
            "De kalibratie werkt hier prima mee (elke camera houdt zijn eigen "
            "resolutie en intrinsics), maar meestal betekent dit dat een camera de "
            "gevraagde resolutie niet aankon en terugviel op zijn maximum.\n\n"
            f"Wil je alle camera's op {target_w}x{target_h} zetten zodat ze gelijk zijn? "
            "De live weergave start dan opnieuw."
        )
        make_uniform = box.addButton(
            f"Alles op {target_w}x{target_h}", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Negeren", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(make_uniform)
        box.exec()
        if box.clickedButton() is make_uniform:
            self._apply_uniform_capture_resolution(target_w, target_h)

    def _apply_uniform_capture_resolution(self, width: int, height: int) -> None:
        setter = getattr(self._calibration_panel, "force_capture_resolution", None)
        if not callable(setter):
            return
        setter(width, height)
        # Pull the updated capture resolution into the active runtime tuning, then
        # restart live so the new resolution actually takes effect on every camera.
        self._on_runtime_tuning_changed(self._calibration_panel.runtime_tuning())
        if self._live_worker is not None and self._live_worker.isRunning() and self._active_sources:
            self._on_start_live(self._active_sources, self._calibration_panel.target_fps())
        # The user made an explicit choice; don't pop the dialog again this session
        # even if a camera still can't reach the target (avoids a restart loop).
        self._resolution_mismatch_prompted = True
        self._set_status(
            f"Capture-resolutie ingesteld op {width}x{height} voor alle camera's; live herstart."
        )

    def _update_measured_fps(self) -> None:
        """Track the real live frame rate from batch arrival intervals and push
        it to the diagnostics page about twice a second."""
        now = time.perf_counter()
        last = self._fps_meter_last_ts
        self._fps_meter_last_ts = now
        if last <= 0.0:
            return
        dt = now - last
        if dt <= 0.0:
            return
        instant = 1.0 / dt
        # Exponential moving average smooths out per-frame jitter.
        if self._fps_meter_value <= 0.0:
            self._fps_meter_value = instant
        else:
            self._fps_meter_value += 0.2 * (instant - self._fps_meter_value)
        if now - self._fps_meter_last_push >= 0.5:
            self._fps_meter_last_push = now
            setter = getattr(self._calibration_panel, "set_current_fps", None)
            if callable(setter):
                setter(self._fps_meter_value)

    def _reset_measured_fps(self) -> None:
        self._fps_meter_last_ts = 0.0
        self._fps_meter_value = 0.0
        self._fps_meter_last_push = 0.0
        setter = getattr(self._calibration_panel, "set_current_fps", None)
        if callable(setter):
            setter(None)

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
        sample_counts = self._preview_sample_counts()

        # Auto capture fires continuously while live, so injecting a captured
        # snapshot frame here (the green "accepted" flash) competes with the
        # display timer's smooth cadence and shows up as a per-sample jitter in
        # the live preview. The live tick already redraws the latest detection
        # overlay and the coverage grid fills in as cells are hit, so for auto
        # capture we only update the stored detections/counts and let the video
        # keep flowing. Manual capture still flashes the captured frame as
        # explicit feedback (one-off, so no perceptible jitter).
        inject_preview = not auto_trigger

        for source_id, frame in active_frames.items():
            feedback = feedback_by_source.get(source_id)
            if feedback is not None:
                feedback_messages.append(feedback.message)
                if feedback.accepted:
                    accepted_total += 1
                detections[source_id] = feedback.detection
                accepted_by_source[source_id] = bool(feedback.accepted)
                if inject_preview:
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
            if inject_preview:
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
                self._show_warning("Geen beeld beschikbaar. Start eerst de live weergave.")
            return False

        workflow_mode = self._calibration_workflow_mode()
        before_sync_sets = self._calibration_manager.synchronized_capture_count()
        # Sync/extrinsics capture always evaluates samples against the dedicated
        # extrinsics thresholds (configured separately in advanced settings); the
        # old "Relax Sync Thresholds" toggle has been removed.
        allow_relaxed_sync = workflow_mode == "sync_extrinsics"
        if workflow_mode == "sync_extrinsics" and len(active_frames) < 2:
            if not auto_trigger:
                self._show_warning("Sync/Extrinsics vereist minimaal 2 actieve camera's.")
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
        # The extrinsics solve reads the capture sets, so never capture while it runs.
        if self._extrinsics_solve_worker is not None:
            return False
        # Extrinsics (sync) capture is independent of the intrinsics solve and may
        # run in parallel with it; only block intrinsics auto-capture while the
        # intrinsics solve is in flight.
        if self._intrinsics_solve_worker is not None and self._calibration_workflow_mode() != "sync_extrinsics":
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
        # Sync/extrinsics capture is allowed during the intrinsics solve (they are
        # independent); only intrinsics capture waits for the solve to finish.
        if self._intrinsics_solve_worker is not None and self._calibration_workflow_mode() != "sync_extrinsics":
            self._calibration_panel.show_feedback(
                "Intrinsics solve is running; intrinsics capture is paused until it finishes.",
                success=False,
            )
            return
        detections = self._latest_calibration_detections if self._latest_calibration_detections else None
        self._capture_calibration_samples(auto_trigger=False, detections=detections)

    def _on_start_auto_capture_from_preview(self) -> None:
        if self._intrinsics_solve_worker is not None and self._calibration_workflow_mode() != "sync_extrinsics":
            self._calibration_panel.show_feedback(
                "Wait for the intrinsics solve to finish before starting intrinsics auto capture.",
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
        self._calibration_panel.show_feedback("Automatisch vastleggen gestart vanuit preview.", success=True)
        self._set_status("Automatisch vastleggen gestart")
        self._update_calibration_preview(force=True)

    def _on_solve_calibration(self) -> None:
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback("Intrinsics berekenen is al bezig.", success=False)
            return

        worker = IntrinsicsSolveWorker(calibration_manager=self._calibration_manager)
        worker.result_ready.connect(self._on_intrinsics_solve_result)
        worker.error.connect(self._on_intrinsics_solve_error)
        worker.state_changed.connect(lambda state: LOGGER.info("Intrinsics solve state: %s", state))
        worker.progress.connect(self._on_intrinsics_solve_progress)
        worker.finished.connect(self._on_intrinsics_solve_finished)
        self._intrinsics_solve_worker = worker
        total_samples = sum(self._calibration_manager.observations_summary(include_sync_only=False).values())
        progress_message = f"Intrinsics berekenen ({total_samples} samples)..."
        self._calibration_panel.set_intrinsics_solve_running(True, progress_message)
        self._calibration_panel.show_feedback(
            f"Intrinsics berekenen op de achtergrond ({total_samples} samples)...",
            success=True,
        )
        self._set_status("Intrinsics berekenen...")
        self._intrinsics_solve_started_at = time.perf_counter()
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
        self._last_intrinsics_solve_ok = bool(solved)
        # For a single-camera rig intrinsics is the whole calibration, so treat a
        # successful intrinsics solve as "finished" and jump to results. Multi-camera
        # rigs still need an extrinsics solve, so they navigate from there instead.
        if solved and len(self._active_source_ids()) < 2:
            self._auto_navigate("results")

    def _on_intrinsics_solve_error(self, message: str) -> None:
        LOGGER.error("Intrinsics solve error: %s", message)
        self._calibration_panel.show_feedback(f"Intrinsics berekenen mislukt: {message}", success=False)
        self._set_status(f"Intrinsics berekenen mislukt: {message}")
        self._last_intrinsics_solve_ok = False

    def _on_intrinsics_solve_progress(self, done: int, total: int) -> None:
        self._calibration_panel.set_solve_progress("intrinsics", done, total)

    def _on_intrinsics_solve_finished(self) -> None:
        worker = self._intrinsics_solve_worker
        self._intrinsics_solve_worker = None
        if self._intrinsics_solve_started_at is not None:
            self._calibration_panel.set_solve_duration(
                "intrinsics", time.perf_counter() - self._intrinsics_solve_started_at
            )
            self._intrinsics_solve_started_at = None
        self._calibration_panel.set_intrinsics_solve_running(False)
        self._refresh_calibration_panel(force=True)
        if worker is not None:
            worker.deleteLater()
        # Drive the automatic chain now the solve worker is fully cleared.
        if self._auto_calibration_active:
            self._advance_auto_chain_after_intrinsics()

    def _advance_auto_chain_after_intrinsics(self) -> None:
        # The chain already switched to extrinsics capture in parallel while this
        # solve ran, so here we only react to its outcome.
        if not self._last_intrinsics_solve_ok:
            # Intrinsics failed: extrinsics cannot be solved, so stop the chain and
            # disarm the extrinsics capture that was started in parallel.
            self._pending_auto_extrinsics_solve = False
            self._calibration_panel.set_auto_capture_enabled(False)
            self._calibration_panel.show_feedback(
                "Automatische kalibratie gestopt: intrinsics berekenen mislukt.", success=False
            )
            self._finish_auto_calibration_chain()
            return
        if len(self._active_source_ids()) < 2:
            # Single camera: intrinsics is the whole calibration (already navigated
            # to Results in the solve result handler). Chain is done.
            self._finish_auto_calibration_chain()
            return
        if self._pending_auto_extrinsics_solve:
            # Extrinsics capture already completed while intrinsics was still
            # solving; run the deferred extrinsics solve now that its result exists.
            self._pending_auto_extrinsics_solve = False
            self._set_status("Intrinsics berekend — extrinsics automatisch berekenen...")
            if not self._on_solve_extrinsics(prompt_on_incomplete=False):
                self._finish_auto_calibration_chain()
        else:
            # Extrinsics capture is still in progress (it began in parallel); just
            # let it continue until the synchronized sets are complete.
            self._set_status("Intrinsics berekend — ga door met extrinsics vastleggen.")

    def _on_solve_extrinsics(self, prompt_on_incomplete: bool = True) -> bool:
        """Kick off the extrinsics solve on a worker thread.

        Returns ``True`` when a solve worker was started (so callers driving the
        automatic chain know to close it from the finished handler instead of
        immediately), ``False`` when the solve was rejected or cancelled up front.
        """
        if self._intrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback(
                "Wait for the intrinsics solve to finish before solving extrinsics.",
                success=False,
            )
            return False
        if self._extrinsics_solve_worker is not None:
            self._calibration_panel.show_feedback("Extrinsics berekenen is al bezig.", success=False)
            return False

        base_bundle = self._current_calibration_bundle or self._calibration_manager.last_solution()
        if base_bundle is None and not self._calibration_manager.sources():
            self._show_warning("Leg eerst kalibratiesamples vast voordat je extrinsics berekent.")
            return False
        # When no intrinsics bundle exists yet, solve_extrinsics() falls back to an
        # intrinsics solve internally; that heavy path also runs on the worker.

        active_ids = self._active_source_ids()
        if len(active_ids) >= 2 and prompt_on_incomplete:
            connectivity = self._extrinsics_connectivity()
            reference_id = self._extrinsics_reference_id()
            # Cameras with no path to the reference are the ones that genuinely stay
            # unsolved; flag those precisely instead of guessing from a raw set count.
            unconnected = sorted(
                sid
                for sid in active_ids
                if connectivity.get(sid, {}).get("state") == "none" and sid != reference_id
            )
            if unconnected:
                reply = QMessageBox.question(
                    self,
                    "Extrinsics incomplete",
                    "These camera(s) never shared a synchronized view that connects them to "
                    f"reference {reference_id}: {', '.join(unconnected)}.\n\n"
                    "For a reliable extrinsic calibration every camera must have seen the "
                    "board together with an already-connected camera. Solve anyway? These "
                    "camera(s) will stay unsolved.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self._set_status("Extrinsics berekenen geannuleerd: nog niet alle camera's zijn verbonden met de referentiecamera.")
                    return False

        reference_source_id = active_ids[0] if active_ids else None
        self._extrinsics_reference_hint = reference_source_id
        worker = ExtrinsicsSolveWorker(
            calibration_manager=self._calibration_manager,
            base_bundle=base_bundle,
            reference_source_id=reference_source_id,
        )
        worker.result_ready.connect(self._on_extrinsics_solve_result)
        worker.error.connect(self._on_extrinsics_solve_error)
        worker.state_changed.connect(lambda state: LOGGER.info("Extrinsics solve state: %s", state))
        worker.progress.connect(self._on_extrinsics_solve_progress)
        worker.finished.connect(self._on_extrinsics_solve_finished)
        self._extrinsics_solve_worker = worker
        # Lock capture too: the extrinsics solve reads the synchronized capture sets,
        # so no new sets should be appended while it runs (unlike the intrinsics solve,
        # which runs in parallel with extrinsics capture).
        self._calibration_panel.set_intrinsics_solve_running(
            True, "Solving extrinsics...", lock_capture=True, stage="extrinsics"
        )
        self._calibration_panel.show_feedback("Extrinsics berekenen op de achtergrond...", success=True)
        self._set_status("Extrinsics berekenen...")
        self._extrinsics_solve_started_at = time.perf_counter()
        worker.start()
        return True

    def _on_extrinsics_solve_result(self, bundle_obj: object) -> None:
        if not isinstance(bundle_obj, CalibrationBundle):
            self._on_extrinsics_solve_error("Extrinsics solve returned an unexpected result.")
            return

        bundle = bundle_obj
        self._calibration_repo.save(bundle, self._calibration_path)
        self._set_current_calibration_bundle(bundle)

        solved_sources = [
            source_id
            for source_id, camera in bundle.cameras.items()
            if camera.rotation is not None and camera.translation is not None
        ]
        reference_id = str(
            bundle.metadata.get("extrinsics_reference_source_id", self._extrinsics_reference_hint or "-")
        )
        message = (
            f"Extrinsics solved for {len(solved_sources)}/{len(bundle.cameras)} camera(s) "
            f"with {reference_id} as reference."
        )
        success = len(solved_sources) >= 2
        self._extrinsics_solve_ok = success
        self._set_status(message)
        self._calibration_panel.show_feedback(message, success=success)
        self._refresh_calibration_panel(force=True)
        for note in bundle.notes[-6:]:
            LOGGER.info("Extrinsics note: %s", note)
        # Extrinsics is the final calibration step: jump to the results tab so the
        # user lands on the calibration outcome (honours the auto-navigate toggle).
        if success:
            self._auto_navigate("results")

    def _on_extrinsics_solve_error(self, message: str) -> None:
        LOGGER.error("Extrinsics solve error: %s", message)
        self._extrinsics_solve_ok = False
        self._calibration_panel.show_feedback(f"Extrinsics berekenen mislukt: {message}", success=False)
        self._set_status(f"Extrinsics berekenen mislukt: {message}")

    def _on_extrinsics_solve_progress(self, done: int, total: int) -> None:
        self._calibration_panel.set_solve_progress("extrinsics", done, total)

    def _on_extrinsics_solve_finished(self) -> None:
        worker = self._extrinsics_solve_worker
        self._extrinsics_solve_worker = None
        if self._extrinsics_solve_started_at is not None:
            self._calibration_panel.set_solve_duration(
                "extrinsics", time.perf_counter() - self._extrinsics_solve_started_at
            )
            self._extrinsics_solve_started_at = None
        self._calibration_panel.set_intrinsics_solve_running(False)
        self._refresh_calibration_panel(force=True)
        if worker is not None:
            worker.deleteLater()
        # Close the automatic chain now the solve worker is fully cleared (the
        # extrinsics solve is the final step of the fully automatic run).
        if self._auto_calibration_active:
            self._finish_auto_calibration_chain()

    def _on_reset_calibration_samples(self) -> None:
        self._calibration_manager.reset()
        self._latest_calibration_detections.clear()
        # Reset also stops the running calibration: tear down the auto chain and
        # disarm auto-capture so the frame loop stops storing new samples once the
        # button has returned to its idle "Start kalibratie" state.
        self._calibration_panel.set_auto_capture_enabled(False)
        self._finish_auto_calibration_chain()
        self._refresh_calibration_panel(force=True)
        self._calibration_panel.show_feedback("Samples gewist; kalibratie gestopt.", success=True)
        self._set_status("Samples gewist; kalibratie gestopt")

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
        # Confirm the new project and let the user change its location from the
        # same popup. The "Locatie wijzigen…" button re-shows the dialog with the
        # newly picked folder; cancelling the picker keeps the current location.
        project_dir = self._new_project_dir
        while True:
            box = QMessageBox(self)
            box.setWindowTitle("New Project")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                f"Start a new calibration project in:\n{project_dir}\n\n"
                "This clears captured samples, unloads the active calibration, and prevents the previous "
                "auto-loaded calibration from coming back on restart. Saved profiles stay on disk."
            )
            start_button = box.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
            change_button = box.addButton("Locatie wijzigen…", QMessageBox.ButtonRole.ActionRole)
            cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(cancel_button)
            box.exec()

            clicked = box.clickedButton()
            if clicked is change_button:
                chosen = QFileDialog.getExistingDirectory(
                    self, "Kies een locatie voor het nieuwe project", str(project_dir)
                )
                if chosen:
                    project_dir = Path(chosen)
                continue
            if clicked is not start_button:
                return
            break

        self._new_project_dir = project_dir
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_error(f"Could not create project folder {project_dir}: {exc}")
            return

        self._calibration_manager.reset_all()
        self._latest_calibration_detections.clear()
        self._last_rendered_frame_indices.clear()
        self._current_calibration_bundle = None
        self._calibration_loaded = False
        self._calibration_path = project_dir / "current_calibration.json"
        self._last_calibration_detection_at = 0.0
        # Anchor the directory browser to the new project folder.
        self._calibration_panel.set_project_home(project_dir)

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
        self._load_threshold_controls()
        self._calibration_panel.show_feedback(message, success=True)
        self._refresh_calibration_panel(force=True)
        self._update_calibration_preview(force=True)

    def _on_acceptance_thresholds_changed(
        self,
        intrinsics_quality: float,
        intrinsics_coverage_ratio: float,
        extrinsics_quality: float,
        extrinsics_coverage_ratio: float,
    ) -> None:
        self._calibration_manager.set_intrinsics_acceptance_thresholds(
            min_quality_score=intrinsics_quality,
            min_coverage_ratio=intrinsics_coverage_ratio,
        )
        self._calibration_manager.set_sync_acceptance_thresholds(
            min_quality_score=extrinsics_quality,
            min_coverage_ratio=extrinsics_coverage_ratio,
        )
        message = (
            "Thresholds bijgewerkt — intrinsics: "
            f"q >= {intrinsics_quality:.2f}, cov >= {intrinsics_coverage_ratio * 100.0:.1f}%; "
            "extrinsics: "
            f"q >= {extrinsics_quality:.2f}, cov >= {extrinsics_coverage_ratio * 100.0:.1f}%."
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
        if self._extrinsics_solve_worker is not None and self._extrinsics_solve_worker.isRunning():
            QMessageBox.information(
                self,
                "Extrinsics Solve",
                "Extrinsics solve is still running. Wait until it finishes before closing the app.",
            )
            event.ignore()
            return
        self._on_stop_live()
        self._stop_camera_probe_worker()
        self._shutdown_detection_worker()
        self._shutdown_preview_render_worker()
        if self._recording_finalize_worker is not None and self._recording_finalize_worker.isRunning():
            # Let an in-progress clip re-encode finish so we don't leave a stray
            # temp file or a half-written clip behind.
            self._recording_finalize_worker.wait(10000)
        super().closeEvent(event)
