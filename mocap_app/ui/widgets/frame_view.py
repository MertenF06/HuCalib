from __future__ import annotations

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from mocap_app.models.types import FramePacket, Pose2D


SKELETON_EDGES = [
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


class CameraFrameWidget(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("color: #1f2937; font-weight: 600;")
        self._image_label = QLabel("")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(260, 180)
        self._image_label.setStyleSheet(
            "background-color: #f8fafc; color: #475569; border-radius: 3px; border: 1px dashed #94a3b8;"
        )
        self._last_pixmap: QPixmap | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._title_label)
        layout.addWidget(self._image_label, stretch=1)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { background-color: #ffffff; border: 1px solid #d4dde8; border-radius: 4px; }")
        self._set_placeholder_text()

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)
        if self._last_pixmap is None:
            self._set_placeholder_text()

    def show_placeholder(self, message: str | None = None) -> None:
        self._last_pixmap = None
        self._image_label.setStyleSheet(
            "background-color: #f8fafc; color: #475569; border-radius: 3px; border: 1px dashed #94a3b8;"
        )
        self._set_placeholder_text(message=message)

    def _set_placeholder_text(self, message: str | None = None) -> None:
        details = message or "No signal yet\nPress Start Live"
        self._image_label.setText(f"{self._title}\n\n{details}")
        self._image_label.setPixmap(QPixmap())

    def set_frame(
        self,
        frame_packet: FramePacket,
        pose_2d: Pose2D | None = None,
        reprojected_points_px: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        frame = frame_packet.frame_bgr.copy()
        cv2.putText(
            frame,
            f"{frame_packet.source_id} | frame {frame_packet.frame_index}",
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (210, 220, 230),
            1,
            cv2.LINE_AA,
        )

        if pose_2d is not None and pose_2d.keypoints:
            keypoints_by_name = {keypoint.name: keypoint for keypoint in pose_2d.keypoints}
            height, width = frame.shape[:2]

            for left_name, right_name in SKELETON_EDGES:
                left = keypoints_by_name.get(left_name)
                right = keypoints_by_name.get(right_name)
                if left is None or right is None:
                    continue
                p1 = (int(left.x * width), int(left.y * height))
                p2 = (int(right.x * width), int(right.y * height))
                cv2.line(frame, p1, p2, (0, 200, 255), 2, cv2.LINE_AA)

            for keypoint in pose_2d.keypoints:
                if keypoint.confidence < 0.2:
                    continue
                px = int(keypoint.x * width)
                py = int(keypoint.y * height)
                cv2.circle(frame, (px, py), 4, (0, 255, 120), -1, cv2.LINE_AA)

        if reprojected_points_px:
            for keypoint_name, (x_px, y_px) in reprojected_points_px.items():
                x = int(x_px)
                y = int(y_px)
                cv2.drawMarker(
                    frame,
                    (x, y),
                    (255, 70, 255),
                    markerType=cv2.MARKER_CROSS,
                    markerSize=10,
                    thickness=2,
                    line_type=cv2.LINE_AA,
                )
                cv2.putText(
                    frame,
                    keypoint_name,
                    (x + 6, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (255, 160, 255),
                    1,
                    cv2.LINE_AA,
                )

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        bytes_per_line = channels * width
        image = QImage(rgb.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).copy()
        self._last_pixmap = QPixmap.fromImage(image)
        self._image_label.setText("")
        self._image_label.setStyleSheet(
            "background-color: #f8fafc; color: #475569; border-radius: 3px; border: 1px solid #cbd5e1;"
        )
        self._render_last_pixmap()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._render_last_pixmap()
        super().resizeEvent(event)

    def _render_last_pixmap(self) -> None:
        if self._last_pixmap is None:
            return
        self._image_label.setPixmap(
            self._last_pixmap.scaled(
                self._image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )
