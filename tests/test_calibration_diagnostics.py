from __future__ import annotations

import os
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from mocap_app.core.config import AppConfig
from mocap_app.io.video_recorder import VideoRecorder
from mocap_app.ui.designed_main_window import DesignedMainWindow


class CalibrationDiagnosticsRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        config = AppConfig(
            app_root=root,
            calibration_dir=root / "Projecten",
            results_dir=root / "Resultaten",
            logs_dir=root / "logs",
            sessions_dir=root / "sessions",
        )
        config.ensure_directories()
        self.window = DesignedMainWindow(config=config)
        self.panel = self.window._calibration_panel

    def tearDown(self) -> None:
        self.panel._mode_time_ticker.stop()
        self.window._display_timer.stop()
        self.window._stop_camera_probe_worker()
        self.window._shutdown_detection_worker()
        self.window._shutdown_preview_render_worker()
        self.window.deleteLater()
        sys.stdout = self._stdout
        sys.stderr = self._stderr
        self._temp_dir.cleanup()

    def _start_intrinsics_timer(self) -> None:
        self.panel._intrinsics_mode_started_at = time.perf_counter() - 2.0
        self.panel._mode_time_ticker.start()
        self.panel.set_auto_capture_enabled(True)
        self.panel.set_calibration_run_active(True)
        self.window._auto_calibration_active = True

    def test_stop_run_freezes_timer_and_capture_state(self) -> None:
        self._start_intrinsics_timer()

        self.window._on_stop_calibration_run()

        self.assertFalse(self.window._auto_calibration_active)
        self.assertFalse(self.panel.auto_capture_enabled())
        self.assertIsNone(self.panel._intrinsics_mode_started_at)
        self.assertFalse(self.panel._mode_time_ticker.isActive())
        self.assertFalse(self.panel._start_calibration_button.isChecked())

    def test_stopping_live_and_finishing_chain_freeze_timers(self) -> None:
        self._start_intrinsics_timer()
        self.panel.set_live_status(False, 0)

        self.assertIsNone(self.panel._intrinsics_mode_started_at)
        self.assertFalse(self.panel._mode_time_ticker.isActive())

        self._start_intrinsics_timer()
        self.window._finish_auto_calibration_chain()

        self.assertIsNone(self.panel._intrinsics_mode_started_at)
        self.assertFalse(self.panel._mode_time_ticker.isActive())

    def test_total_time_tracks_active_modes_not_solver_time(self) -> None:
        self.panel._intrinsics_mode_seconds = 12.0
        self.panel._extrinsics_mode_seconds = 8.0
        self.panel._refresh_mode_time_diagnostics()
        expected = self.panel._format_compute_duration(20.0)

        self.assertEqual(self.window.lab_diag_total_time.text(), "Totale kalibratietijd")
        self.assertEqual(self.window.text_diag_total_time.toPlainText(), expected)

        self.panel.set_solve_duration("intrinsics", 1.5)
        self.panel.set_solve_duration("extrinsics", 2.5)

        self.assertEqual(self.window.text_diag_total_time.toPlainText(), expected)
        self.assertEqual(self.window.text_diag_Intrinsics_time.toPlainText(), "1.50 s")
        self.assertEqual(self.window.text_diag_extrinsics_time.toPlainText(), "2.50 s")

    def test_disabling_auto_navigation_cancels_running_chain(self) -> None:
        self._start_intrinsics_timer()

        self.panel._auto_navigate_checkbox.setChecked(False)

        self.assertFalse(self.window._auto_calibration_active)
        self.assertFalse(self.panel.auto_capture_enabled())
        self.assertIsNone(self.panel._intrinsics_mode_started_at)
        self.assertFalse(self.panel._start_calibration_button.isChecked())

    def test_dropped_frames_are_exposed_to_diagnostics(self) -> None:
        class FullQueue:
            @staticmethod
            def put_nowait(_item: object) -> None:
                raise queue.Full

        recorder = object.__new__(VideoRecorder)
        recorder._closed = False
        recorder._write_queue = FullQueue()
        recorder._dropped_batches = 0
        recorder._dropped_frames = 0
        recorder.write_frames({"cam-1": object(), "cam-2": object(), "cam-3": object()})

        self.window._set_dropped_frames(recorder.dropped_frames())

        self.assertEqual(recorder.dropped_frames(), 3)
        self.assertEqual(self.window.text_diag_dropped_frames.toPlainText(), "3")


if __name__ == "__main__":
    unittest.main()
