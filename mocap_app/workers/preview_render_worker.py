from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage

from mocap_app.io.calibration_io import CalibrationManager


LOGGER = logging.getLogger(__name__)


class PreviewRenderWorker(QObject):
    """Prepares display-ready preview images off the UI thread.

    For each source it replicates the on-screen preview pipeline (undistort →
    mirror → downscale → BGR-to-RGB → QImage) that previously ran on the UI
    thread every display tick. Detection and calibration are untouched: they
    still use the full-resolution capture frame, so this only affects what is
    drawn, not calibration quality.

    QImages may be constructed on a worker thread; the cheap ``QPixmap`` step is
    left to the UI thread. Each emitted QImage is ``.copy()``-ed so it owns its
    pixels and the source numpy buffers can be released.
    """

    rendered = Signal(object)

    def __init__(self, manager: CalibrationManager) -> None:
        super().__init__()
        self._manager = manager

    @Slot(object)
    def render(self, payload: object) -> None:
        try:
            request = dict(payload)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        frames: dict[str, Any] = request.get("frames") or {}
        undistort: dict[str, bool] = request.get("undistort") or {}
        mirror: dict[str, bool] = request.get("mirror") or {}
        bundle = request.get("bundle")
        max_width = int(request.get("max_width") or 0)
        max_height = int(request.get("max_height") or 0)

        images: dict[str, QImage] = {}
        for source_id, frame_bgr in frames.items():
            try:
                images[source_id] = self._render_one(
                    source_id=source_id,
                    frame_bgr=frame_bgr,
                    undistort=bool(undistort.get(source_id, False)),
                    mirror=bool(mirror.get(source_id, False)),
                    bundle=bundle,
                    max_width=max_width,
                    max_height=max_height,
                )
            except Exception:  # noqa: BLE001 - a render failure must not kill the worker
                LOGGER.exception("Preview render failed for source '%s'.", source_id)
        self.rendered.emit(
            {
                "images": images,
                "frame_indices": request.get("frame_indices") or {},
            }
        )

    def _render_one(
        self,
        source_id: str,
        frame_bgr: Any,
        undistort: bool,
        mirror: bool,
        bundle: Any,
        max_width: int,
        max_height: int,
    ) -> QImage:
        if undistort:
            frame_bgr = self._manager.undistort_frame(
                source_id=source_id, frame_bgr=frame_bgr, bundle=bundle
            )
        if mirror:
            frame_bgr = cv2.flip(frame_bgr, 1)
        frame_bgr = self._downscale(frame_bgr, max_width, max_height)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        height, width, channels = rgb.shape
        return QImage(
            rgb.data, width, height, channels * width, QImage.Format.Format_RGB888
        ).copy()

    @staticmethod
    def _downscale(frame_bgr: Any, max_width: int, max_height: int) -> Any:
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
