from __future__ import annotations

import io
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import cv2
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
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
from mocap_app.ui.main_window import MainWindow as FunctionalMainWindow
from ui.gui import Ui_MainWindow
from ui.guiStyle import apply_styles


# Maximum number of cameras that can be added to the preview grid at once.
_MAX_CAMERAS = 12


class _AggregateCheckBox(QCheckBox):
    """Checkbox that can display a partial (mixed) state for per-camera options,
    yet only toggles between checked and unchecked on a user click."""

    def nextCheckState(self) -> None:  # type: ignore[override]
        self.setCheckState(
            Qt.CheckState.Unchecked
            if self.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )


class ConsoleStream(io.StringIO):
    def __init__(self, console_widget: QPlainTextEdit) -> None:
        super().__init__()
        self._console_widget = console_widget

    def write(self, text: str) -> int:
        if text.strip():
            self._console_widget.appendPlainText(text.rstrip())
        return len(text)

    def flush(self) -> None:
        return None


class _PreviewCanvas(QLabel):
    def __init__(self, message: str = "Geen beeld", parent: QWidget | None = None) -> None:
        super().__init__(message, parent)
        self._frame_pixmap: QPixmap | None = None
        self._detection: ChessboardDetectionResult | None = None
        self._overlay_state: dict[str, Any] = {}
        self._status = ""
        self._sample_count = 0
        self._overlay_cache_key: tuple[Any, ...] | None = None
        self._overlay_cache: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setStyleSheet("background-color: black; color: white;")

    def set_frame_pixmap(self, pixmap: QPixmap) -> None:
        self._frame_pixmap = pixmap
        self.update()

    def set_overlay_data(
        self,
        detection: ChessboardDetectionResult | None,
        overlay_state: dict[str, Any] | None,
        status: str = "",
        sample_count: int = 0,
    ) -> None:
        self._detection = detection
        self._overlay_state = dict(overlay_state or {})
        self._status = status
        self._sample_count = int(sample_count)
        self.update()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._overlay_cache_key = None
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0))
        if self._frame_pixmap is None or self._frame_pixmap.isNull():
            painter.setPen(QtGui.QColor(245, 250, 255))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text() or "Geen beeld")
            painter.end()
            return

        # Smooth scaling avoids the jagged/shimmering look (which reads as
        # "smearing") when the frame is scaled to the tile.
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        image_rect = self._image_rect()
        painter.drawPixmap(image_rect, self._frame_pixmap, QtCore.QRectF(self._frame_pixmap.rect()))
        overlay = self._overlay_pixmap(image_rect)
        if overlay is not None:
            painter.drawPixmap(0, 0, overlay)
        painter.end()

    def _image_rect(self) -> QtCore.QRectF:
        if self._frame_pixmap is None or self._frame_pixmap.isNull():
            return QtCore.QRectF(self.rect())
        pixmap_size = self._frame_pixmap.size()
        if pixmap_size.width() <= 0 or pixmap_size.height() <= 0:
            return QtCore.QRectF(self.rect())
        scale = min(
            self.width() / float(pixmap_size.width()),
            self.height() / float(pixmap_size.height()),
        )
        draw_w = pixmap_size.width() * scale
        draw_h = pixmap_size.height() * scale
        x = (self.width() - draw_w) / 2.0
        y = (self.height() - draw_h) / 2.0
        return QtCore.QRectF(x, y, draw_w, draw_h)

    def _overlay_pixmap(self, image_rect: QtCore.QRectF) -> QPixmap | None:
        if self._detection is None or not self._overlay_state.get("overlay_enabled", False):
            self._overlay_cache_key = None
            self._overlay_cache = None
            return None
        key = self._overlay_key(image_rect)
        if self._overlay_cache_key == key and self._overlay_cache is not None:
            return self._overlay_cache

        overlay = QPixmap(self.size())
        overlay.fill(Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(overlay)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        self._draw_overlay(painter, image_rect)
        painter.end()

        self._overlay_cache_key = key
        self._overlay_cache = overlay
        return overlay

    def _overlay_key(self, image_rect: QtCore.QRectF) -> tuple[Any, ...]:
        detection = self._detection
        corners_sig: tuple[float, ...] = ()
        if detection is not None and detection.corners is not None:
            corners_sig = tuple(round(float(value), 1) for value in detection.corners.reshape(-1))
        state = self._overlay_state
        return (
            round(image_rect.x(), 1),
            round(image_rect.y(), 1),
            round(image_rect.width(), 1),
            round(image_rect.height(), 1),
            detection.source_id if detection else "",
            detection.pattern_type if detection else "",
            tuple(detection.image_size) if detection else (),
            bool(detection.found) if detection else False,
            int(detection.detected_corners) if detection else 0,
            round(float(detection.quality_score), 3) if detection else 0.0,
            round(float(detection.coverage_ratio), 4) if detection else 0.0,
            tuple(detection.diagnostics[:3]) if detection else (),
            corners_sig,
            tuple(tuple(row) for row in state.get("hit_counts", []) if isinstance(row, list)),
            tuple(state.get("grid_shape", (0, 0))),
            int(state.get("target_samples_per_cell", 0) or 0),
            int(state.get("sample_count", self._sample_count) or 0),
            state.get("accepted"),
            bool(state.get("mirror", False)),
            int(state.get("visited_cells", 0) or 0),
            int(state.get("total_cells", 0) or 0),
            round(float(state.get("coverage_ratio", 0.0) or 0.0), 4),
            bool(state.get("show_grid", True)),
            round(self._overlay_scale(), 3),
        )

    def _overlay_scale(self) -> float:
        try:
            return max(0.3, min(3.0, float(self._overlay_state.get("overlay_scale", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _draw_overlay(self, painter: QtGui.QPainter, image_rect: QtCore.QRectF) -> None:
        if self._detection is None:
            return
        # The textual feedback (source/samples/state/metrics) is shown in a label
        # above the image, not painted over the camera picture. Only the spatial
        # coverage grid and the detected-corner marks are drawn on the frame. The
        # coverage grid is intrinsics-specific, so it is suppressed in extrinsics
        # mode while the detection marks (and the text label) stay.
        if self._overlay_state.get("show_grid", True):
            self._draw_grid(painter, image_rect)
        self._draw_detection_marks(painter, image_rect)

    def _draw_grid(self, painter: QtGui.QPainter, image_rect: QtCore.QRectF) -> None:
        cols, rows = self._grid_shape()
        if cols <= 0 or rows <= 0:
            return
        target = max(1, int(self._overlay_state.get("target_samples_per_cell", 3) or 3))
        cell_w = image_rect.width() / cols
        cell_h = image_rect.height() / rows
        hit_counts = self._overlay_state.get("hit_counts", [])
        current_cells = self._current_detection_cells(cols, rows)

        for row in range(rows):
            for col in range(cols):
                hit_count = self._hit_count_for_cell(hit_counts, row, col, cols)
                if hit_count > 0:
                    painter.fillRect(
                        QtCore.QRectF(
                            image_rect.left() + col * cell_w,
                            image_rect.top() + row * cell_h,
                            cell_w,
                            cell_h,
                        ),
                        self._cell_tint(hit_count, target),
                    )

        dark_pen = QtGui.QPen(QtGui.QColor(20, 24, 28, 215), 3)
        light_pen = QtGui.QPen(QtGui.QColor(235, 245, 250, 190), 1)
        for col in range(1, cols):
            x = image_rect.left() + col * cell_w
            painter.setPen(dark_pen)
            painter.drawLine(QtCore.QPointF(x, image_rect.top()), QtCore.QPointF(x, image_rect.bottom()))
            painter.setPen(light_pen)
            painter.drawLine(QtCore.QPointF(x, image_rect.top()), QtCore.QPointF(x, image_rect.bottom()))
        for row in range(1, rows):
            y = image_rect.top() + row * cell_h
            painter.setPen(dark_pen)
            painter.drawLine(QtCore.QPointF(image_rect.left(), y), QtCore.QPointF(image_rect.right(), y))
            painter.setPen(light_pen)
            painter.drawLine(QtCore.QPointF(image_rect.left(), y), QtCore.QPointF(image_rect.right(), y))

        font = QtGui.QFont("Segoe UI")
        font.setBold(True)
        font.setPixelSize(max(7, int(max(11, min(24, int(min(cell_w, cell_h) * 0.18))) * self._overlay_scale())))
        painter.setFont(font)
        metrics_obj = QtGui.QFontMetrics(font)
        for row in range(rows):
            for col in range(cols):
                x0 = image_rect.left() + col * cell_w
                y0 = image_rect.top() + row * cell_h
                text = f"{self._hit_count_for_cell(hit_counts, row, col, cols)}/{target}"
                text_rect = QtCore.QRectF(
                    x0 + 4,
                    y0 + 4,
                    metrics_obj.horizontalAdvance(text) + 9,
                    metrics_obj.height() + 5,
                )
                painter.fillRect(text_rect, QtGui.QColor(0, 0, 0, 185))
                painter.setPen(QtGui.QColor(255, 255, 255))
                painter.drawText(text_rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)
                if (row, col) in current_cells:
                    painter.setPen(QtGui.QPen(QtGui.QColor(0, 220, 255), 2))
                    painter.drawRect(
                        QtCore.QRectF(x0 + 1, y0 + 1, max(1.0, cell_w - 2), max(1.0, cell_h - 2))
                    )

    def _draw_detection_marks(self, painter: QtGui.QPainter, image_rect: QtCore.QRectF) -> None:
        detection = self._detection
        if detection is None or not detection.found or detection.corners is None:
            return
        points = [self._map_point(float(point[0]), float(point[1]), image_rect) for point in detection.corners.reshape(-1, 2)]
        if detection.pattern_type != "charuco" and len(points) > 1:
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 165, 255), 2))
            for left, right in zip(points, points[1:]):
                painter.drawLine(left, right)
        painter.setBrush(QtGui.QColor(70, 220, 120))
        painter.setPen(QtGui.QPen(QtGui.QColor(12, 24, 18), 1))
        radius = max(1.5, min(5.0, image_rect.height() / 160.0) * self._overlay_scale())
        for point in points:
            painter.drawEllipse(point, radius, radius)

    def _grid_shape(self) -> tuple[int, int]:
        value = self._overlay_state.get("grid_shape", (6, 4))
        if isinstance(value, tuple) and len(value) == 2:
            return max(1, int(value[0])), max(1, int(value[1]))
        if isinstance(value, list) and len(value) == 2:
            return max(1, int(value[0])), max(1, int(value[1]))
        return 6, 4

    def _hit_count_for_cell(self, hit_counts: Any, row: int, col: int, cols: int) -> int:
        source_col = cols - 1 - col if self._overlay_state.get("mirror", False) else col
        if isinstance(hit_counts, list) and row < len(hit_counts):
            row_counts = hit_counts[row]
            if isinstance(row_counts, list) and source_col < len(row_counts):
                return int(row_counts[source_col])
        return 0

    def _cell_tint(self, hit_count: int, target: int) -> QtGui.QColor:
        if hit_count >= target:
            return QtGui.QColor(60, 185, 80, 40)
        if hit_count >= max(1, int(target * 2 / 3)):
            return QtGui.QColor(70, 205, 150, 38)
        return QtGui.QColor(95, 215, 240, 36)

    def _current_detection_cells(self, cols: int, rows: int) -> set[tuple[int, int]]:
        detection = self._detection
        if detection is None or not detection.found:
            return set()
        points: list[tuple[float, float]] = []
        if detection.corners is not None:
            points.extend((float(point[0]), float(point[1])) for point in detection.corners.reshape(-1, 2))
        if detection.board_bbox_px is not None:
            x_px, y_px, width, height = detection.board_bbox_px
            points.extend(
                [
                    (x_px, y_px),
                    (x_px + width, y_px),
                    (x_px, y_px + height),
                    (x_px + width, y_px + height),
                ]
            )
        if detection.board_center_px is not None:
            points.append(detection.board_center_px)
        return {self._point_to_grid_cell(x, y, cols, rows) for x, y in points}

    def _point_to_grid_cell(self, x_px: float, y_px: float, cols: int, rows: int) -> tuple[int, int]:
        detection = self._detection
        if detection is None:
            return 0, 0
        width, height = detection.image_size
        safe_width = max(1.0, float(width))
        safe_height = max(1.0, float(height))
        if self._overlay_state.get("mirror", False):
            x_px = safe_width - 1.0 - x_px
        col = min(max(int(x_px * cols / safe_width), 0), cols - 1)
        row = min(max(int(y_px * rows / safe_height), 0), rows - 1)
        return row, col

    def _map_point(self, x_px: float, y_px: float, image_rect: QtCore.QRectF) -> QtCore.QPointF:
        detection = self._detection
        if detection is None:
            return QtCore.QPointF(image_rect.left(), image_rect.top())
        width, height = detection.image_size
        safe_width = max(1.0, float(width))
        safe_height = max(1.0, float(height))
        if self._overlay_state.get("mirror", False):
            x_px = safe_width - 1.0 - x_px
        return QtCore.QPointF(
            image_rect.left() + (x_px / safe_width) * image_rect.width(),
            image_rect.top() + (y_px / safe_height) * image_rect.height(),
        )


class DesignedPreviewPopout(QDialog):
    rename_requested = Signal()
    overlay_toggled = Signal(bool)
    mirror_toggled = Signal(bool)
    undistort_toggled = Signal(bool)
    remove_requested = Signal()

    def __init__(self, title: str, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_pixmap: QPixmap | None = None
        self.setWindowTitle(title)
        self.resize(900, 620)

        self._title_button = QPushButton(display_name)
        self._title_button.setToolTip("Camera hernoemen")
        self._title_button.setMinimumWidth(110)
        self._overlay_button = QPushButton("Overlay")
        self._overlay_button.setCheckable(True)
        self._overlay_button.setToolTip("Detectie-overlay aan/uit voor deze camera")
        self._mirror_button = QPushButton("Spiegelen")
        self._mirror_button.setCheckable(True)
        self._mirror_button.setToolTip("Camerabeeld spiegelen")
        self._undistort_button = QPushButton("Corrigeren")
        self._undistort_button.setCheckable(True)
        self._undistort_button.setToolTip("Lenscorrectie-preview aan/uit")
        self._delete_button = QPushButton("X")
        self._delete_button.setToolTip("Deze bron uit de lijst verwijderen")
        self._delete_button.setFixedWidth(42)

        controls = QHBoxLayout()
        controls.setContentsMargins(6, 6, 6, 0)
        controls.setSpacing(8)
        controls.addWidget(self._title_button, stretch=1)
        controls.addWidget(self._overlay_button)
        controls.addWidget(self._mirror_button)
        controls.addWidget(self._undistort_button)
        controls.addWidget(self._delete_button)

        # Feedback text above the image instead of painted over the camera picture.
        self._status = QLabel("Wachten op livebeeld")
        self._status.setWordWrap(True)
        self._status.setContentsMargins(6, 0, 6, 0)
        self._status.setStyleSheet("QLabel { color: #1f2937; font-size: 12px; }")

        self._image = _PreviewCanvas("Geen beeld")
        # The image canvas must take all the vertical space left by the controls and
        # the status line; otherwise the box layout splits the height evenly and the
        # picture ends up letterboxed in the bottom portion of the window.
        self._image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._image, stretch=1)

        self._title_button.clicked.connect(self.rename_requested)
        self._overlay_button.toggled.connect(self.overlay_toggled)
        self._mirror_button.toggled.connect(self.mirror_toggled)
        self._undistort_button.toggled.connect(self.undistort_toggled)
        self._delete_button.clicked.connect(self.remove_requested)

    def set_display_name(self, name: str) -> None:
        self._title_button.setText(name)
        self._title_button.setToolTip(f"Rename camera: {name}")

    def set_overlay_active(self, active: bool) -> None:
        self._overlay_button.blockSignals(True)
        self._overlay_button.setChecked(active)
        self._overlay_button.blockSignals(False)

    def set_mirror_active(self, active: bool) -> None:
        self._mirror_button.blockSignals(True)
        self._mirror_button.setChecked(active)
        self._mirror_button.blockSignals(False)

    def set_undistort_active(self, active: bool) -> None:
        self._undistort_button.blockSignals(True)
        self._undistort_button.setChecked(active)
        self._undistort_button.blockSignals(False)

    def set_frame(
        self,
        pixmap: QPixmap,
        detection: ChessboardDetectionResult | None = None,
        overlay_state: dict[str, Any] | None = None,
        status: str = "",
        sample_count: int = 0,
    ) -> None:
        self._last_pixmap = pixmap
        if status:
            self._status.setText(status)
        self._image.set_frame_pixmap(pixmap)
        self._image.set_overlay_data(detection, overlay_state, status, sample_count)


class _AspectRatioBox(QWidget):
    """Centers a single child widget at a fixed aspect ratio.

    The camera preview uses this so it stays a tidy box (default 4:3) instead
    of stretching to fill the full, often very wide, workspace. The page
    background shows around the box rather than black letterbox bars.
    """

    def __init__(
        self,
        child: QWidget,
        ratio: float = 4.0 / 3.0,
        max_width: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ratio = max(0.1, float(ratio))
        self._max_width = max_width
        self._child = child
        # Layout-managed centering (instead of manual setGeometry) so the child
        # keeps a normal layout pass — important for complex children and to
        # avoid paint ghosting.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(child, alignment=Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        width = max(1, self.width())
        height = max(1, self.height())
        if width / height > self._ratio:
            child_h = height
            child_w = int(round(height * self._ratio))
        else:
            child_w = width
            child_h = int(round(width / self._ratio))
        # Cap the size so each camera stays a compact block in the grid instead
        # of one large field, even when only a few cameras are present.
        if self._max_width is not None and child_w > self._max_width:
            child_w = self._max_width
            child_h = int(round(child_w / self._ratio))
        # Size the child to exactly the aspect box; the layout centers it.
        self._child.setFixedSize(child_w, child_h)


class DesignedPreviewTile(QFrame):
    undistort_toggled = Signal(str, bool)
    preview_options_changed = Signal()
    remove_requested = Signal(str)
    name_changed = Signal(str, str)

    def __init__(self, source_id: str) -> None:
        super().__init__()
        self._source_id = source_id
        self._last_pixmap: QPixmap | None = None
        self._last_detection: ChessboardDetectionResult | None = None
        self._last_overlay_state: dict[str, Any] = {}
        self._last_status = ""
        self._last_sample_count = 0
        self._popout: DesignedPreviewPopout | None = None

        self._display_name = source_id
        # Compact controls so they fit inside a small camera card without the
        # labels getting clipped.
        self._title_button = QPushButton(source_id)
        self._title_button.setToolTip("Camera hernoemen")
        self._title_button.setMaximumWidth(150)
        self._open_button = QPushButton("Groot")
        self._open_button.setToolTip("Camerabeeld in apart venster openen")
        self._open_button.setCheckable(True)
        self._overlay_button = QPushButton("Overlay")
        self._overlay_button.setToolTip("Detectie-overlay aan/uit voor deze camera")
        self._overlay_button.setCheckable(True)
        self._overlay_button.setChecked(True)
        self._mirror_button = QPushButton("Spiegel")
        self._mirror_button.setToolTip("Camerabeeld spiegelen")
        self._mirror_button.setCheckable(True)
        self._undistort = QPushButton("Lens")
        self._undistort.setToolTip("Lenscorrectie-preview aan/uit")
        self._undistort.setCheckable(True)
        self._delete_button = QPushButton("X")
        self._delete_button.setToolTip("Deze bron uit de lijst verwijderen")
        self._delete_button.setFixedWidth(28)
        for _btn in (
            self._title_button,
            self._open_button,
            self._overlay_button,
            self._mirror_button,
            self._undistort,
            self._delete_button,
        ):
            _btn.setProperty("compact", True)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self._title_button)
        controls.addStretch(1)
        controls.addWidget(self._open_button)
        controls.addWidget(self._overlay_button)
        controls.addWidget(self._mirror_button)
        controls.addWidget(self._undistort)
        controls.addWidget(self._delete_button)

        # Fixed, compact video area so each camera stays a small card in the grid.
        self._image = _PreviewCanvas("Geen beeld")
        self._image.setFixedSize(380, 285)

        # Feedback text lives above the image (not drawn over the camera picture).
        self._status = QLabel("Wachten op livebeeld")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel { color: #1f2937; font-size: 11px; }")
        # Progress shows the percentage of captured samples relative to the max for
        # the active mode. Text is hidden; the bar turns green once the max is hit.
        self._sample_target = 0
        self._progress_full = False
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._refresh_progress()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._image)
        layout.addWidget(self._progress)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("camera-tile", True)
        # Size to content so the card stays compact and the grid can pack the
        # cards from the top-left instead of stretching one over the whole area.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._title_button.clicked.connect(self._rename_camera)
        self._open_button.clicked.connect(self._toggle_popout)
        self._delete_button.clicked.connect(lambda: self.remove_requested.emit(self._source_id))
        self._overlay_button.toggled.connect(self._toggle_overlay)
        self._mirror_button.toggled.connect(self._toggle_mirror)
        self._undistort.toggled.connect(self._toggle_undistort)

    @property
    def source_id(self) -> str:
        return self._source_id

    def undistort_enabled(self) -> bool:
        return self._undistort.isChecked()

    def overlay_enabled(self) -> bool:
        return self._overlay_button.isChecked()

    def mirror_enabled(self) -> bool:
        return self._mirror_button.isChecked()

    def display_name(self) -> str:
        return self._display_name.strip() or self._source_id

    def set_display_name(self, name: str) -> None:
        self._display_name = name.strip() or self._source_id
        self._title_button.setText(self._display_name)
        self._title_button.setToolTip(f"Rename camera: {self._display_name}")
        if self._popout is not None:
            self._popout.setWindowTitle(f"Live Feed - {self._display_name}")
            self._popout.set_display_name(self._display_name)

    def _emit_name_changed(self) -> None:
        self.name_changed.emit(self._source_id, self.display_name())

    def _rename_camera(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "Rename camera",
            "Camera name:",
            text=self.display_name(),
        )
        if not accepted:
            return
        self.set_display_name(name)
        self._emit_name_changed()

    def set_overlay_active(self, active: bool) -> None:
        self._overlay_button.blockSignals(True)
        self._overlay_button.setChecked(active)
        self._overlay_button.blockSignals(False)
        if self._popout is not None:
            self._popout.set_overlay_active(active)
        self.preview_options_changed.emit()

    def set_mirror_active(self, active: bool) -> None:
        self._mirror_button.blockSignals(True)
        self._mirror_button.setChecked(active)
        self._mirror_button.blockSignals(False)
        if self._popout is not None:
            self._popout.set_mirror_active(active)
        self.preview_options_changed.emit()

    _PROGRESS_FULL_STYLE = (
        "QProgressBar::chunk { background-color: #2e9e3f; }"
    )

    def set_sample_target(self, target: int) -> None:
        self._sample_target = max(int(target), 0)
        self._refresh_progress()

    def set_sample_count(self, count: int) -> None:
        self._last_sample_count = max(int(count), 0)
        self._refresh_progress()

    def _refresh_progress(self) -> None:
        count = max(int(self._last_sample_count), 0)
        target = int(self._sample_target)
        if target > 0:
            percent = min(int(round(count / target * 100)), 100)
            full = count >= target
        else:
            # No limit configured: show raw progress, never mark as "full".
            percent = min(count, 100)
            full = False
        self._progress.setValue(percent)
        # Only re-apply the stylesheet when the full/not-full state flips, otherwise
        # every preview frame would force a style re-polish on the bar.
        if full != self._progress_full:
            self._progress_full = full
            self._progress.setStyleSheet(self._PROGRESS_FULL_STYLE if full else "")
        self._progress.setToolTip(f"{count}/{target}" if target > 0 else f"{count} samples")

    def set_frame(
        self,
        frame_bgr: Any,
        status: str,
        sample_count: int,
        detection: ChessboardDetectionResult | None = None,
        overlay_state: dict[str, Any] | None = None,
    ) -> None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        self.set_frame_image(image, status, sample_count, detection, overlay_state)

    def set_frame_image(
        self,
        image: QImage,
        status: str,
        sample_count: int,
        detection: ChessboardDetectionResult | None = None,
        overlay_state: dict[str, Any] | None = None,
    ) -> None:
        # The frame is already undistorted, mirrored, downscaled and converted
        # to RGB on the preview-render worker thread, so the UI thread only does
        # the cheap QPixmap conversion and paint.
        self._last_pixmap = QPixmap.fromImage(image)
        self._last_detection = detection
        self._last_overlay_state = dict(overlay_state or {})
        self._last_status = status
        self._last_sample_count = int(sample_count)
        self._status.setText(status)
        self.set_sample_count(sample_count)
        self._image.set_frame_pixmap(self._last_pixmap)
        self._image.set_overlay_data(detection, self._last_overlay_state, status, sample_count)
        if self._popout is not None:
            self._popout.set_frame(
                self._last_pixmap,
                detection=self._last_detection,
                overlay_state=self._last_overlay_state,
                status=self._last_status,
                sample_count=self._last_sample_count,
            )

    def _toggle_popout(self, checked: bool) -> None:
        if checked:
            self._open_popout()
        elif self._popout is not None:
            self._popout.close()

    def _toggle_undistort(self, checked: bool) -> None:
        if self._popout is not None:
            self._popout.set_undistort_active(checked)
        self.undistort_toggled.emit(self._source_id, checked)
        self.preview_options_changed.emit()

    def _toggle_overlay(self, checked: bool) -> None:
        if self._popout is not None:
            self._popout.set_overlay_active(checked)
        self.preview_options_changed.emit()

    def _toggle_mirror(self, checked: bool) -> None:
        if self._popout is not None:
            self._popout.set_mirror_active(checked)
        self.preview_options_changed.emit()

    def _set_undistort_from_popout(self, checked: bool) -> None:
        self._undistort.blockSignals(True)
        self._undistort.setChecked(checked)
        self._undistort.blockSignals(False)
        self.undistort_toggled.emit(self._source_id, checked)
        self.preview_options_changed.emit()

    def _set_overlay_from_popout(self, checked: bool) -> None:
        self._overlay_button.blockSignals(True)
        self._overlay_button.setChecked(checked)
        self._overlay_button.blockSignals(False)
        self.preview_options_changed.emit()

    def _set_mirror_from_popout(self, checked: bool) -> None:
        self._mirror_button.blockSignals(True)
        self._mirror_button.setChecked(checked)
        self._mirror_button.blockSignals(False)
        self.preview_options_changed.emit()

    def close_popout(self) -> None:
        if self._popout is not None:
            self._popout.close()

    def _open_popout(self) -> None:
        if self._popout is None:
            self._popout = DesignedPreviewPopout(f"Live Feed - {self.display_name()}", self.display_name(), self)
            self._popout.rename_requested.connect(self._rename_camera)
            self._popout.overlay_toggled.connect(self._set_overlay_from_popout)
            self._popout.mirror_toggled.connect(self._set_mirror_from_popout)
            self._popout.undistort_toggled.connect(self._set_undistort_from_popout)
            self._popout.remove_requested.connect(lambda: self.remove_requested.emit(self._source_id))
            self._popout.finished.connect(self._on_popout_closed)
            self._popout.set_overlay_active(self._overlay_button.isChecked())
            self._popout.set_mirror_active(self._mirror_button.isChecked())
            self._popout.set_undistort_active(self._undistort.isChecked())
        if self._last_pixmap is not None:
            self._popout.set_frame(
                self._last_pixmap,
                detection=self._last_detection,
                overlay_state=self._last_overlay_state,
                status=self._last_status,
                sample_count=self._last_sample_count,
            )
        self._popout.show()
        self._popout.raise_()
        self._popout.activateWindow()
        self._open_button.blockSignals(True)
        self._open_button.setChecked(True)
        self._open_button.blockSignals(False)

    def _on_popout_closed(self) -> None:
        self._popout = None
        self._open_button.blockSignals(True)
        self._open_button.setChecked(False)
        self._open_button.blockSignals(False)

class DesignedCalibrationPanel(QtCore.QObject):
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
    # intrinsics_quality, intrinsics_coverage_ratio, extrinsics_quality, extrinsics_coverage_ratio
    acceptance_thresholds_changed = Signal(float, float, float, float)
    workflow_mode_changed = Signal(str)
    spatial_grid_changed = Signal(int, int)
    sources_changed = Signal(object)
    preview_options_changed = Signal()
    record_toggled = Signal(bool)
    export_preview_requested = Signal(str)
    export_requested = Signal(str)
    # Emitted by the single "Start kalibratie"/"Stop kalibratie" button shown when
    # auto-navigation is enabled; drives the fully automatic calibration chain.
    start_calibration_requested = Signal()
    stop_calibration_requested = Signal()

    def __init__(self, window: "DesignedMainWindow", default_camera_csv: str, default_fps: float) -> None:
        super().__init__(window)
        self.window = window
        self._tiles: dict[str, DesignedPreviewTile] = {}
        self._tile_boxes: dict[str, _AspectRatioBox] = {}
        self._source_order: list[str] = []
        self._video_sources: list[CameraSourceConfig] = []
        self._detected_cameras: list[CameraProbeResult] = []
        self._camera_probe_running = False
        self._advanced_scroll: QScrollArea | None = None
        self._live_active = False
        self._active_cameras = 0
        self._project_root = Path.cwd()
        self._icon_provider = QFileIconProvider()
        self._camera_names = dict(getattr(self.window._config, "camera_labels", {}) or {})

        # Diagnostics: accumulate how long each capture mode has been active.
        # ``_active_timed_mode`` is the mode currently running (if any); the timer
        # ticks once a second so the displayed time advances live.
        self._mode_time_accum: dict[str, float] = {"intrinsics": 0.0, "sync_extrinsics": 0.0}
        self._active_timed_mode: str | None = None
        self._mode_time_started_at = 0.0
        self._mode_time_timer = QtCore.QTimer(self)
        self._mode_time_timer.setInterval(1000)
        self._mode_time_timer.timeout.connect(self._refresh_mode_time_diagnostics)

        self._setup_navigation()
        self._setup_console()
        self._setup_camera_page(default_camera_csv, default_fps)
        self._setup_results_page()
        self._setup_directory_page()
        self._setup_diagnostics_page()
        self._setup_advanced_page(default_camera_csv, default_fps)
        # Now that the auto-navigation checkbox exists, set the initial calibration
        # control layout (single Start button vs. per-phase cards).
        self._update_calibration_controls_visibility()
        self._connect_designed_actions()
        self.switch_page(0)

    def _setup_navigation(self) -> None:
        self._nav_buttons = [
            self.window.btn_home,
            self.window.btn_cameras,
            self.window.btn_results,
            self.window.btn_directory,
            self.window.btn_diagnostics,
            self.window.btn_advanced_settings,
        ]
        for index, button in enumerate(self._nav_buttons):
            button.clicked.connect(lambda _checked=False, page=index: self.switch_page(page))

    def _setup_console(self) -> None:
        self.window.plaintextedit_console.setReadOnly(True)
        self.window.lineedit_console_input.returnPressed.connect(self._handle_console_input)
        sys.stdout = ConsoleStream(self.window.plaintextedit_console)
        sys.stderr = ConsoleStream(self.window.plaintextedit_console)

    def uses_qt_preview_overlay(self) -> bool:
        return True

    def _setup_camera_page(self, default_camera_csv: str, default_fps: float) -> None:
        self._setup_camera_splitter()
        self.window.spin_cap_fps.setRange(1, 120)
        self.window.spin_cap_fps.setValue(max(1, int(round(default_fps))))
        self.window.btn_cap_intrinsics_start.setCheckable(True)
        self.window.btn_cap_extrinsics_start.setCheckable(True)

        # Single calibration button that replaces the per-phase Intrinsics/
        # Extrinsics cards when auto-navigation is on. It is placed in the exact
        # same grid cells those cards occupy after _compact_camera_controls()
        # relayouts them (rows 0-1, columns 2-3), so it fills that whole
        # rectangle. Visibility is mutually exclusive, so the overlap is never
        # visible at the same time.
        self._start_calibration_button = QPushButton("Start kalibratie")
        self._start_calibration_button.setObjectName("btn_cap_start_calibration")
        self._start_calibration_button.setCheckable(True)
        self._start_calibration_button.setProperty("accent", True)
        self._start_calibration_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._start_calibration_button.toggled.connect(self._toggle_calibration_run)
        frame_layout = self.window.frame.layout()
        if frame_layout is not None:
            frame_layout.addWidget(self._start_calibration_button, 0, 2, 2, 2)

        self.window.combo_cap_pattern.blockSignals(True)
        self.window.combo_cap_pattern.clear()
        self.window.combo_cap_pattern.addItem("Chessboard", "chessboard")
        self.window.combo_cap_pattern.addItem("Charuco", "charuco")
        self.window.combo_cap_pattern.blockSignals(False)

        self.window.btn_camera_detect.clicked.connect(
            lambda: self.probe_cameras_requested.emit(int(self._probe_max_spin.value()))
        )
        self.window.btn_camera_start_live.clicked.connect(self._emit_start_live)
        self.window.btn_camera_stop_live.clicked.connect(self.stop_live_requested)
        self.window.btn_camera_record.toggled.connect(self._toggle_record)
        self.window.btn_camera_load_video.clicked.connect(self._load_video_sources)

        self._camera_scroll = QScrollArea()
        self._camera_scroll.setWidgetResizable(True)
        self._camera_scroll_content = QWidget()
        self._camera_grid = QGridLayout(self._camera_scroll_content)
        # Column/row stretch is configured per layout in _rebuild_camera_grid so
        # the tiles always fill the available preview area for any camera count.
        self._camera_scroll.setWidget(self._camera_scroll_content)
        self.window.gridLayout_6.addWidget(self._camera_scroll)

        self._add_camera_button = QPushButton("+ Camera Toevoegen")
        self._add_camera_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._add_camera_button.clicked.connect(self._append_camera_source)
        # Open with an empty preview page: the user runs a scan and then adds
        # detected cameras one by one via the add-camera button.
        self._source_csv = ""
        self.set_sources([])
        # set_sources([]) early-returns when already empty, so build the grid
        # once explicitly to place the add-camera button.
        self._rebuild_camera_grid()

    def _setup_camera_splitter(self) -> None:
        page_layout = self.window.page_cameras.layout()
        if page_layout is None or getattr(self.window, "_camera_splitter", None) is not None:
            return

        page_layout.removeWidget(self.window.frame)
        page_layout.removeWidget(self.window.frame_cam)

        splitter = QSplitter(Qt.Orientation.Vertical, self.window.page_cameras)
        splitter.setObjectName("splitter_camera_page")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.window.frame)
        splitter.addWidget(self.window.frame_cam)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([116, 720])
        page_layout.addWidget(splitter, stretch=1)
        self.window._camera_splitter = splitter

    def _setup_results_page(self) -> None:
        self._intrinsics_text = self._plain_text_in_frame(self.window.frame_res_intrinsic_results)
        self._extrinsics_text = self._plain_text_in_frame(self.window.frame_res_extrinsics_results)
        self._frames_text = self._plain_text_in_frame(self.window.frame_res_aantal_frames)
        self._camera_info_text = self._plain_text_in_frame(self.window.frame_res_camera_info)
        self._error_text = self._plain_text_in_frame(self.window.frame_res_error)

        existing_preview = self.window.frame_res_preview_tmol.findChild(QPlainTextEdit)
        self._tmol_preview = existing_preview or QPlainTextEdit()
        self._tmol_preview.setReadOnly(True)
        self._tmol_preview.setPlainText("No export preview available yet.")
        self._tmol_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._tmol_preview.setFont(QtGui.QFont("Consolas", 9))
        if existing_preview is None:
            preview_layout = self.window.frame_res_preview_tmol.layout()
            if preview_layout is None:
                preview_layout = QVBoxLayout(self.window.frame_res_preview_tmol)
                preview_layout.setContentsMargins(4, 4, 4, 4)
            preview_layout.addWidget(self._tmol_preview)

        # Format selector (TOML / JSON) for both preview and export.
        self._export_format_combo = QComboBox()
        self._export_format_combo.addItem("TOML", "toml")
        self._export_format_combo.addItem("JSON", "json")
        export_bar = self.window.frame_4.layout()
        if export_bar is not None:
            format_label = QLabel("Formaat:")
            insert_at = max(0, export_bar.indexOf(self.window.btn_res_show_tmol))
            export_bar.insertWidget(insert_at, format_label)
            export_bar.insertWidget(insert_at + 1, self._export_format_combo)
        self.window.btn_res_show_tmol.setText("Preview")
        self.window.btn_res_show_tmol.setToolTip("Toon de huidige kalibratie in het gekozen formaat")
        self.window.export_toml.setText("Export")
        self.window.export_toml.setToolTip("Exporteer de huidige kalibratie naar een bestand")

        self.window.btn_res_show_tmol.clicked.connect(self._request_export_preview)
        self.window.pushButton.clicked.connect(lambda: self.window.stackedWidget_2.setCurrentIndex(0))
        self.window.export_toml.clicked.connect(self._request_export)

    def _setup_directory_page(self) -> None:
        layout = QVBoxLayout(self.window.frame_directory)
        layout.setContentsMargins(10, 10, 10, 10)

        toolbar = QHBoxLayout()
        self._directory_up_button = QPushButton("Omhoog")
        self._directory_down_button = QPushButton("Omlaag")
        self._directory_path = QLineEdit(str(self._project_root))
        self._directory_path.setReadOnly(True)
        self._directory_refresh_button = QPushButton("Vernieuwen")
        self._directory_browse_button = QPushButton("Bladeren...")
        toolbar.addWidget(self._directory_up_button)
        toolbar.addWidget(self._directory_down_button)
        toolbar.addWidget(QLabel("Startpad:"))
        toolbar.addWidget(self._directory_path, stretch=1)
        toolbar.addWidget(self._directory_refresh_button)
        toolbar.addWidget(self._directory_browse_button)

        self._directory_tree = QTreeWidget()
        self._directory_tree.setHeaderLabels(["Naam", "Type", "Gewijzigd"])
        self._directory_tree.setColumnCount(3)
        # Let the Name column take the available width so paths stay readable;
        # Type and Gewijzigd only take what their content needs.
        _dir_header = self._directory_tree.header()
        _dir_header.setStretchLastSection(False)
        _dir_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        _dir_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        _dir_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._directory_tree.setColumnWidth(0, 420)
        self._directory_tree.itemExpanded.connect(self._on_directory_item_expanded)
        self._directory_tree.itemDoubleClicked.connect(lambda item, _column: self._go_down_directory(item))

        layout.addLayout(toolbar)
        layout.addWidget(self._directory_tree)

        self._directory_up_button.clicked.connect(self._go_up_directory)
        self._directory_down_button.clicked.connect(self._go_down_directory)
        self._directory_refresh_button.clicked.connect(lambda: self.load_root_directory(self._project_root))
        self._directory_browse_button.clicked.connect(self._browse_directory)
        self.load_root_directory(self._project_root)

    def _setup_diagnostics_page(self) -> None:
        for widget in [
            self.window.text_diag_current_fps,
            self.window.text_diag_dropped_frames,
            self.window.text_diag_used_cams,
            self.window.text_diag_Intrinsics_time,
            self.window.text_diag_extrinsics_time,
            self.window.text_diag_total_time,
        ]:
            widget.setReadOnly(True)
        self.window.text_diag_current_fps.setPlainText(str(self.window.spin_cap_fps.value()))
        self.window.text_diag_dropped_frames.setPlainText("0")
        self.window.text_diag_used_cams.setPlainText("0")
        self.window.text_diag_Intrinsics_time.setPlainText("-")
        self.window.text_diag_extrinsics_time.setPlainText("-")
        self.window.text_diag_total_time.setPlainText("-")

    # --- Diagnostics: per-mode active time -------------------------------------
    def _accumulate_mode_time(self) -> None:
        """Fold the time spent in the currently active mode into its accumulator
        and advance the start marker, so repeated calls keep the running total
        correct while a mode stays active."""
        if self._active_timed_mode is None:
            return
        now = time.perf_counter()
        self._mode_time_accum[self._active_timed_mode] += now - self._mode_time_started_at
        self._mode_time_started_at = now

    def _start_mode_timing(self, mode: str) -> None:
        if mode not in self._mode_time_accum:
            return
        # Switching modes: bank the previous mode's elapsed time first.
        self._accumulate_mode_time()
        self._active_timed_mode = mode
        self._mode_time_started_at = time.perf_counter()
        if not self._mode_time_timer.isActive():
            self._mode_time_timer.start()
        self._refresh_mode_time_diagnostics()

    def _stop_mode_timing(self) -> None:
        if self._active_timed_mode is None:
            return
        self._accumulate_mode_time()
        self._active_timed_mode = None
        self._mode_time_timer.stop()
        self._refresh_mode_time_diagnostics()

    def _reset_mode_timing(self) -> None:
        self._mode_time_timer.stop()
        self._active_timed_mode = None
        self._mode_time_accum = {"intrinsics": 0.0, "sync_extrinsics": 0.0}
        self.window.text_diag_Intrinsics_time.setPlainText("-")
        self.window.text_diag_extrinsics_time.setPlainText("-")
        self.window.text_diag_total_time.setPlainText("-")

    def _refresh_mode_time_diagnostics(self) -> None:
        self._accumulate_mode_time()
        intrinsics = self._mode_time_accum["intrinsics"]
        extrinsics = self._mode_time_accum["sync_extrinsics"]
        self.window.text_diag_Intrinsics_time.setPlainText(self._format_duration(intrinsics))
        self.window.text_diag_extrinsics_time.setPlainText(self._format_duration(extrinsics))
        self.window.text_diag_total_time.setPlainText(self._format_duration(intrinsics + extrinsics))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = int(seconds)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _setup_advanced_page(self, default_camera_csv: str, default_fps: float) -> None:
        self.window.doubleSpinBox.setRange(1.0, 500.0)
        self.window.doubleSpinBox.setDecimals(2)
        self.window.doubleSpinBox.setSingleStep(0.5)
        self.window.doubleSpinBox.setValue(24.0)

        self._sources_input = QLineEdit("")
        self._preview_fps_spin = self._double_spin(1.0, 120.0, min(default_fps, 30.0), 1.0, 1)
        self._detect_hz_spin = self._double_spin(0.5, 20.0, 5.0, 0.5, 1)
        self._capture_resolution_combo = QComboBox()
        self._capture_resolution_combo.addItem("Auto", (0, 0))
        self._capture_resolution_combo.addItem("640 x 480", (640, 480))
        self._capture_resolution_combo.addItem("960 x 540", (960, 540))
        self._capture_resolution_combo.addItem("1280 x 720", (1280, 720))
        self._capture_resolution_combo.addItem("1920 x 1080", (1920, 1080))
        # Default capture at 1080p for sharp, detailed calibration frames; the
        # preview is downscaled separately so the live view stays smooth.
        self._capture_resolution_combo.setCurrentIndex(
            self._capture_resolution_combo.findData((1920, 1080))
        )
        self._preview_resolution_combo = QComboBox()
        self._preview_resolution_combo.addItem("Auto", (0, 0))
        self._preview_resolution_combo.addItem("640 x 480", (640, 480))
        self._preview_resolution_combo.addItem("960 x 540", (960, 540))
        self._preview_resolution_combo.addItem("1280 x 720", (1280, 720))
        self._preview_resolution_combo.addItem("1920 x 1080", (1920, 1080))
        # Downscale the on-screen preview to 720p so display stays fast and
        # low-latency, independent of the (higher) capture resolution.
        self._preview_resolution_combo.setCurrentIndex(
            self._preview_resolution_combo.findData((1280, 720))
        )
        self._probe_max_spin = self._spin(1, 20, 10)

        self._chess_cols_spin = self._spin(2, 30, 9)
        self._chess_rows_spin = self._spin(2, 30, 6)
        self._charuco_x_spin = self._spin(2, 30, 5)
        self._charuco_y_spin = self._spin(2, 30, 3)
        self._charuco_square_spin = self._double_spin(1.0, 500.0, 77.0, 0.5, 2)
        self._charuco_marker_spin = self._double_spin(1.0, 500.0, 61.0, 0.5, 2)
        self._charuco_square_spin.setSuffix(" mm")
        self._charuco_marker_spin.setSuffix(" mm")

        self._workflow_combo = QComboBox()
        self._workflow_combo.addItem("Intrinsics", "intrinsics")
        self._workflow_combo.addItem("Sync / Extrinsics", "sync_extrinsics")
        self._overlay_checkbox = _AggregateCheckBox("Show Detection Overlay")
        self._overlay_checkbox.setTristate(True)
        self._overlay_checkbox.setChecked(True)
        self._mirror_checkbox = _AggregateCheckBox("Mirror Preview")
        self._mirror_checkbox.setTristate(True)
        self._auto_capture_checkbox = QCheckBox("Auto Capture Valid Samples")
        # When enabled, opening a new project jumps to the Camera tab and a
        # finished (extrinsics) calibration jumps to the Results tab.
        self._auto_navigate_checkbox = QCheckBox("Automatisch tussen tabbladen wisselen")
        self._auto_navigate_checkbox.setChecked(True)
        self._auto_navigate_checkbox.setToolTip(
            "Nieuw project opent het Camera-tabblad; een afgeronde kalibratie opent het Resultaten-tabblad."
        )
        # Toggling auto-navigation swaps the single Start-kalibratie button for the
        # per-phase Intrinsics/Extrinsics cards (and back).
        self._auto_navigate_checkbox.toggled.connect(
            lambda _checked: self._update_calibration_controls_visibility()
        )
        self._auto_cooldown_spin = self._double_spin(0.1, 10.0, 0.33, 0.01, 2)
        # Separate sample budgets per mode: intrinsics needs many per-camera poses,
        # extrinsics only needs a handful of synchronized sets shared between cameras.
        # The intrinsics budget is a dropdown of totals that divide evenly over the
        # spatial grid (= samples-per-cell x cells); options are rebuilt below once
        # the grid spinboxes exist. Otherwise the progress bar can read "full"
        # before every cell has its samples.
        self._auto_max_intrinsics_combo = QComboBox()
        self._auto_max_intrinsics_combo.currentIndexChanged.connect(self._on_intrinsics_max_changed)
        self._auto_max_extrinsics_spin = self._spin(0, 1000, 40)
        self._auto_max_extrinsics_spin.setSpecialValueText("No limit")
        self._auto_max_extrinsics_spin.setSuffix(" samples")
        # Independent acceptance thresholds: intrinsics is strict per-camera,
        # extrinsics covers synchronized multi-camera sets (usually more lenient).
        self._intrinsics_quality_spin = self._double_spin(0.0, 1.0, 0.25, 0.05, 2)
        self._intrinsics_coverage_spin = self._double_spin(0.0, 25.0, 1.8, 0.2, 1)
        self._intrinsics_coverage_spin.setSuffix(" %")
        self._extrinsics_quality_spin = self._double_spin(0.0, 1.0, 0.15, 0.05, 2)
        self._extrinsics_coverage_spin = self._double_spin(0.0, 25.0, 1.0, 0.2, 1)
        self._extrinsics_coverage_spin.setSuffix(" %")
        self._grid_cols_spin = self._spin(1, 20, 6)
        self._grid_rows_spin = self._spin(1, 20, 4)
        # Keep the intrinsics sample budget divisible over the grid: track the
        # chosen samples-per-cell and rebuild the dropdown when the grid changes.
        self._intrinsics_per_cell_target = 3
        self._grid_cols_spin.valueChanged.connect(lambda _v: self._rebuild_intrinsics_max_options())
        self._grid_rows_spin.valueChanged.connect(lambda _v: self._rebuild_intrinsics_max_options())
        self._rebuild_intrinsics_max_options()

        self._auto_status = QLabel("Auto capture off.")
        self._probe_status = QLabel("Camera scan: not run yet.")
        self._probe_status.setWordWrap(True)
        self._feedback = QLabel("Ready.")
        self._feedback.setWordWrap(True)
        self._warnings = QPlainTextEdit()
        self._warnings.setReadOnly(True)
        self._warnings.setMinimumHeight(92)

        self._start_live_button = QPushButton("Live starten")
        self._stop_live_button = QPushButton("Live stoppen")
        self._probe_button = QPushButton("Camera's zoeken")
        self._capture_button = QPushButton("Capture Intrinsics Sample(s)")
        self._capture_sync_button = QPushButton("Capture Sync Set(s)")
        self._start_auto_button = QPushButton("Start Auto Capture")
        self._apply_live_settings_button = QPushButton("Apply Live Source Settings")
        self._apply_chessboard_button = QPushButton("Apply Chessboard Settings")
        self._apply_charuco_button = QPushButton("Apply ChArUco Settings")
        self._apply_workflow_button = QPushButton("Apply Workflow Settings")
        self._save_profile_button = QPushButton("Save Profile")
        self._load_profile_button = QPushButton("Load Profile")
        self._reset_samples_button = QPushButton("Reset Samples")

        self._compact_advanced_controls()

        advanced_root = QWidget()
        advanced_layout = QVBoxLayout(advanced_root)
        advanced_layout.setContentsMargins(4, 4, 4, 4)
        advanced_layout.setSpacing(8)
        advanced_layout.addWidget(self._section("Live source settings", self._live_settings_form()))
        advanced_layout.addWidget(self._section("Chessboard settings", self._chessboard_settings_form()))
        advanced_layout.addWidget(self._section("ChArUco settings", self._charuco_settings_form()))
        advanced_layout.addWidget(self._section("Workflow and thresholds", self._workflow_settings_form()))
        advanced_layout.addWidget(self._section("Navigatie", self._navigation_settings_form()))
        advanced_layout.addWidget(self._section("Advanced actions", self._advanced_actions_widget()))
        advanced_layout.addWidget(self._section("Status and warnings", self._status_widget()))
        advanced_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(advanced_root)
        self._advanced_scroll = scroll
        page_layout = self.window.page_advanced_settings.layout()
        if page_layout is None:
            page_layout = QVBoxLayout(self.window.page_advanced_settings)
            page_layout.setContentsMargins(0, 0, 0, 0)
        else:
            self._clear_layout(page_layout)
        page_layout.addWidget(scroll)

        self._connect_advanced_controls()

    def _compact_advanced_controls(self) -> None:
        self._compact_field(self._sources_input, 360)
        for widget in [
            self._capture_resolution_combo,
            self._preview_resolution_combo,
            self.window.combo_cap_pattern,
        ]:
            self._compact_field(widget, 180)
            self._wheel_scrolls_page(widget)

        for widget in [
            self.window.spin_cap_fps,
            self._preview_fps_spin,
            self._detect_hz_spin,
            self._probe_max_spin,
            self._chess_cols_spin,
            self._chess_rows_spin,
            self.window.doubleSpinBox,
            self._charuco_x_spin,
            self._charuco_y_spin,
            self._charuco_square_spin,
            self._charuco_marker_spin,
            self._auto_cooldown_spin,
            self._auto_max_intrinsics_combo,
            self._auto_max_extrinsics_spin,
            self._intrinsics_quality_spin,
            self._intrinsics_coverage_spin,
            self._extrinsics_quality_spin,
            self._extrinsics_coverage_spin,
            self._grid_cols_spin,
            self._grid_rows_spin,
        ]:
            self._compact_field(widget, 120)
            self._wheel_scrolls_page(widget)

        for button in [
            self._apply_live_settings_button,
            self._apply_chessboard_button,
            self._apply_charuco_button,
            self._apply_workflow_button,
        ]:
            button.setMaximumWidth(280)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _compact_field(self, widget: QWidget, width: int) -> None:
        widget.setFixedWidth(width)
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _wheel_scrolls_page(self, widget: QWidget) -> None:
        widget.setProperty("wheel-scrolls-advanced-page", True)
        widget.installEventFilter(self)

    def _connect_designed_actions(self) -> None:
        self.window.btn_newproject.clicked.connect(self.new_project_requested)
        self.window.btn_loadproject.clicked.connect(self._browse_directory)
        self.window.actionNew_project.triggered.connect(self.new_project_requested)
        self.window.actionOpen_project.triggered.connect(self._browse_directory)
        self.window.actionQuit.triggered.connect(self.window.close)
        self.window.actionOpen_documentation.triggered.connect(self._open_documentation)

        self.window.btn_cap_intrinsics_start.clicked.connect(self._toggle_intrinsics_start)
        self.window.btn_cap_extrinsics_start.clicked.connect(self._toggle_extrinsics_start)
        self.window.btn_cap_calculate_intrinsics.clicked.connect(self.solve_requested)
        self.window.btn_cap_calculate_extrinsics.clicked.connect(self._emit_solve_extrinsics)
        self.window.btn_cap_reset_calibration.clicked.connect(self._emit_reset)
        self.window.combo_cap_pattern.currentIndexChanged.connect(self._emit_pattern_changed)
        self.window.spin_cap_fps.valueChanged.connect(self._emit_runtime_tuning_changed)
        # The FPS dropdown is the capture FPS, which only takes effect on (re)start.
        self.window.spin_cap_fps.valueChanged.connect(self._warn_capture_restart_needed)

    def _connect_advanced_controls(self) -> None:
        self._start_live_button.clicked.connect(self._emit_start_live)
        self._stop_live_button.clicked.connect(self.stop_live_requested)
        self._probe_button.clicked.connect(lambda: self.probe_cameras_requested.emit(int(self._probe_max_spin.value())))
        self._capture_button.clicked.connect(self._capture_intrinsics_sample)
        self._capture_sync_button.clicked.connect(self._capture_sync_sample)
        self._start_auto_button.clicked.connect(self.auto_capture_start_requested)
        self._apply_live_settings_button.clicked.connect(self._apply_live_settings)
        self._apply_chessboard_button.clicked.connect(lambda: self._apply_board_settings("Chessboard settings applied."))
        self._apply_charuco_button.clicked.connect(lambda: self._apply_board_settings("ChArUco settings applied."))
        self._apply_workflow_button.clicked.connect(self._apply_workflow_settings)
        self._save_profile_button.clicked.connect(self.save_profile_requested)
        self._load_profile_button.clicked.connect(self.load_profile_requested)
        self._reset_samples_button.clicked.connect(self._emit_reset)
        self.window.doubleSpinBox.valueChanged.connect(lambda _value: None)
        # Preview-only settings can be applied to a running live session
        # immediately (they only affect display downscaling, the preview refresh
        # rate and the detection cadence — not the camera itself), so the preview
        # always reflects the configured Preview Resolution instead of staying at
        # the resolution it started with.
        self._preview_resolution_combo.currentIndexChanged.connect(self._emit_runtime_tuning_changed)
        self._preview_fps_spin.valueChanged.connect(self._emit_runtime_tuning_changed)
        self._detect_hz_spin.valueChanged.connect(self._emit_runtime_tuning_changed)
        # Reflect the current per-camera overlay/mirror state back into the
        # advanced checkboxes whenever a tile option changes.
        self.preview_options_changed.connect(self._sync_advanced_checkboxes_from_tiles)
        self._sync_advanced_checkboxes_from_tiles()

    def _plain_text_in_frame(self, frame: QFrame) -> QPlainTextEdit:
        existing = frame.findChild(QPlainTextEdit)
        if existing is not None:
            existing.setReadOnly(True)
            existing.setPlainText("-")
            return existing
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("-")
        layout = frame.layout()
        if layout is None:
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(text)
        return text

    def _clear_layout(self, layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
            elif child_layout is not None:
                self._clear_layout(child_layout)

    def _double_spin(
        self,
        minimum: float,
        maximum: float,
        value: float,
        step: float,
        decimals: int,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(value)
        return spin

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _section(self, title: str, content: QWidget) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QLabel(title)
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)
        layout.addWidget(content)
        return frame

    def _setup_compact_form(self, form: QFormLayout) -> None:
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

    def _live_settings_form(self) -> QWidget:
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self._setup_compact_form(form)
        form.addRow("Sources (CSV)", self._sources_input)
        form.addRow("Capture FPS", self.window.spin_cap_fps)
        form.addRow("Capture Resolution", self._capture_resolution_combo)
        form.addRow("Preview FPS", self._preview_fps_spin)
        form.addRow("Preview Resolution", self._preview_resolution_combo)
        form.addRow("Calibration Detect Hz", self._detect_hz_spin)
        form.addRow("Probe Max Index", self._probe_max_spin)
        form.addRow("", self._probe_status)
        form.addRow("", self._apply_live_settings_button)
        return form_widget

    def _chessboard_settings_form(self) -> QWidget:
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self._setup_compact_form(form)
        form.addRow("Columns", self._chess_cols_spin)
        form.addRow("Rows", self._chess_rows_spin)
        form.addRow("Square", self.window.doubleSpinBox)
        form.addRow("", self._apply_chessboard_button)
        return form_widget

    def _charuco_settings_form(self) -> QWidget:
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self._setup_compact_form(form)
        form.addRow("ChArUco Squares X", self._charuco_x_spin)
        form.addRow("ChArUco Squares Y", self._charuco_y_spin)
        form.addRow("ChArUco Square", self._charuco_square_spin)
        form.addRow("ChArUco Marker", self._charuco_marker_spin)
        form.addRow("", self._apply_charuco_button)
        return form_widget

    def _workflow_settings_form(self) -> QWidget:
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self._setup_compact_form(form)
        # Workflow mode and auto-capture are driven entirely from the Camera tab
        # (the Intrinsics/Extrinsics Start buttons), so their controls are kept as
        # internal state only and intentionally not shown here.
        form.addRow("Pattern", self.window.combo_cap_pattern)
        form.addRow("Overlay", self._overlay_checkbox)
        form.addRow("Spiegelen", self._mirror_checkbox)
        form.addRow("Cooldown", self._auto_cooldown_spin)
        form.addRow("Max Samples (Intrinsics)", self._auto_max_intrinsics_combo)
        form.addRow("Max Samples (Extrinsics)", self._auto_max_extrinsics_spin)
        form.addRow("Min Quality (Intrinsics)", self._intrinsics_quality_spin)
        form.addRow("Min Coverage (Intrinsics)", self._intrinsics_coverage_spin)
        form.addRow("Min Quality (Extrinsics)", self._extrinsics_quality_spin)
        form.addRow("Min Coverage (Extrinsics)", self._extrinsics_coverage_spin)
        grid = QWidget()
        grid_layout = QHBoxLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.addWidget(self._grid_cols_spin)
        grid_layout.addWidget(QLabel("x"))
        grid_layout.addWidget(self._grid_rows_spin)
        grid_layout.addStretch(1)
        form.addRow("Spatial Grid", grid)
        form.addRow("", self._apply_workflow_button)
        return form_widget

    def _navigation_settings_form(self) -> QWidget:
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self._setup_compact_form(form)
        form.addRow("Auto-navigatie", self._auto_navigate_checkbox)
        return form_widget

    def _advanced_actions_widget(self) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        buttons = [
            self._probe_button,
            self._start_live_button,
            self._stop_live_button,
            self._capture_button,
            self._capture_sync_button,
            self._start_auto_button,
            self._save_profile_button,
            self._load_profile_button,
            self._reset_samples_button,
        ]
        for index, button in enumerate(buttons):
            button.setMinimumHeight(30)
            layout.addWidget(button, index // 2, index % 2)
        return widget

    def _status_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._feedback)
        layout.addWidget(self._auto_status)
        layout.addWidget(self._warnings)
        return widget

    # Page indices in stackedWidget, matching the navigation buttons.
    _PAGE_CAMERAS = 1
    _PAGE_RESULTS = 2

    def auto_navigation_enabled(self) -> bool:
        return self._auto_navigate_checkbox.isChecked()

    def maybe_auto_navigate(self, destination: str) -> None:
        """Switch tabs automatically when the advanced 'Auto-navigatie' toggle is on.

        ``destination`` is ``"cameras"`` (after opening a new project) or
        ``"results"`` (after a calibration is finished).
        """
        if not self.auto_navigation_enabled():
            return
        if destination == "cameras":
            self.switch_page(self._PAGE_CAMERAS)
        elif destination == "results":
            self.switch_page(self._PAGE_RESULTS)

    def switch_page(self, index: int) -> None:
        self.window.stackedWidget.setCurrentIndex(index)
        # Use the team stylesheet's nav styling (property-driven) instead of
        # hardcoded inline colours so hover/disabled states keep working and the
        # look stays consistent with guiStyle.
        for button_index, button in enumerate(self._nav_buttons):
            button.setProperty("active", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _handle_console_input(self) -> None:
        text = self.window.lineedit_console_input.text().strip()
        self.window.lineedit_console_input.clear()
        if not text:
            return
        self._log(f"> {text}", with_timestamp=False)
        command = text.lower()
        if command in {"help", "?", "commands"}:
            self._show_console_help()
        elif command == "home":
            self.switch_page(0)
        elif command in {"cameras", "camera", "kalibratie"}:
            self.switch_page(1)
        elif command == "results":
            self.switch_page(2)
        elif command == "directory":
            self.switch_page(3)
        elif command == "diagnostics":
            self.switch_page(4)
        elif command in {"settings", "advanced"}:
            self.switch_page(5)
        elif command == "start live":
            self._emit_start_live()
        elif command == "stop live":
            self.stop_live_requested.emit()
        elif command.startswith("capture intrinsics"):
            self._capture_intrinsics_sample()
        elif command.startswith("capture extrinsics"):
            self._capture_sync_sample()
        elif command == "solve intrinsics":
            self.solve_requested.emit()
        elif command == "solve extrinsics":
            self._emit_solve_extrinsics()
        else:
            self._log(f"Unknown command: {text}")

    def _log(self, text: str, with_timestamp: bool = True) -> None:
        prefix = datetime.now().strftime("[%H:%M:%S] ") if with_timestamp else ""
        self.window.plaintextedit_console.appendPlainText(f"{prefix}{text}")
        scrollbar = self.window.plaintextedit_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _show_console_help(self) -> None:
        self._log(
            "Commands: help | home | cameras | results | directory | diagnostics | settings | "
            "start live | stop live | capture intrinsics | capture extrinsics | solve intrinsics | solve extrinsics",
            with_timestamp=False,
        )

    def _open_documentation(self) -> None:
        webbrowser.open("https://github.com/MertenF06/HuCalib")
        self._log("Documentation opened in web browser.")

    def _warn_capture_restart_needed(self) -> None:
        """Capture FPS/resolution are applied to the camera only when live capture
        starts, so changing them mid-session has no effect until a restart. Tell the
        user when they change these while live is running (preview settings do apply
        immediately)."""
        if self._live_active:
            self.show_feedback(
                "Capture-FPS/-resolutie gewijzigd: stop en start live opnieuw om dit toe te "
                "passen. Preview-instellingen werken wel direct.",
                success=False,
            )

    def _apply_live_settings(self) -> None:
        self._sync_source_input_preview()
        self._emit_runtime_tuning_changed()
        if self._live_active:
            self._warn_capture_restart_needed()
        else:
            self.show_feedback("Live source settings applied.", success=True)

    def _apply_workflow_settings(self) -> None:
        self._apply_preview_options_to_tiles()
        self._emit_runtime_tuning_changed()
        # Apply the threshold values to the manager BEFORE the workflow-mode
        # change runs: the mode-change handler reloads the spinboxes from the
        # manager, so emitting it first would overwrite freshly typed values
        # (e.g. a threshold set to 0) with the manager's previous values.
        self._emit_acceptance_thresholds_changed()
        self._emit_workflow_mode_changed()
        self._emit_spatial_grid_changed()
        self.show_feedback("Workflow settings applied.", success=True)

    def _apply_preview_options_to_tiles(self) -> None:
        """Push the advanced overlay/mirror/auto-capture options onto every camera tile.

        A checkbox left in the mixed (partial) state means "leave each camera as
        it is", so only a deliberate checked/unchecked choice forces all cameras.
        """
        overlay_state = self._overlay_checkbox.checkState()
        mirror_state = self._mirror_checkbox.checkState()
        # Block panel signals while updating tiles: set_overlay_active/set_mirror_active
        # emit preview_options_changed per tile, which can re-enter set_sources and
        # mutate self._tiles mid-iteration. Iterate over a snapshot and refresh once.
        self.blockSignals(True)
        try:
            for tile in list(self._tiles.values()):
                if overlay_state != Qt.CheckState.PartiallyChecked:
                    tile.set_overlay_active(overlay_state == Qt.CheckState.Checked)
                if mirror_state != Qt.CheckState.PartiallyChecked:
                    tile.set_mirror_active(mirror_state == Qt.CheckState.Checked)
        finally:
            self.blockSignals(False)
        self.set_auto_capture_enabled(self._auto_capture_checkbox.isChecked())
        self.preview_options_changed.emit()

    def _sync_advanced_checkboxes_from_tiles(self) -> None:
        """Reflect the aggregate per-camera overlay/mirror state in the checkboxes."""
        if not getattr(self, "_overlay_checkbox", None) or not getattr(self, "_mirror_checkbox", None):
            return
        if not self._tiles:
            return
        self._set_aggregate_check_state(
            self._overlay_checkbox, [tile.overlay_enabled() for tile in self._tiles.values()]
        )
        self._set_aggregate_check_state(
            self._mirror_checkbox, [tile.mirror_enabled() for tile in self._tiles.values()]
        )

    def _set_aggregate_check_state(self, checkbox: QCheckBox, states: list[bool]) -> None:
        if not states:
            return
        if all(states):
            state = Qt.CheckState.Checked
        elif not any(states):
            state = Qt.CheckState.Unchecked
        else:
            state = Qt.CheckState.PartiallyChecked
        checkbox.blockSignals(True)
        checkbox.setCheckState(state)
        checkbox.blockSignals(False)

    def eventFilter(self, obj: object, event: object) -> bool:
        if (
            isinstance(obj, QWidget)
            and obj.property("wheel-scrolls-advanced-page")
            and isinstance(event, QtGui.QWheelEvent)
            and event.type() == QEvent.Type.Wheel
        ):
            scroll = self._advanced_scroll
            if scroll is not None:
                delta = event.pixelDelta().y()
                if delta == 0:
                    delta = event.angleDelta().y()
                if delta != 0:
                    bar = scroll.verticalScrollBar()
                    bar.setValue(bar.value() - delta)
            return True
        return super().eventFilter(obj, event)

    def _apply_board_settings(self, message: str) -> None:
        self.board_settings_applied.emit(self.board_settings())
        self.show_feedback(message, success=True)

    def _current_export_format(self) -> str:
        data = self._export_format_combo.currentData()
        return str(data if data is not None else "toml").lower().strip()

    def _request_export_preview(self) -> None:
        self.export_preview_requested.emit(self._current_export_format())

    def _request_export(self) -> None:
        self.export_requested.emit(self._current_export_format())

    def show_export_preview(self, text: str) -> None:
        self._tmol_preview.setPlainText(text)
        self.window.stackedWidget_2.setCurrentIndex(1)

    def _emit_start_live(self) -> None:
        try:
            sources = self.current_sources()
        except ValueError as exc:
            self.ui_message.emit(str(exc))
            return
        self.start_live_requested.emit(sources, self.target_fps())

    def _toggle_record(self, checked: bool) -> None:
        self.record_toggled.emit(checked)

    def set_recording_active(self, active: bool) -> None:
        button = self.window.btn_camera_record
        button.blockSignals(True)
        button.setChecked(active)
        button.blockSignals(False)
        button.setText("Stop opname" if active else "Opnemen")
        button.setStyleSheet(
            "background-color: #c62828; color: white; font-weight: bold;" if active else ""
        )

    def _update_record_button_enabled(self) -> None:
        button = getattr(self.window, "btn_camera_record", None)
        if button is None:
            return
        button.setEnabled(not self._video_sources)
        if self._video_sources:
            button.setToolTip("Opnemen is uitgeschakeld zolang video's als bron geladen zijn.")
        else:
            button.setToolTip("Neem de live beelden op en sla ze op als videobestand")
        self._refresh_add_camera_button()

    def _load_video_sources(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self.window,
            "Selecteer video('s) voor kalibratie",
            str(self._project_root),
            "Videobestanden (*.mp4 *.avi *.mov *.mkv *.m4v);;Alle bestanden (*)",
        )
        if not files:
            return
        files = files[:_MAX_CAMERAS]
        sources: list[CameraSourceConfig] = []
        for index, file_path in enumerate(files):
            source_id = f"cam{index}"
            sources.append(
                CameraSourceConfig(
                    source_id=source_id,
                    kind="video",
                    uri=file_path,
                    label=Path(file_path).name,
                )
            )
        self._video_sources = sources
        self.set_sources([source.source_id for source in sources])
        for source in sources:
            tile = self._tiles.get(source.source_id)
            if tile is not None:
                tile.set_display_name(source.label)
        self._apply_video_fps(files[0])
        self._update_record_button_enabled()
        self._emit_sources_changed()
        self.start_live_requested.emit(sources, self.target_fps())
        names = ", ".join(source.label for source in sources)
        self.show_feedback(
            f"{len(sources)} video('s) geladen: {names}. "
            "Druk op Start bij Intrinsics of Extrinsics om te berekenen.",
            success=True,
        )
        self.switch_page(1)

    def _apply_video_fps(self, video_path: str) -> None:
        try:
            capture = cv2.VideoCapture(video_path)
            fps = capture.get(cv2.CAP_PROP_FPS)
            capture.release()
        except Exception:  # noqa: BLE001 - best effort, fall back to current value
            return
        if fps and fps > 0:
            # Set programmatically (and live is restarted right after for the video),
            # so suppress the "restart needed" hint that a manual edit would trigger.
            self.window.spin_cap_fps.blockSignals(True)
            self.window.spin_cap_fps.setValue(max(1, min(120, int(round(fps)))))
            self.window.spin_cap_fps.blockSignals(False)

    # Styling for the active capture-mode button. Intrinsics and Extrinsics are
    # two mutually exclusive modes (see _enter_capture_mode).
    _MODE_ACTIVE_STYLE = "background-color: #0078d7; color: white; font-weight: bold;"
    # Distinct colour for the single Start/Stop calibration button while a run is
    # active, so it reads clearly different from its idle (accent blue) state.
    _CALIBRATION_RUN_ACTIVE_STYLE = "background-color: #A33434; color: white; font-weight: bold;"

    def _toggle_intrinsics_start(self, checked: bool) -> None:
        if checked:
            self._enter_capture_mode("intrinsics")
        else:
            self._exit_capture_mode("intrinsics")

    def _toggle_extrinsics_start(self, checked: bool) -> None:
        if checked:
            self._enter_capture_mode("sync_extrinsics")
        else:
            self._exit_capture_mode("sync_extrinsics")

    def enter_intrinsics_mode(self) -> None:
        """Public hook for the controller to start the intrinsics capture mode
        (used by the automatic calibration chain)."""
        self._enter_capture_mode("intrinsics")

    def enter_extrinsics_mode(self) -> None:
        """Public hook for the controller to auto-advance from intrinsics to the
        extrinsics capture mode once every camera has its intrinsic samples."""
        self._enter_capture_mode("sync_extrinsics")

    def _toggle_calibration_run(self, checked: bool) -> None:
        """Single Start/Stop calibration button (auto-navigation mode)."""
        if checked:
            self._start_calibration_button.setText("Stop kalibratie")
            self._start_calibration_button.setStyleSheet(self._CALIBRATION_RUN_ACTIVE_STYLE)
            self.start_calibration_requested.emit()
        else:
            self._start_calibration_button.setText("Start kalibratie")
            self._start_calibration_button.setStyleSheet("")
            self.stop_calibration_requested.emit()

    def set_calibration_run_active(self, active: bool) -> None:
        """Reflect the calibration-chain state on the button without re-emitting
        the start/stop signals (used when the chain finishes on its own)."""
        button = self._start_calibration_button
        button.blockSignals(True)
        button.setChecked(active)
        button.blockSignals(False)
        button.setText("Stop kalibratie" if active else "Start kalibratie")
        button.setStyleSheet(self._CALIBRATION_RUN_ACTIVE_STYLE if active else "")

    def _update_calibration_controls_visibility(self) -> None:
        """Show the single Start-kalibratie button when auto-navigation is on,
        otherwise show the per-phase Intrinsics/Extrinsics cards."""
        auto = self.auto_navigation_enabled()
        self._start_calibration_button.setVisible(auto)
        self.window.frame_2.setVisible(not auto)
        self.window.frame_3.setVisible(not auto)
        if not auto and self._start_calibration_button.isChecked():
            # Switching to manual mode cancels any running chain on the button.
            self.set_calibration_run_active(False)

    def open_all_detected_cameras(self) -> list[CameraSourceConfig]:
        """Open every detected webcam as a source tile and return the resulting
        source configs (empty list when nothing was detected)."""
        if not self._detected_cameras:
            return []
        indices = [camera.index for camera in self._detected_cameras][:_MAX_CAMERAS]
        self._video_sources = []
        self._sources_input.setText(",".join(str(index) for index in indices))
        self._sync_source_input_preview()
        try:
            return self.current_sources()
        except ValueError:
            return []

    def _enter_capture_mode(self, mode: str) -> None:
        """Arm a capture mode.

        Intrinsics and Extrinsics behave as two mutually exclusive modes: starting
        one stops the other. Intrinsics shows the per-camera coverage-grid overlay
        to guide the board across the frame; Extrinsics hides the grid (it only
        needs the board shared between cameras) and leans on the automatically
        relaxed sync acceptance thresholds the backend applies in the
        ``sync_extrinsics`` workflow.
        """
        is_intrinsics = mode == "intrinsics"
        start_button = (
            self.window.btn_cap_intrinsics_start
            if is_intrinsics
            else self.window.btn_cap_extrinsics_start
        )
        other_button = (
            self.window.btn_cap_extrinsics_start
            if is_intrinsics
            else self.window.btn_cap_intrinsics_start
        )

        # Modes are mutually exclusive: clear the other mode's button without
        # re-triggering its toggle handler (which would tear down the live capture
        # we re-arm just below for the new mode).
        if other_button.isChecked():
            other_button.blockSignals(True)
            other_button.setChecked(False)
            other_button.blockSignals(False)
        self._reset_mode_button(other_button)

        start_button.blockSignals(True)
        start_button.setChecked(True)
        start_button.blockSignals(False)
        start_button.setText("Stop")
        start_button.setStyleSheet(self._MODE_ACTIVE_STYLE)

        self.set_workflow_mode(mode)
        # Drives _on_calibration_workflow_mode_changed, which switches the active
        # acceptance thresholds (relaxed for sync/extrinsics) automatically.
        self.workflow_mode_changed.emit(mode)
        # Track how long this mode stays active for the diagnostics page.
        self._start_mode_timing(mode)
        self._set_all_tile_overlays(is_intrinsics)
        # Arm auto-capture: the frame-driven capture loop stores valid samples
        # automatically once live frames + detections flow.
        self.set_auto_capture_enabled(True)
        # Start live only if it isn't already running. Restarting would clear the
        # frame buffer, and auto-capture would then have nothing to work with for
        # the first moments. We deliberately do NOT emit auto_capture_start_requested
        # here: that handler warns and *disables* auto-capture when no frame has
        # arrived yet, which is exactly the case right after a fresh live start.
        if not self._live_active:
            self._emit_start_live()
        self.show_feedback(
            (
                "Intrinsics-modus actief — automatische capture aan; beweeg het bord "
                "door het beeld."
                if is_intrinsics
                else "Extrinsics-modus actief — automatische capture aan; houd het bord "
                "zichtbaar in meerdere camera's."
            ),
            success=True,
        )

    def _exit_capture_mode(self, mode: str) -> None:
        button = (
            self.window.btn_cap_intrinsics_start
            if mode == "intrinsics"
            else self.window.btn_cap_extrinsics_start
        )
        # Stopping a capture mode only disarms auto-capture. The live preview keeps
        # running (stop it with the dedicated "Live stoppen" button) and any active
        # recording keeps going (stop it with the record button), so calibration
        # mode switches never interrupt an ongoing recording.
        self.set_auto_capture_enabled(False)
        self._reset_mode_button(button)
        self._stop_mode_timing()

    def _reset_mode_button(self, button: QPushButton) -> None:
        button.setText("Start")
        button.setStyleSheet("")

    def _set_all_tile_overlays(self, active: bool) -> None:
        """Show (intrinsics) or hide (extrinsics) the detection/coverage-grid
        overlay on every camera tile.

        ``set_overlay_active`` emits ``preview_options_changed`` per tile, which can
        re-enter ``set_sources`` and mutate ``self._tiles`` mid-iteration, so iterate
        over a snapshot with the panel's signals blocked and refresh once at the end.
        """
        self.blockSignals(True)
        try:
            for tile in list(self._tiles.values()):
                tile.set_overlay_active(active)
        finally:
            self.blockSignals(False)
        self._sync_advanced_checkboxes_from_tiles()
        self.preview_options_changed.emit()

    def _emit_solve_extrinsics(self) -> None:
        self.set_workflow_mode("sync_extrinsics")
        self.workflow_mode_changed.emit("sync_extrinsics")
        self.solve_extrinsics_requested.emit()

    def _emit_reset(self) -> None:
        self.window.btn_cap_intrinsics_start.setChecked(False)
        self.window.btn_cap_extrinsics_start.setChecked(False)
        self.window.btn_cap_intrinsics_start.setText("Start")
        self.window.btn_cap_extrinsics_start.setText("Start")
        self.window.btn_cap_intrinsics_start.setStyleSheet("")
        self.window.btn_cap_extrinsics_start.setStyleSheet("")
        for tile in self._tiles.values():
            tile.set_sample_count(0)
        self._reset_mode_timing()
        self.reset_requested.emit()

    def _capture_intrinsics_sample(self) -> None:
        self.set_workflow_mode("intrinsics")
        self.workflow_mode_changed.emit("intrinsics")
        self.capture_requested.emit()

    def _capture_sync_sample(self) -> None:
        self.set_workflow_mode("sync_extrinsics")
        self.workflow_mode_changed.emit("sync_extrinsics")
        self.capture_requested.emit()

    def _emit_pattern_changed(self) -> None:
        self.pattern_changed.emit(self.current_pattern())

    def _emit_workflow_mode_changed(self) -> None:
        self.workflow_mode_changed.emit(self.current_workflow_mode())

    def _emit_acceptance_thresholds_changed(self) -> None:
        intr_q, intr_cov, extr_q, extr_cov = self.acceptance_threshold_values()
        self.acceptance_thresholds_changed.emit(intr_q, intr_cov, extr_q, extr_cov)

    def _emit_spatial_grid_changed(self) -> None:
        cols, rows = self.spatial_grid_values()
        self.spatial_grid_changed.emit(cols, rows)

    def _emit_runtime_tuning_changed(self) -> None:
        self.runtime_tuning_changed.emit(self.runtime_tuning())
        self.window.text_diag_current_fps.setPlainText(str(self.window.spin_cap_fps.value()))

    def _sync_source_input_preview(self) -> None:
        self._video_sources = []
        self._update_record_button_enabled()
        self._source_csv = self._sources_input.text().strip()
        self.set_sources(self._source_ids_for_csv(self._source_csv))
        self._emit_sources_changed()
        self._refresh_add_camera_button()

    def _append_camera_source(self) -> None:
        if self._video_sources:
            QMessageBox.information(
                self.window,
                "Camera toevoegen",
                "Verwijder eerst de geladen video's voordat je webcams toevoegt.",
            )
            return
        tokens = [token.strip() for token in self._sources_input.text().split(",") if token.strip()]
        numeric_tokens = {int(token) for token in tokens if token.isdigit()}
        if len(tokens) >= _MAX_CAMERAS:
            QMessageBox.information(
                self.window,
                "Camera toevoegen",
                f"Je kunt maximaal {_MAX_CAMERAS} camera's tegelijk gebruiken.",
            )
            return
        next_index = self._next_detected_camera_index(numeric_tokens)
        if next_index is None:
            # No camera available: give clear popup feedback instead of silently
            # doing nothing.
            if self._camera_probe_running:
                QMessageBox.information(
                    self.window,
                    "Camera toevoegen",
                    "De camera scan loopt nog. Wacht even tot de scan klaar is.",
                )
            else:
                QMessageBox.information(
                    self.window,
                    "Geen camera beschikbaar",
                    "Er is geen extra camera gevonden.\n\n"
                    "Sluit nog een camera aan en klik op 'Camera's zoeken'.",
                )
            self._refresh_add_camera_button()
            return
        tokens.append(str(next_index))
        self._sources_input.setText(",".join(tokens))
        self._sync_source_input_preview()

    def _remove_source(self, source_id: str) -> None:
        if self._video_sources:
            self._video_sources = [
                source for source in self._video_sources if source.source_id != source_id
            ]
            tile = self._tiles.get(source_id)
            if tile is not None:
                tile.close_popout()
            self.set_sources([source.source_id for source in self._video_sources])
            self._update_record_button_enabled()
            self._emit_sources_changed()
            return
        try:
            index = self._source_order.index(source_id)
        except ValueError:
            return
        tile = self._tiles.get(source_id)
        if tile is not None:
            tile.close_popout()
        tokens = [token.strip() for token in self._sources_input.text().split(",") if token.strip()]
        if 0 <= index < len(tokens):
            del tokens[index]
        self._sources_input.setText(",".join(tokens))
        self._sync_source_input_preview()
        self._refresh_add_camera_button()

    def _source_ids_for_csv(self, csv: str) -> list[str]:
        tokens = [token.strip() for token in csv.split(",") if token.strip()]
        return [self._source_id_for_token(token, index) for index, token in enumerate(tokens[:_MAX_CAMERAS])]

    def _source_id_for_token(self, token: str, index: int) -> str:
        return f"cam{int(token)}" if token.isdigit() else f"cam{index}"

    def _emit_sources_changed(self) -> None:
        try:
            sources = self.current_sources()
        except ValueError:
            sources = []
        self.sources_changed.emit(sources)

    def current_sources(self) -> list[CameraSourceConfig]:
        if self._video_sources:
            return list(self._video_sources)
        raw = self._sources_input.text().strip()
        if not raw:
            raise ValueError("Camera CSV is empty. Provide at least one source.")
        tokens = [token.strip() for token in raw.split(",") if token.strip()]
        if not tokens:
            raise ValueError("No valid camera sources parsed.")
        if len(tokens) > 4:
            raise ValueError("Use up to 4 sources for calibration.")
        sources: list[CameraSourceConfig] = []
        for index, token in enumerate(tokens):
            source_id = self._source_id_for_token(token, index)
            label = self._camera_names.get(source_id)
            if token.isdigit():
                sources.append(
                    CameraSourceConfig(
                        source_id=source_id,
                        kind="webcam",
                        uri=int(token),
                        label=label or f"Webcam {token}",
                    )
                )
            else:
                sources.append(
                    CameraSourceConfig(
                        source_id=source_id,
                        kind="video",
                        uri=token,
                        label=label or token,
                    )
                )
        return sources

    def target_fps(self) -> float:
        return float(self.window.spin_cap_fps.value())

    def runtime_tuning(self) -> RuntimeTuning:
        capture_size = self._capture_resolution_combo.currentData()
        if not isinstance(capture_size, tuple) or len(capture_size) != 2:
            capture_size = (0, 0)
        preview_size = self._preview_resolution_combo.currentData()
        if not isinstance(preview_size, tuple) or len(preview_size) != 2:
            preview_size = (0, 0)
        return RuntimeTuning(
            capture_fps=float(self.window.spin_cap_fps.value()),
            capture_width=int(capture_size[0]),
            capture_height=int(capture_size[1]),
            preview_fps=float(self._preview_fps_spin.value()),
            preview_max_width=int(preview_size[0]),
            preview_max_height=int(preview_size[1]),
            calibration_detection_hz=float(self._detect_hz_spin.value()),
            overlays_enabled=self._overlay_checkbox.isChecked(),
            detection_capture_enabled=False,
            detection_reconstruction_enabled=False,
            detection_analysis_enabled=False,
        )

    def set_sources(self, source_ids: list[str]) -> None:
        source_ids = source_ids[:_MAX_CAMERAS]
        if source_ids == self._source_order and set(self._tiles) == set(source_ids):
            # Nothing changed: keep the existing grid so live tiles don't flicker
            # or jump cells when this is called on every refresh.
            return
        existing = set(self._tiles)
        requested = set(source_ids)

        for source_id in sorted(existing - requested):
            tile = self._tiles.pop(source_id)
            tile.close_popout()
            self._camera_grid.removeWidget(tile)
            tile.deleteLater()

        for source_id in source_ids:
            if source_id in self._tiles:
                continue
            tile = DesignedPreviewTile(source_id)
            tile.set_display_name(self._camera_names.get(source_id, source_id))
            tile.undistort_toggled.connect(self.undistort_toggled)
            tile.preview_options_changed.connect(self.preview_options_changed)
            tile.remove_requested.connect(self._remove_source)
            tile.name_changed.connect(self._on_camera_name_changed)
            self._tiles[source_id] = tile

        self._source_order = list(source_ids)
        self._rebuild_camera_grid()
        self._sync_advanced_checkboxes_from_tiles()
        self._refresh_add_camera_button()

    def _rebuild_camera_grid(self) -> None:
        while self._camera_grid.count():
            item = self._camera_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        # Clear any stretch left over from a previous layout.
        for col in range(self._camera_grid.columnCount()):
            self._camera_grid.setColumnStretch(col, 0)
        for row in range(self._camera_grid.rowCount()):
            self._camera_grid.setRowStretch(row, 0)

        # The cards are fixed-size; pack them from the top-left instead of
        # stretching/centering one large field in the middle.
        top_left = Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        self._camera_grid.setAlignment(top_left)

        count = len(self._source_order)
        show_add = count < _MAX_CAMERAS
        total_cells = max(1, count + (1 if show_add else 0))

        # Roughly square grid; cap the column count so it doesn't get too wide.
        columns = 1
        while columns * columns < total_cells:
            columns += 1
        columns = min(columns, 4)

        for index, source_id in enumerate(self._source_order):
            self._camera_grid.addWidget(
                self._tiles[source_id], index // columns, index % columns, alignment=top_left
            )

        # The add button sits in the next free cell, compact.
        if show_add:
            self._add_camera_button.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            self._add_camera_button.setMinimumHeight(36)
            self._add_camera_button.setMinimumWidth(150)
            self._camera_grid.addWidget(
                self._add_camera_button, count // columns, count % columns, alignment=top_left
            )

        self._refresh_add_camera_button()

    def _current_source_tokens(self) -> list[str]:
        if not hasattr(self, "_sources_input"):
            return []
        return [token.strip() for token in self._sources_input.text().split(",") if token.strip()]

    def _detected_camera_indices(self) -> list[int]:
        return [camera.index for camera in self._detected_cameras]

    def _next_detected_camera_index(self, used_indices: set[int] | None = None) -> int | None:
        used = set(used_indices or set())
        for index in self._detected_camera_indices():
            if index not in used:
                return index
        return None

    def _sync_sources_to_detected_cameras(self) -> None:
        if not self._detected_cameras or self._video_sources:
            return
        detected = self._detected_camera_indices()
        tokens = self._current_source_tokens()
        next_tokens: list[str] = []
        used: set[int] = set()
        non_numeric = [token for token in tokens if not token.isdigit()]

        for token in tokens:
            if not token.isdigit():
                continue
            index = int(token)
            if index in detected and index not in used:
                next_tokens.append(str(index))
                used.add(index)

        if not next_tokens and not non_numeric and detected:
            next_tokens.append(str(detected[0]))

        next_tokens.extend(non_numeric)
        next_tokens = next_tokens[:_MAX_CAMERAS]
        if next_tokens != tokens:
            self._sources_input.setText(",".join(next_tokens))
            self._sync_source_input_preview()
        else:
            self._refresh_add_camera_button()

    def _refresh_add_camera_button(self) -> None:
        if not hasattr(self, "_add_camera_button") or not hasattr(self, "_sources_input"):
            return
        tokens = self._current_source_tokens()
        at_max = len(tokens) >= _MAX_CAMERAS
        # The button is a clear "add". It stays clickable even when nothing is
        # available so a click can give popup feedback (handled in
        # _append_camera_source). Only scanning, loaded videos or the max block it.
        clickable = not self._camera_probe_running and not self._video_sources and not at_max
        self._add_camera_button.setEnabled(clickable)
        if self._camera_probe_running:
            self._add_camera_button.setText("Scannen...")
            self._add_camera_button.setToolTip("Wacht tot de camera scan klaar is.")
        elif self._video_sources:
            self._add_camera_button.setText("+ Camera toevoegen")
            self._add_camera_button.setToolTip("Verwijder eerst geladen video's om webcams toe te voegen.")
        elif at_max:
            self._add_camera_button.setText(f"Maximaal {_MAX_CAMERAS} camera's")
            self._add_camera_button.setToolTip(f"Je kunt maximaal {_MAX_CAMERAS} camera's tegelijk gebruiken.")
        else:
            self._add_camera_button.setText("+ Camera toevoegen")
            self._add_camera_button.setToolTip("Voeg de volgende gevonden camera toe.")

    def _tile_status(self, count: int, detection: ChessboardDetectionResult | None) -> str:
        label = "Extrinsics" if self.current_workflow_mode() == "sync_extrinsics" else "Intrinsics"
        status = f"{label}={count}"
        if detection is not None and detection.found:
            status += (
                f" | {detection.pattern_type}"
                f" | corners={detection.detected_corners}"
                f" | q={detection.quality_score:.2f}"
                f" | cov={detection.coverage_ratio * 100:.1f}%"
            )
        elif detection is not None:
            status += f" | {detection.pattern_type} not found"
        return status

    def update_previews(
        self,
        preview_frames: dict[str, Any],
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
        overlay_states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        overlay_states = overlay_states or {}
        target = self.auto_capture_max_samples()
        for source_id, frame_bgr in preview_frames.items():
            tile = self._tiles.get(source_id)
            if tile is None:
                continue
            detection = detections.get(source_id)
            count = int(sample_counts.get(source_id, 0))
            tile.set_sample_target(target)
            tile.set_frame(
                frame_bgr,
                self._tile_status(count, detection),
                count,
                detection=detection,
                overlay_state=overlay_states.get(source_id),
            )

    def update_preview_images(
        self,
        images: dict[str, QImage],
        detections: dict[str, ChessboardDetectionResult],
        sample_counts: dict[str, int],
        overlay_states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Display frames already prepared (RGB QImage) by the render worker."""
        overlay_states = overlay_states or {}
        target = self.auto_capture_max_samples()
        for source_id, image in images.items():
            tile = self._tiles.get(source_id)
            if tile is None:
                continue
            detection = detections.get(source_id)
            count = int(sample_counts.get(source_id, 0))
            tile.set_sample_target(target)
            tile.set_frame_image(
                image,
                self._tile_status(count, detection),
                count,
                detection=detection,
                overlay_state=overlay_states.get(source_id),
            )

    def _display_name(self, source_id: str) -> str:
        tile = self._tiles.get(source_id)
        if tile is not None:
            return tile.display_name()
        return self._camera_names.get(source_id, source_id)

    def _on_camera_name_changed(self, source_id: str, name: str) -> None:
        clean_name = name.strip() or source_id
        self._camera_names[source_id] = clean_name
        if hasattr(self.window._config, "camera_labels"):
            self.window._config.camera_labels = dict(self._camera_names)
            try:
                self.window._config.save()
            except Exception:  # noqa: BLE001
                pass
        self._log(f"Camera renamed: {source_id} -> {clean_name}")


    def update_camera_status_table(
        self,
        source_ids: list[str],
        sample_counts: dict[str, int],
        sample_breakdown: dict[str, dict[str, int]],
        bundle: CalibrationBundle | None,
        live_detection: dict[str, ChessboardDetectionResult] | None = None,
    ) -> None:
        intrinsics: list[str] = []
        extrinsics: list[str] = []
        frames: list[str] = []
        camera_info: list[str] = []
        errors: list[str] = []

        for source_id in source_ids:
            display_name = self._display_name(source_id)
            camera = bundle.cameras.get(source_id) if bundle else None
            breakdown = sample_breakdown.get(source_id, {})
            count = int(sample_counts.get(source_id, 0))
            total = int(breakdown.get("total", count))
            status = self._camera_status_text(camera)
            reproj = f"{camera.reprojection_error:.4f}px" if camera and camera.reprojection_error is not None else "-"
            intrinsics.append(f"{display_name} ({source_id}): {status}, samples {count}/{total}, reprojection {reproj}")
            if camera and camera.rotation is not None and camera.translation is not None:
                extrinsics.append(f"{display_name} ({source_id}): solved")
            else:
                extrinsics.append(f"{display_name} ({source_id}): unsolved")
            frames.append(f"{display_name} ({source_id}): intrinsics={count}, sync={int(breakdown.get('synchronized', 0))}")
            image_size = f"{camera.image_size[0]}x{camera.image_size[1]}" if camera and camera.image_size else "-"
            camera_info.append(f"{display_name} ({source_id}): image={image_size}")
            diagnostics = []
            if camera:
                diagnostics.extend(camera.diagnostics)
            if live_detection and source_id in live_detection:
                diagnostics.extend(live_detection[source_id].diagnostics)
            if diagnostics:
                errors.append(f"{display_name} ({source_id}): " + "; ".join(dict.fromkeys(diagnostics)))

        if bundle and bundle.notes:
            errors.extend(bundle.notes[-8:])

        self._intrinsics_text.setPlainText("\n".join(intrinsics) or "-")
        self._extrinsics_text.setPlainText("\n".join(extrinsics) or "-")
        self._frames_text.setPlainText("\n".join(frames) or "-")
        self._camera_info_text.setPlainText("\n".join(camera_info) or "-")
        self._error_text.setPlainText("\n".join(dict.fromkeys(errors)) or "-")

    def _camera_status_text(self, camera: CameraCalibration | None) -> str:
        return camera.status if camera else "unsolved"

    def show_feedback(self, message: str, success: bool) -> None:
        color = "#0f7b0f" if success else "#9a6700"
        self._feedback.setStyleSheet(f"color: {color};")
        self._feedback.setText(message)
        self._log(message)

    def show_warnings(self, lines: list[str]) -> None:
        self._warnings.setPlainText("\n".join(lines))

    def set_live_status(self, live_active: bool, active_cameras: int) -> None:
        self._live_active = live_active
        self._active_cameras = active_cameras
        self.window.btn_camera_start_live.setEnabled(not live_active)
        self.window.btn_camera_stop_live.setEnabled(live_active)
        self.window.text_diag_used_cams.setPlainText(str(active_cameras))
        state = "On" if live_active else "Off"
        self._feedback.setText(f"Live: {state} | Cameras: {active_cameras}")
        if not live_active:
            # Stopping live disarms both capture modes, so freeze the mode timer.
            self._stop_mode_timing()
            for button in [self.window.btn_cap_intrinsics_start, self.window.btn_cap_extrinsics_start]:
                button.blockSignals(True)
                button.setChecked(False)
                button.setText("Start")
                button.setStyleSheet("")
                button.blockSignals(False)

    def set_camera_probe_running(self, running: bool) -> None:
        self._camera_probe_running = running
        self._probe_button.setEnabled(not running)
        self.window.btn_camera_detect.setEnabled(not running)
        self._probe_max_spin.setEnabled(not running)
        self._probe_button.setText("Scannen..." if running else "Camera's zoeken")
        self.window.btn_camera_detect.setText("Scannen..." if running else "Camera's zoeken")
        self._probe_status.setText("Camera scan: scanning..." if running else self._probe_status.text())
        self._refresh_add_camera_button()

    def set_detected_cameras(self, cameras: list[CameraProbeResult]) -> None:
        self._detected_cameras = sorted(cameras, key=lambda camera: camera.index)
        if not cameras:
            self._probe_status.setText("Camera scan: no cameras found.")
            self._log("Camera scan: no cameras found.")
            self._refresh_add_camera_button()
            return
        parts = []
        for camera in self._detected_cameras:
            resolution = f"{camera.width}x{camera.height}" if camera.width > 0 and camera.height > 0 else "unknown res"
            backend = f" ({camera.backend})" if camera.backend else ""
            parts.append(f"{camera.index}: {resolution}{backend}")
        text = "Camera scan: " + " | ".join(parts)
        self._probe_status.setText(text)
        self._log(text)
        self._sync_sources_to_detected_cameras()

    def probe_max_index(self) -> int:
        return int(self._probe_max_spin.value())

    def set_intrinsics_solve_running(self, running: bool, message: str = "Solving intrinsics...") -> None:
        for button in [
            self.window.btn_cap_calculate_intrinsics,
            self.window.btn_cap_calculate_extrinsics,
            self._capture_button,
            self._capture_sync_button,
            self._reset_samples_button,
            self._load_profile_button,
            self._apply_live_settings_button,
            self._apply_chessboard_button,
            self._apply_charuco_button,
            self._apply_workflow_button,
        ]:
            button.setEnabled(not running)
        if running:
            self._feedback.setText(message)

    def current_pattern(self) -> str:
        data = self.window.combo_cap_pattern.currentData()
        return str(data if data is not None else "chessboard").lower().strip()

    def set_pattern_options(self, pattern_names: list[str], selected: str) -> None:
        self.window.combo_cap_pattern.blockSignals(True)
        self.window.combo_cap_pattern.clear()
        for name in pattern_names:
            key = name.lower().strip()
            self.window.combo_cap_pattern.addItem("Charuco" if key == "charuco" else "Chessboard", key)
        if self.window.combo_cap_pattern.count() == 0:
            self.window.combo_cap_pattern.addItem("Chessboard", "chessboard")
        index = self.window.combo_cap_pattern.findData(selected.lower().strip())
        self.window.combo_cap_pattern.setCurrentIndex(index if index >= 0 else 0)
        self.window.combo_cap_pattern.blockSignals(False)
        self._emit_pattern_changed()

    def board_settings(self) -> CalibrationBoardSettings:
        return CalibrationBoardSettings(
            chessboard_cols=int(self._chess_cols_spin.value()),
            chessboard_rows=int(self._chess_rows_spin.value()),
            chessboard_square_size_m=float(self.window.doubleSpinBox.value()) / 1000.0,
            charuco_squares_x=int(self._charuco_x_spin.value()),
            charuco_squares_y=int(self._charuco_y_spin.value()),
            charuco_square_size_m=float(self._charuco_square_spin.value()) / 1000.0,
            charuco_marker_size_m=float(self._charuco_marker_spin.value()) / 1000.0,
        )

    def set_board_settings(self, settings: CalibrationBoardSettings) -> None:
        self._chess_cols_spin.setValue(int(settings.chessboard_cols))
        self._chess_rows_spin.setValue(int(settings.chessboard_rows))
        self.window.doubleSpinBox.setValue(float(settings.chessboard_square_size_m) * 1000.0)
        self._charuco_x_spin.setValue(int(settings.charuco_squares_x))
        self._charuco_y_spin.setValue(int(settings.charuco_squares_y))
        self._charuco_square_spin.setValue(float(settings.charuco_square_size_m) * 1000.0)
        self._charuco_marker_spin.setValue(float(settings.charuco_marker_size_m) * 1000.0)

    def current_workflow_mode(self) -> Literal["intrinsics", "sync_extrinsics"]:
        data = self._workflow_combo.currentData()
        mode = str(data if data is not None else "intrinsics").lower().strip()
        return "sync_extrinsics" if mode == "sync_extrinsics" else "intrinsics"

    def set_workflow_mode(self, mode: str) -> None:
        index = self._workflow_combo.findData(mode.lower().strip())
        self._workflow_combo.blockSignals(True)
        self._workflow_combo.setCurrentIndex(index if index >= 0 else 0)
        self._workflow_combo.blockSignals(False)
        if mode == "sync_extrinsics":
            self._capture_button.setText("Capture Intrinsics Sample(s)")
            self._capture_sync_button.setEnabled(True)
        else:
            self._capture_sync_button.setEnabled(True)

    def auto_capture_enabled(self) -> bool:
        return self._auto_capture_checkbox.isChecked()

    def set_auto_capture_enabled(self, enabled: bool) -> None:
        # Auto capture is no longer a per-camera button; it is driven by the
        # active intrinsics/extrinsics mode (see _enter_capture_mode) through this
        # single checkbox, which the backend reads via auto_capture_enabled().
        self._auto_capture_checkbox.setChecked(enabled)

    def auto_capture_cooldown_sec(self) -> float:
        return float(self._auto_cooldown_spin.value())

    # Offer 1..12 samples per spatial-grid cell as the intrinsics budget; the
    # total (cells x per-cell) is therefore always evenly divisible over the grid.
    _INTRINSICS_PER_CELL_CHOICES = tuple(range(1, 13))

    def _rebuild_intrinsics_max_options(self) -> None:
        """Repopulate the intrinsics budget dropdown with totals that divide
        evenly over the current spatial grid, preserving the chosen per-cell
        target across grid changes."""
        combo = self._auto_max_intrinsics_combo
        cols, rows = self.spatial_grid_values()
        cells = max(1, int(cols) * int(rows))
        target = max(1, int(getattr(self, "_intrinsics_per_cell_target", 3)))
        combo.blockSignals(True)
        combo.clear()
        for per_cell in self._INTRINSICS_PER_CELL_CHOICES:
            total = cells * per_cell
            combo.addItem(f"{per_cell}/vak ({total})", total)
        combo.addItem("Geen limiet", 0)
        # Select the option matching the preserved per-cell target.
        index = min(target, len(self._INTRINSICS_PER_CELL_CHOICES)) - 1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _on_intrinsics_max_changed(self, _index: int) -> None:
        total = int(self._auto_max_intrinsics_combo.currentData() or 0)
        if total > 0:
            cols, rows = self.spatial_grid_values()
            cells = max(1, int(cols) * int(rows))
            self._intrinsics_per_cell_target = max(1, total // cells)

    def intrinsics_max_samples(self) -> int:
        return int(self._auto_max_intrinsics_combo.currentData() or 0)

    def extrinsics_max_samples(self) -> int:
        return int(self._auto_max_extrinsics_spin.value())

    def auto_capture_max_samples(self) -> int:
        # Mode-specific budget: intrinsics and extrinsics keep separate sample
        # limits so capturing one no longer eats into the other's progress.
        if self.current_workflow_mode() == "sync_extrinsics":
            return self.extrinsics_max_samples()
        return self.intrinsics_max_samples()

    def set_auto_capture_status(self, message: str) -> None:
        self._auto_status.setText(message)

    def overlay_enabled(self) -> bool:
        if not self._tiles:
            return self._overlay_checkbox.isChecked()
        return any(tile.overlay_enabled() for tile in self._tiles.values())

    def overlay_enabled_for(self, source_id: str) -> bool:
        tile = self._tiles.get(source_id)
        return tile.overlay_enabled() if tile else self._overlay_checkbox.isChecked()

    def mirror_preview_enabled(self) -> bool:
        return self._mirror_checkbox.isChecked()

    def mirror_preview_enabled_for(self, source_id: str) -> bool:
        tile = self._tiles.get(source_id)
        return self._mirror_checkbox.isChecked() or (tile.mirror_enabled() if tile else False)

    def undistort_enabled_for(self, source_id: str) -> bool:
        tile = self._tiles.get(source_id)
        return tile.undistort_enabled() if tile else False

    def spatial_grid_values(self) -> tuple[int, int]:
        return int(self._grid_cols_spin.value()), int(self._grid_rows_spin.value())

    def set_spatial_grid_values(self, cols: int, rows: int) -> None:
        self._grid_cols_spin.blockSignals(True)
        self._grid_rows_spin.blockSignals(True)
        self._grid_cols_spin.setValue(max(1, int(cols)))
        self._grid_rows_spin.setValue(max(1, int(rows)))
        self._grid_cols_spin.blockSignals(False)
        self._grid_rows_spin.blockSignals(False)
        # Signals were blocked above, so refresh the intrinsics budget options to
        # match the new grid explicitly.
        self._rebuild_intrinsics_max_options()

    def set_acceptance_threshold_values(
        self,
        intrinsics_quality: float,
        intrinsics_coverage_ratio: float,
        extrinsics_quality: float,
        extrinsics_coverage_ratio: float,
    ) -> None:
        for spin, value in (
            (self._intrinsics_quality_spin, float(intrinsics_quality)),
            (self._intrinsics_coverage_spin, float(intrinsics_coverage_ratio) * 100.0),
            (self._extrinsics_quality_spin, float(extrinsics_quality)),
            (self._extrinsics_coverage_spin, float(extrinsics_coverage_ratio) * 100.0),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def acceptance_threshold_values(self) -> tuple[float, float, float, float]:
        return (
            float(self._intrinsics_quality_spin.value()),
            float(self._intrinsics_coverage_spin.value()) / 100.0,
            float(self._extrinsics_quality_spin.value()),
            float(self._extrinsics_coverage_spin.value()) / 100.0,
        )

    def load_root_directory(self, directory_path: Path | str) -> None:
        path = Path(directory_path)
        if not path.exists() or not path.is_dir():
            self._log(f"Directory not found: {path}")
            return
        self._project_root = path
        self._directory_path.setText(str(path))
        self._directory_tree.clear()
        root = QTreeWidgetItem(self._directory_tree)
        root.setText(0, path.name or str(path))
        root.setText(1, "Map")
        root.setData(0, Qt.ItemDataRole.UserRole, str(path))
        root.setIcon(0, self._icon_provider.icon(QFileIconProvider.IconType.Folder))
        self._populate_directory_item(root, path, 1)
        root.setExpanded(True)

    def _populate_directory_item(self, parent: QTreeWidgetItem, path: Path, depth: int) -> None:
        while parent.childCount() > 0:
            parent.removeChild(parent.child(0))
        try:
            items = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            return
        for item_path in items:
            if item_path.name.startswith("."):
                continue
            item = QTreeWidgetItem(parent)
            item.setText(0, item_path.name)
            item.setData(0, Qt.ItemDataRole.UserRole, str(item_path))
            if item_path.is_dir():
                item.setText(1, "Map")
                item.setIcon(0, self._icon_provider.icon(QFileIconProvider.IconType.Folder))
                if depth < 10:
                    dummy = QTreeWidgetItem(item)
                    dummy.setText(0, "Loading...")
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
            else:
                item.setText(1, "Bestand")
                item.setIcon(0, self._icon_provider.icon(QtCore.QFileInfo(str(item_path))))
            try:
                modified = datetime.fromtimestamp(item_path.stat().st_mtime).strftime("%d-%m-%Y %H:%M")
            except OSError:
                modified = "-"
            item.setText(2, modified)

    def _on_directory_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole + 1) is False:
            path = Path(str(item.data(0, Qt.ItemDataRole.UserRole)))
            self._populate_directory_item(item, path, self._directory_depth(item) + 1)

    def _directory_depth(self, item: QTreeWidgetItem) -> int:
        depth = 0
        current = item
        while current.parent() is not None:
            depth += 1
            current = current.parent()
        return depth

    def _go_up_directory(self) -> None:
        parent = self._project_root.parent
        if parent != self._project_root:
            self.load_root_directory(parent)

    def _go_down_directory(self, item: QTreeWidgetItem | None = None) -> None:
        target_item = item or self._directory_tree.currentItem()
        if target_item is None:
            self._log("Select a folder first.")
            return
        path_data = target_item.data(0, Qt.ItemDataRole.UserRole)
        if path_data is None:
            return
        path = Path(str(path_data))
        if path.is_dir():
            self.load_root_directory(path)
            return
        self._log(f"Not a folder: {path.name}")

    def _browse_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self.window, "Selecteer een project map", str(self._project_root))
        if not selected:
            return
        self.load_root_directory(Path(selected))
        self.switch_page(3)
        self._log(f"Project folder loaded: {selected}")


class DesignedMainWindow(FunctionalMainWindow, Ui_MainWindow):
    def _create_calibration_panel(self, default_camera_csv: str, default_fps: float):
        if not hasattr(QtCore.Qt, "QFrame"):
            QtCore.Qt.QFrame = QtWidgets.QFrame
        if not hasattr(QtWidgets, "QAction"):
            QtWidgets.QAction = QtGui.QAction
        self.setupUi(self)
        self._compact_camera_controls()
        self._setup_resizable_shell()
        self._setup_settings_menu()
        return DesignedCalibrationPanel(
            window=self,
            default_camera_csv=default_camera_csv,
            default_fps=default_fps,
        )

    def _compact_camera_controls(self) -> None:
        self.btn_camera_detect = QPushButton("Camera's zoeken", self.frame)
        self.btn_camera_detect.setObjectName("btn_camera_detect")
        self.btn_camera_start_live = QPushButton("Live starten", self.frame)
        self.btn_camera_start_live.setObjectName("btn_camera_start_live")
        self.btn_camera_start_live.setProperty("accent", True)
        self.btn_camera_stop_live = QPushButton("Live stoppen", self.frame)
        self.btn_camera_stop_live.setObjectName("btn_camera_stop_live")
        self.btn_camera_stop_live.setEnabled(False)

        self.btn_camera_record = QPushButton("Opnemen", self.frame)
        self.btn_camera_record.setObjectName("btn_camera_record")
        self.btn_camera_record.setCheckable(True)
        self.btn_camera_record.setToolTip("Neem de live beelden op en sla ze op als videobestand")
        self.btn_camera_load_video = QPushButton("Video laden", self.frame)
        self.btn_camera_load_video.setObjectName("btn_camera_load_video")
        self.btn_camera_load_video.setToolTip(
            "Laad een videobestand om de intrinsics/extrinsics daaruit te berekenen"
        )

        live_actions = QWidget(self.frame)
        live_actions_layout = QHBoxLayout(live_actions)
        live_actions_layout.setContentsMargins(0, 0, 0, 0)
        live_actions_layout.setSpacing(6)
        live_actions_layout.addWidget(self.btn_camera_detect)
        live_actions_layout.addWidget(self.btn_camera_start_live)
        live_actions_layout.addWidget(self.btn_camera_stop_live)

        video_actions = QWidget(self.frame)
        video_actions_layout = QHBoxLayout(video_actions)
        video_actions_layout.setContentsMargins(0, 0, 0, 0)
        video_actions_layout.setSpacing(6)
        video_actions_layout.addWidget(self.btn_camera_record)
        video_actions_layout.addWidget(self.btn_camera_load_video)

        top_layout = self.frame.layout()
        if isinstance(top_layout, QGridLayout):
            top_layout.setContentsMargins(8, 6, 8, 6)
            top_layout.setHorizontalSpacing(8)
            top_layout.setVerticalSpacing(4)
            for widget in [
                self.lab_cap_fps,
                self.spin_cap_fps,
                self.lab_cap_pattern,
                self.combo_cap_pattern,
            ]:
                top_layout.removeWidget(widget)
                widget.setParent(None)

            top_layout.addWidget(live_actions, 0, 0, 1, 2)
            top_layout.addWidget(video_actions, 1, 0, 1, 2)
            top_layout.addWidget(self.frame_2, 0, 2, 2, 1)
            top_layout.addWidget(self.frame_3, 0, 3, 2, 1)
            top_layout.addWidget(
                self.btn_cap_reset_calibration,
                0,
                4,
                2,
                1,
                Qt.AlignmentFlag.AlignRight,
            )
            top_layout.setColumnStretch(0, 0)
            top_layout.setColumnStretch(1, 1)
            top_layout.setColumnStretch(2, 1)
            top_layout.setColumnStretch(3, 1)
            top_layout.setColumnStretch(4, 0)

        for panel in [self.frame_2, self.frame_3]:
            layout = panel.layout()
            if isinstance(layout, QVBoxLayout):
                layout.setContentsMargins(8, 4, 8, 4)
                layout.setSpacing(3)

        for button in [
            self.btn_cap_intrinsics_start,
            self.btn_cap_calculate_intrinsics,
            self.btn_cap_extrinsics_start,
            self.btn_cap_calculate_extrinsics,
            self.btn_camera_detect,
            self.btn_camera_start_live,
            self.btn_camera_stop_live,
            self.btn_camera_record,
            self.btn_camera_load_video,
        ]:
            button.setMinimumHeight(24)

        self.btn_cap_reset_calibration.setText("")
        self.btn_cap_reset_calibration.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.btn_cap_reset_calibration.setToolTip("Reset calibration")
        self.btn_cap_reset_calibration.setMinimumWidth(36)
        self.btn_cap_reset_calibration.setMaximumWidth(36)
        self.btn_cap_reset_calibration.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.btn_cap_reset_calibration.setProperty("danger", True)
        self.frame.setMinimumHeight(104)
        self.frame.setMaximumHeight(124)

    def _setup_resizable_shell(self) -> None:
        central_layout = self.centralwidget.layout()
        if central_layout is None or getattr(self, "_main_splitter", None) is not None:
            return

        for widget in [self.frame_menu, self.frame_pages, self.frame_console]:
            central_layout.removeWidget(widget)

        while central_layout.count():
            item = central_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        self.frame_menu.setMinimumWidth(120)
        self.frame_menu.setMaximumWidth(360)
        self.frame_pages.setMinimumWidth(420)
        self.frame_console.setMinimumHeight(125)

        self._right_splitter = QSplitter(Qt.Orientation.Vertical, self.centralwidget)
        self._right_splitter.setObjectName("splitter_content_console")
        self._right_splitter.setChildrenCollapsible(False)
        self._right_splitter.addWidget(self.frame_pages)
        self._right_splitter.addWidget(self.frame_console)
        self._right_splitter.setStretchFactor(0, 5)
        self._right_splitter.setStretchFactor(1, 2)
        self._right_splitter.setSizes([760, 150])

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal, self.centralwidget)
        self._main_splitter.setObjectName("splitter_main")
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self.frame_menu)
        self._main_splitter.addWidget(self._right_splitter)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([190, 1330])

        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._main_splitter, 0, 0, 1, 1)

    def _setup_settings_menu(self) -> None:
        if getattr(self, "menuSettings", None) is not None:
            return

        self.menuSettings = QtWidgets.QMenu(self.menuBar)
        self.menuSettings.setObjectName("menuSettings")
        self.menuSettings.setTitle("Settings")

        self.menuUiScale = QtWidgets.QMenu(self.menuSettings)
        self.menuUiScale.setObjectName("menuUiScale")
        self.menuUiScale.setTitle("UI scale")
        self._ui_scale_actions: list[QtGui.QAction] = []
        self._ui_scale_group = QtGui.QActionGroup(self)
        self._ui_scale_group.setExclusive(True)

        for label, value in [
            ("30%", 0.30),
            ("40%", 0.40),
            ("50%", 0.50),
            ("60%", 0.60),
            ("70%", 0.70),
            ("80%", 0.80),
            ("90%", 0.90),
            ("100%", 1.00),
            ("110%", 1.10),
            ("125%", 1.25),
            ("150%", 1.50),
        ]:
            action = QtGui.QAction(label, self)
            action.setCheckable(True)
            action.setData(value)
            action.triggered.connect(
                lambda checked=False, scale=value: self._apply_ui_scale(scale, persist=True)
            )
            self._ui_scale_group.addAction(action)
            self.menuUiScale.addAction(action)
            self._ui_scale_actions.append(action)

        self.menuSettings.addMenu(self.menuUiScale)

        self.menuOverlayScale = QtWidgets.QMenu(self.menuSettings)
        self.menuOverlayScale.setObjectName("menuOverlayScale")
        self.menuOverlayScale.setTitle("Overlay scale")
        self._overlay_scale_actions: list[QtGui.QAction] = []
        self._overlay_scale_group = QtGui.QActionGroup(self)
        self._overlay_scale_group.setExclusive(True)

        for label, value in [
            ("50%", 0.50),
            ("75%", 0.75),
            ("100%", 1.00),
            ("125%", 1.25),
            ("150%", 1.50),
            ("200%", 2.00),
        ]:
            action = QtGui.QAction(label, self)
            action.setCheckable(True)
            action.setData(value)
            action.triggered.connect(
                lambda checked=False, scale=value: self._apply_overlay_scale(scale)
            )
            self._overlay_scale_group.addAction(action)
            self.menuOverlayScale.addAction(action)
            self._overlay_scale_actions.append(action)

        self.menuSettings.addMenu(self.menuOverlayScale)
        self.menuBar.insertMenu(self.menuHelp.menuAction(), self.menuSettings)
        self._sync_ui_scale_menu()
        self._sync_overlay_scale_menu()

    def _setup_ui(self) -> None:
        self._designed_status_bar().showMessage("Idle")

    def _designed_status_bar(self):
        status_bar = getattr(self, "statusBar", None)
        return status_bar() if callable(status_bar) else status_bar

    def _set_status(self, message: str) -> None:
        status_bar = self._designed_status_bar()
        if status_bar is not None:
            status_bar.showMessage(message)

    def _apply_window_style(self) -> None:
        self._apply_ui_scale(self._configured_ui_scale(), persist=False)

    def _configured_ui_scale(self) -> float:
        try:
            value = float(getattr(self._config, "ui_scale", 0.70))
        except (TypeError, ValueError):
            value = 0.70
        return max(0.30, min(1.6, value))

    def _apply_ui_scale(self, scale: float, persist: bool) -> None:
        scale = max(0.30, min(1.6, float(scale)))
        self._current_ui_scale = scale

        app = QtWidgets.QApplication.instance()
        if app is not None:
            if not hasattr(self, "_base_app_font_point_size"):
                point_size = app.font().pointSizeF()
                self._base_app_font_point_size = point_size if point_size > 0 else 9.0
            font = app.font()
            font.setPointSizeF(max(7.0, self._base_app_font_point_size * scale))
            app.setFont(font)

        apply_styles(self, scale=scale)
        self._sync_ui_scale_menu()

        if persist:
            self._config.ui_scale = scale
            try:
                self._config.save()
            except Exception:  # noqa: BLE001
                pass

    def _sync_ui_scale_menu(self) -> None:
        actions = getattr(self, "_ui_scale_actions", [])
        if not actions:
            return
        current = getattr(self, "_current_ui_scale", self._configured_ui_scale())
        for action in actions:
            action.blockSignals(True)
            action.setChecked(abs(float(action.data()) - current) < 0.001)
            action.blockSignals(False)

    def _configured_overlay_scale(self) -> float:
        try:
            value = float(getattr(self._config, "overlay_scale", 1.0))
        except (TypeError, ValueError):
            value = 1.0
        return max(0.3, min(3.0, value))

    def _apply_overlay_scale(self, scale: float) -> None:
        scale = max(0.3, min(3.0, float(scale)))
        self._config.overlay_scale = scale
        try:
            self._config.save()
        except Exception:  # noqa: BLE001
            pass
        self._sync_overlay_scale_menu()
        # Force a preview refresh so overlays are redrawn at the new scale.
        panel = getattr(self, "_calibration_panel", None)
        if panel is not None and hasattr(panel, "preview_options_changed"):
            panel.preview_options_changed.emit()

    def _sync_overlay_scale_menu(self) -> None:
        actions = getattr(self, "_overlay_scale_actions", [])
        if not actions:
            return
        current = self._configured_overlay_scale()
        for action in actions:
            action.blockSignals(True)
            action.setChecked(abs(float(action.data()) - current) < 0.001)
            action.blockSignals(False)

    def _apply_initial_window_geometry(self) -> None:
        self.resize(1280, 800)
