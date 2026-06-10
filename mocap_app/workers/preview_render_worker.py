from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage

from mocap_app.io.calibration_io import CalibrationManager


LOGGER = logging.getLogger(__name__)


def resize_for_preview(frame_bgr: Any, max_width: int, max_height: int) -> Any:
    """Shrink a frame to fit the configured preview box for display only.

    The frame is scaled *down* (aspect ratio preserved) when it exceeds the
    ``max_width`` x ``max_height`` box. Frames that already fit are returned
    untouched: the preview canvas scales the pixmap up to the tile during paint
    (with smooth transform), so upscaling here would only burn CPU and memory
    without adding detail. A non-positive width/height means "unconstrained" on
    that axis; when both are unset the frame is returned untouched.
    """
    max_width = int(max_width or 0)
    max_height = int(max_height or 0)
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
    if not scale_candidates:
        return frame_bgr
    scale = min(scale_candidates)
    if scale <= 0 or scale >= 1.0:
        return frame_bgr
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    if target_width == width and target_height == height:
        return frame_bgr
    # INTER_AREA gives the best quality when shrinking.
    return cv2.resize(frame_bgr, (target_width, target_height), interpolation=cv2.INTER_AREA)


class PreviewRenderWorker(QObject):
    """Prepares display-ready preview images off the UI thread.

    For each source it replicates the on-screen preview pipeline (undistort →
    mirror → downscale → QImage) that previously ran on the UI thread every
    display tick. The QImage wraps the BGR data directly (Format_BGR888), so no
    per-frame colour conversion is needed. Detection and calibration are
    untouched: they still use the full-resolution capture frame, so this only
    affects what is drawn, not calibration quality.

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
        frame_bgr = resize_for_preview(frame_bgr, max_width, max_height)
        # Format_BGR888 lets Qt consume the OpenCV buffer as-is, skipping a
        # full-frame BGR-to-RGB conversion per camera per tick.
        frame_bgr = np.ascontiguousarray(frame_bgr)
        height, width, channels = frame_bgr.shape
        return QImage(
            frame_bgr.data, width, height, channels * width, QImage.Format.Format_BGR888
        ).copy()
