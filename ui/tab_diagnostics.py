"""Diagnostics tab - read-only counters fed by the calibration backend."""

from __future__ import annotations

import time

from PySide6 import QtCore


class TabDiagnostics:
    def __init__(self, logic_instance) -> None:
        self.logic = logic_instance
        self.window = logic_instance.window
        self._timer: QtCore.QTimer | None = None
        self._intrinsics_last_duration: float | None = None
        self._extrinsics_last_duration: float | None = None
        self._intrinsics_started_at: float | None = None
        self._extrinsics_started_at: float | None = None

    def setup(self) -> None:
        self.window.text_diag_dropped_frames.setPlainText("0")
        self.window.text_diag_current_fps.setPlainText("0.0")
        self.window.text_diag_used_cams.setPlainText("0")
        self.window.text_diag_Intrinsics_time.setPlainText("-")
        self.window.text_diag_extrinsics_time.setPlainText("-")
        self.window.text_diag_total_time.setPlainText("00:00:00")

        self._timer = QtCore.QTimer(self.window)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ----- public hooks called by tab_cameras ------------------------------

    def begin_intrinsics_timer(self) -> None:
        self._intrinsics_started_at = time.perf_counter()
        self.window.text_diag_Intrinsics_time.setPlainText("Bezig...")

    def end_intrinsics_timer(self) -> None:
        if self._intrinsics_started_at is None:
            return
        duration = time.perf_counter() - self._intrinsics_started_at
        self._intrinsics_started_at = None
        self._intrinsics_last_duration = duration
        self.window.text_diag_Intrinsics_time.setPlainText(self._format_seconds(duration))

    def begin_extrinsics_timer(self) -> None:
        self._extrinsics_started_at = time.perf_counter()
        self.window.text_diag_extrinsics_time.setPlainText("Bezig...")

    def end_extrinsics_timer(self) -> None:
        if self._extrinsics_started_at is None:
            return
        duration = time.perf_counter() - self._extrinsics_started_at
        self._extrinsics_started_at = None
        self._extrinsics_last_duration = duration
        self.window.text_diag_extrinsics_time.setPlainText(self._format_seconds(duration))

    # ----- internal --------------------------------------------------------

    def _tick(self) -> None:
        tab_cameras = getattr(self.logic, "tab_cameras", None)
        if tab_cameras is None:
            return

        active_frames = [
            frame for frame in tab_cameras.camera_frames if frame.thread is not None
        ]
        self.window.text_diag_used_cams.setPlainText(str(len(active_frames)))

        avg_fps = 0.0
        dropped = 0
        for frame in active_frames:
            avg_fps += frame.measured_fps()
            dropped += frame.dropped_frames()
        if active_frames:
            avg_fps /= len(active_frames)
        self.window.text_diag_current_fps.setPlainText(f"{avg_fps:.1f}")
        self.window.text_diag_dropped_frames.setPlainText(str(dropped))

        uptime = self.logic.uptime_seconds()
        self.window.text_diag_total_time.setPlainText(self._format_hms(uptime))

    @staticmethod
    def _format_seconds(value: float) -> str:
        if value < 1.0:
            return f"{value * 1000:.0f} ms"
        return f"{value:.2f} s"

    @staticmethod
    def _format_hms(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
