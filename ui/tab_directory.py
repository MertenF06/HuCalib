"""Directory tab - lightweight file explorer rooted in the project folder."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt, QFileInfo
from PySide6.QtWidgets import (
    QFileDialog,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

MAX_TREE_DEPTH = 10


class TabDirectory:
    def __init__(self, logic_instance) -> None:
        self.logic = logic_instance
        self.window = logic_instance.window
        self.root_directory: Path | None = None
        self.tree_widget: QTreeWidget | None = None
        self.path_input: QLineEdit | None = None
        self.icon_provider: QFileIconProvider | None = None

    def setup(self) -> None:
        config = getattr(self.logic, "config", None)
        if config is not None:
            self.root_directory = Path(config.default_sessions_dir)
        else:
            self.root_directory = Path.cwd()

        self.icon_provider = QFileIconProvider()

        frame_layout = QVBoxLayout()
        frame_layout.setContentsMargins(12, 12, 12, 12)
        frame_layout.setSpacing(8)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(6)

        up_btn = QPushButton("↑ Omhoog")
        up_btn.clicked.connect(self.go_up_directory)
        toolbar_layout.addWidget(up_btn)

        path_label = QLabel("Pad:")
        path_label.setProperty("section", True)
        toolbar_layout.addWidget(path_label)

        self.path_input = QLineEdit()
        self.path_input.setText(str(self.root_directory))
        self.path_input.setReadOnly(True)
        toolbar_layout.addWidget(self.path_input, stretch=1)

        refresh_btn = QPushButton("Vernieuwen")
        refresh_btn.clicked.connect(self.refresh_directory)
        toolbar_layout.addWidget(refresh_btn)

        browse_btn = QPushButton("Bladeren...")
        browse_btn.clicked.connect(self.browse_directory)
        toolbar_layout.addWidget(browse_btn)

        set_default_btn = QPushButton("Stel in als standaard")
        set_default_btn.clicked.connect(self.set_as_default)
        toolbar_layout.addWidget(set_default_btn)

        frame_layout.addLayout(toolbar_layout)

        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Naam", "Type", "Gewijzigd"])
        self.tree_widget.setColumnCount(3)
        self.tree_widget.setMinimumHeight(400)
        self.tree_widget.itemExpanded.connect(self.on_tree_item_expanded)
        frame_layout.addWidget(self.tree_widget, stretch=1)

        self.window.frame_directory.setLayout(frame_layout)

        self.load_root_directory(self.root_directory)

    def load_root_directory(self, directory_path) -> None:
        try:
            directory_path = Path(directory_path)
            directory_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self.window, "Fout", f"Kon directory niet aanmaken: {exc}"
            )
            return

        if not directory_path.is_dir():
            QtWidgets.QMessageBox.warning(
                self.window, "Fout", f"Pad is geen directory: {directory_path}"
            )
            return

        self.root_directory = directory_path
        if self.path_input is not None:
            self.path_input.setText(str(directory_path))
        if self.tree_widget is None:
            return

        self.tree_widget.clear()
        root_item = QTreeWidgetItem(self.tree_widget)
        root_item.setText(0, directory_path.name or str(directory_path))
        root_item.setData(0, Qt.ItemDataRole.UserRole, str(directory_path))
        assert self.icon_provider is not None
        root_item.setIcon(0, self.icon_provider.icon(QFileIconProvider.IconType.Folder))
        root_item.setText(1, "Map")
        self._populate_tree_item_lazy(root_item, directory_path, 1)
        root_item.setExpanded(True)

    def _populate_tree_item_lazy(self, parent_item, directory_path: Path, depth_level: int) -> None:
        while parent_item.childCount() > 0:
            parent_item.removeChild(parent_item.child(0))
        try:
            items = sorted(
                directory_path.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
        except (PermissionError, OSError):
            return

        assert self.icon_provider is not None
        for item_path in items:
            if item_path.name.startswith("."):
                continue

            item = QTreeWidgetItem(parent_item)
            item.setText(0, item_path.name)
            item.setData(0, Qt.ItemDataRole.UserRole, str(item_path))
            if item_path.is_dir():
                item.setIcon(0, self.icon_provider.icon(QFileIconProvider.IconType.Folder))
                item.setText(1, "Map")
            else:
                file_info = QFileInfo(str(item_path))
                item.setIcon(0, self.icon_provider.icon(file_info))
                item.setText(1, "Bestand")

            try:
                mod_time = item_path.stat().st_mtime
                item.setText(2, datetime.fromtimestamp(mod_time).strftime("%d-%m-%Y %H:%M"))
            except OSError:
                item.setText(2, "-")

            if item_path.is_dir() and depth_level < MAX_TREE_DEPTH:
                dummy = QTreeWidgetItem(item)
                dummy.setText(0, "Laden...")
                item.setData(0, Qt.ItemDataRole.UserRole + 1, False)

    def on_tree_item_expanded(self, item) -> None:
        item_path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if not item_path_str:
            return
        if item.data(0, Qt.ItemDataRole.UserRole + 1) is False:
            self._populate_tree_item_lazy(
                item, Path(item_path_str), self._get_depth_level(item) + 1
            )
            item.setData(0, Qt.ItemDataRole.UserRole + 1, True)

    @staticmethod
    def _get_depth_level(item) -> int:
        depth = 0
        current = item
        while current.parent() is not None:
            depth += 1
            current = current.parent()
        return depth

    def go_up_directory(self) -> None:
        if self.root_directory is None:
            return
        parent_path = Path(self.root_directory).parent
        if parent_path != self.root_directory:
            self.load_root_directory(parent_path)
        else:
            QtWidgets.QMessageBox.information(
                self.window, "Info", "U bent al in de root directory."
            )

    def refresh_directory(self) -> None:
        if self.root_directory is not None:
            self.load_root_directory(self.root_directory)

    def browse_directory(self) -> None:
        selected_dir = QFileDialog.getExistingDirectory(
            self.window,
            "Selecteer een directory",
            str(self.root_directory or Path.cwd()),
        )
        if selected_dir:
            self.load_root_directory(selected_dir)

    def set_as_default(self) -> None:
        config = getattr(self.logic, "config", None)
        if config is None or self.root_directory is None:
            return
        config.default_sessions_dir = self.root_directory
        config.save()
        QtWidgets.QMessageBox.information(
            self.window,
            "Standaard locatie",
            f"Standaard locatie ingesteld op:\n{self.root_directory}",
        )
