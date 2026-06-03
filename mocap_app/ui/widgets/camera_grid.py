from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from mocap_app.models.types import FramePacket, Pose2D
from mocap_app.ui.widgets.frame_view import CameraFrameWidget


class CameraGridWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._empty_hint = QLabel("No source slots configured.\nAdd cameras in Capture and press Start Live.")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet(
            "color: #4b5563; border: 1px dashed #94a3b8; border-radius: 6px; background: #f8fafc; padding: 12px;"
        )

        self._grid_container = QWidget()
        self._layout = QGridLayout(self._grid_container)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(6)
        self._widgets: dict[str, CameraFrameWidget] = {}
        self._source_order: list[str] = []
        self._last_columns = 0
        self.setMinimumSize(720, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._grid_container, stretch=1)
        root.addWidget(self._empty_hint, stretch=1)
        self._empty_hint.hide()
        self.setStyleSheet("QWidget { background-color: #f4f7fb; }")

    def set_sources(self, source_ids: list[str]) -> None:
        trimmed_ids = source_ids[:4]
        existing_ids = set(self._widgets.keys())
        requested_ids = set(trimmed_ids)

        for source_id in sorted(existing_ids - requested_ids):
            widget = self._widgets.pop(source_id)
            self._layout.removeWidget(widget)
            widget.deleteLater()

        for source_id in trimmed_ids:
            if source_id in self._widgets:
                continue
            self._widgets[source_id] = CameraFrameWidget(title=f"Source {source_id}")

        self._source_order = list(trimmed_ids)
        self._rebuild_layout(order=self._source_order)
        self._update_empty_hint_visibility()

        for source_id in trimmed_ids:
            widget = self._widgets.get(source_id)
            if widget is not None:
                widget.show_placeholder()

    def update_batch(
        self,
        frames: dict[str, FramePacket],
        poses_2d: dict[str, Pose2D],
        reprojected_points_px: dict[str, dict[str, tuple[float, float]]] | None = None,
    ) -> None:
        for source_id, frame in frames.items():
            if source_id not in self._widgets:
                if len(self._widgets) >= 4:
                    continue
                self._widgets[source_id] = CameraFrameWidget(title=f"Source {source_id}")
                self._source_order = sorted(self._widgets.keys())
                self._rebuild_layout(order=self._source_order)
                self._update_empty_hint_visibility()
            self._widgets[source_id].set_frame(
                frame_packet=frame,
                pose_2d=poses_2d.get(source_id),
                reprojected_points_px=(reprojected_points_px or {}).get(source_id),
            )

    def _rebuild_layout(self, order: list[str]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        if not order:
            self._update_empty_hint_visibility()
            return

        columns = self._calculate_columns(len(order))
        self._last_columns = columns
        rows = (len(order) + columns - 1) // columns

        for col in range(columns):
            self._layout.setColumnStretch(col, 1)
        for row in range(rows):
            self._layout.setRowStretch(row, 1)

        for idx, source_id in enumerate(order):
            widget = self._widgets[source_id]
            row = idx // columns
            column = idx % columns
            self._layout.addWidget(widget, row, column)
        self._update_empty_hint_visibility()

    def _calculate_columns(self, count: int) -> int:
        if count <= 1:
            return 1
        if count == 2:
            return 2 if self.width() >= 900 else 1
        return 2

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        if self._source_order:
            columns = self._calculate_columns(len(self._source_order))
            if columns != self._last_columns:
                self._rebuild_layout(order=self._source_order)
        super().resizeEvent(event)

    def _update_empty_hint_visibility(self) -> None:
        has_sources = bool(self._source_order)
        self._grid_container.setVisible(has_sources)
        self._empty_hint.setVisible(not has_sources)
