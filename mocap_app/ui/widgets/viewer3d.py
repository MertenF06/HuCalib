from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from mocap_app.models.types import Pose3D, Pose3DKeypoint


SKELETON_EDGES_3D = [
    ("nose", "left_shoulder"),
    ("nose", "right_shoulder"),
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("right_shoulder", "right_elbow"),
    ("left_elbow", "left_wrist"),
    ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"),
    ("right_hip", "right_knee"),
    ("left_knee", "left_ankle"),
    ("right_knee", "right_ankle"),
]


class Pose3DViewerWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._pose: Pose3D | None = None
        self._reconstruction_mode = "unavailable"
        self._reconstructed_joints = 0
        self._mean_reprojection_error_px: float | None = None
        self._triangulation_status = "Idle"
        self._yaw = 0.65
        self._pitch = 0.38
        self._zoom = 1.0
        self._auto_rotate = True
        self._last_mouse_pos = QPoint()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(40)
        self.setMinimumHeight(460)
        self.setMouseTracking(True)

    def set_pose(self, pose: Pose3D | None) -> None:
        self._pose = pose
        self.update()

    def set_reconstruction_metadata(
        self,
        mode: str,
        reconstructed_joints: int,
        mean_reprojection_error_px: float | None,
        triangulation_status: str = "Idle",
    ) -> None:
        self._reconstruction_mode = mode
        self._reconstructed_joints = reconstructed_joints
        self._mean_reprojection_error_px = mean_reprojection_error_px
        self._triangulation_status = triangulation_status
        self.update()

    def _animate(self) -> None:
        if self._auto_rotate and self.isVisible():
            self._yaw += 0.006
            self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._auto_rotate = False
            self._last_mouse_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton:
            current = event.position().toPoint()
            delta = current - self._last_mouse_pos
            self._yaw += delta.x() * 0.01
            self._pitch = max(-1.1, min(1.1, self._pitch + delta.y() * 0.008))
            self._last_mouse_pos = current
            self.update()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self._auto_rotate = not self._auto_rotate
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        angle = event.angleDelta().y()
        self._zoom *= 1.1 if angle > 0 else 0.9
        self._zoom = max(0.4, min(2.8, self._zoom))
        self.update()
        super().wheelEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_background(painter)

            if self._pose is None or not self._pose.keypoints:
                painter.setPen(QColor("#334155"))
                empty_text = "No 3D pose yet"
                if self._triangulation_status:
                    empty_text = f"{empty_text}\n{self._triangulation_status}"
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, empty_text)
                self._draw_overlay(painter, keypoints=0)
                return

            self._draw_grid(painter)
            self._draw_axes(painter)
            self._draw_pose(painter, self._pose)
            self._draw_overlay(painter, keypoints=len(self._pose.keypoints))
        finally:
            # Ensure painter lifecycle is closed, even when draw code raises.
            if painter.isActive():
                painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor("#f8fbff"))
        gradient.setColorAt(1.0, QColor("#eaf1f9"))
        painter.fillRect(self.rect(), gradient)

    def _draw_grid(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor("#c8d4e2"), 1))
        for i in range(-4, 5):
            start = self._project_point(i * 0.25, -0.7, 0.0)
            end = self._project_point(i * 0.25, -0.7, 2.4)
            painter.drawLine(start, end)
            start2 = self._project_point(-1.0, -0.7, i * 0.25 + 1.2)
            end2 = self._project_point(1.0, -0.7, i * 0.25 + 1.2)
            painter.drawLine(start2, end2)

    def _draw_axes(self, painter: QPainter) -> None:
        origin = self._project_point(0.0, 0.0, 0.0)
        x_axis = self._project_point(0.8, 0.0, 0.0)
        y_axis = self._project_point(0.0, 0.8, 0.0)
        z_axis = self._project_point(0.0, 0.0, 2.0)

        painter.setPen(QPen(QColor("#ff6868"), 2))
        painter.drawLine(origin, x_axis)
        painter.setPen(QPen(QColor("#73ff7b"), 2))
        painter.drawLine(origin, y_axis)
        painter.setPen(QPen(QColor("#57c2ff"), 2))
        painter.drawLine(origin, z_axis)

    def _draw_pose(self, painter: QPainter, pose: Pose3D) -> None:
        keypoints_by_name = {keypoint.name: keypoint for keypoint in pose.keypoints}

        for left_name, right_name in SKELETON_EDGES_3D:
            left = keypoints_by_name.get(left_name)
            right = keypoints_by_name.get(right_name)
            if left is None or right is None:
                continue
            edge_confidence = min(left.confidence, right.confidence)
            edge_alpha = int(max(60.0, min(255.0, 255.0 * edge_confidence)))
            painter.setPen(QPen(QColor(244, 211, 114, edge_alpha), 2))
            left_point = self._project_keypoint(left)
            right_point = self._project_keypoint(right)
            painter.drawLine(left_point, right_point)

        for keypoint in pose.keypoints:
            if keypoint.confidence < 0.2:
                continue
            point = self._project_keypoint(keypoint)
            painter.setPen(Qt.PenStyle.NoPen)
            point_alpha = int(max(80.0, min(255.0, 255.0 * keypoint.confidence)))
            painter.setBrush(QColor(126, 255, 190, point_alpha))
            painter.drawEllipse(QRectF(point.x() - 3.5, point.y() - 3.5, 7.0, 7.0))

    def _draw_overlay(self, painter: QPainter, keypoints: int) -> None:
        painter.setPen(QColor("#334155"))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        mode = "Auto-Rotate ON" if self._auto_rotate else "Manual Orbit"
        painter.drawText(
            QRectF(12, 10, self.width() - 20, 20),
            Qt.AlignmentFlag.AlignLeft,
            f"{mode} | Zoom {self._zoom:.2f}x | Visible Keypoints {keypoints}",
        )
        reproj_text = (
            f"Mean Reprojection Error: {self._mean_reprojection_error_px:.2f}px"
            if self._mean_reprojection_error_px is not None
            else "Mean Reprojection Error: n/a"
        )
        painter.drawText(
            QRectF(12, 28, self.width() - 20, 18),
            Qt.AlignmentFlag.AlignLeft,
            f"Mode: {self._reconstruction_mode} | Reconstructed Joints: {self._reconstructed_joints} | {reproj_text}",
        )
        painter.drawText(
            QRectF(12, 48, self.width() - 20, 36),
            int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap),
            self._triangulation_status,
        )
        painter.drawText(
            QRectF(12, self.height() - 24, self.width() - 20, 20),
            Qt.AlignmentFlag.AlignLeft,
            "Drag to rotate, wheel to zoom, double-click to toggle auto-rotate",
        )

    def _project_keypoint(self, keypoint: Pose3DKeypoint) -> QPointF:
        return self._project_point(keypoint.x, keypoint.y, keypoint.z)

    def _project_point(self, x: float, y: float, z: float) -> QPointF:
        cos_yaw = math.cos(self._yaw)
        sin_yaw = math.sin(self._yaw)
        cos_pitch = math.cos(self._pitch)
        sin_pitch = math.sin(self._pitch)

        xz_x = x * cos_yaw - z * sin_yaw
        xz_z = x * sin_yaw + z * cos_yaw

        yz_y = y * cos_pitch - xz_z * sin_pitch
        yz_z = y * sin_pitch + xz_z * cos_pitch

        depth = yz_z + 5.0
        scale = (min(self.width(), self.height()) * 0.33 * self._zoom) / max(depth, 0.2)
        center_x = self.width() * 0.5
        center_y = self.height() * 0.62
        sx = center_x + xz_x * scale
        sy = center_y - yz_y * scale
        return QPointF(sx, sy)
