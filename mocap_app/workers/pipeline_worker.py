from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from mocap_app.models.types import CalibrationBundle, FramePacket, PipelineResult
from mocap_app.pipeline.manager import MocapPipeline


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _PipelineJob:
    frames: dict[str, FramePacket]
    run_detection: bool


class PipelineWorker(QThread):
    """Runs the motion pipeline off the GUI thread with a latest-frame policy."""

    result_ready = Signal(object)
    state_changed = Signal(str)
    error = Signal(str)

    def __init__(self, pipeline: MocapPipeline) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._condition = threading.Condition()
        self._pipeline_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._latest_job: _PipelineJob | None = None
        self._dropped_input_jobs = 0

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

    def submit_batch(self, frames: dict[str, FramePacket], run_detection: bool) -> None:
        if not frames:
            return
        job = _PipelineJob(frames=frames, run_detection=run_detection)
        with self._condition:
            if self._latest_job is not None:
                self._dropped_input_jobs += 1
            self._latest_job = job
            self._condition.notify()

    def update_calibration(self, bundle: CalibrationBundle | None) -> None:
        with self._pipeline_lock:
            self._pipeline.update_calibration(bundle)

    def run(self) -> None:
        self.state_changed.emit("pipeline_started")
        while not self._stop_event.is_set():
            with self._condition:
                while self._latest_job is None and not self._stop_event.is_set():
                    self._condition.wait(timeout=0.2)
                if self._stop_event.is_set():
                    break
                job = self._latest_job
                self._latest_job = None

            if job is None:
                continue

            try:
                with self._pipeline_lock:
                    result = self._pipeline.process(
                        job.frames,
                        run_detection=job.run_detection,
                    )
                latest_timestamp = max(frame.timestamp_sec for frame in job.frames.values())
                result.debug.capture_latency_ms = max(0.0, (time.time() - latest_timestamp) * 1000.0)
                result.debug.dropped_input_batches = self._dropped_input_jobs
                self.result_ready.emit(result)
            except Exception as exc:  # pragma: no cover - UI surface area
                LOGGER.exception("Pipeline worker failed.")
                self.error.emit(str(exc))

        self.state_changed.emit("pipeline_stopped")
