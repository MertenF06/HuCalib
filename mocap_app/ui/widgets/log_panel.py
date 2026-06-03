from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class LogPanelWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Time", "Level", "Logger", "Message"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet(
            """
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f6f9fc;
                color: #1f2937;
                gridline-color: #d9e2ec;
            }
            QHeaderView::section {
                background-color: #eef3f9;
                color: #1f2937;
                padding: 6px 4px;
                border: 1px solid #d4dde8;
            }
            """
        )

        self._severity_filter = QComboBox()
        self._severity_filter.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self._severity_filter.currentTextChanged.connect(self._apply_filter)

        self._clear_button = QPushButton("Clear Logs")
        self._clear_button.clicked.connect(self._table.clearContents)
        self._clear_button.clicked.connect(lambda: self._table.setRowCount(0))

        toolbar = QHBoxLayout()
        toolbar.addWidget(self._severity_filter)
        toolbar.addWidget(self._clear_button)
        toolbar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self._table)

    def append_record(self, payload: dict[str, Any]) -> None:
        timestamp = str(payload.get("timestamp", ""))
        severity = str(payload.get("severity", "INFO")).upper()
        logger = str(payload.get("logger", "app"))
        message = str(payload.get("message", ""))

        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(timestamp))
        self._table.setItem(row, 1, QTableWidgetItem(severity))
        self._table.setItem(row, 2, QTableWidgetItem(logger))
        self._table.setItem(row, 3, QTableWidgetItem(message))
        self._apply_severity_color(row, severity)
        self._table.scrollToBottom()
        self._apply_filter()

    def append_message(self, message: str) -> None:
        # Backward-compatible fallback for text-only logging calls.
        self.append_record(
            {
                "timestamp": "",
                "severity": "INFO",
                "logger": "app",
                "message": message,
            }
        )

    def _apply_filter(self) -> None:
        wanted = self._severity_filter.currentText()
        for row in range(self._table.rowCount()):
            level_item = self._table.item(row, 1)
            level = level_item.text().upper() if level_item else "INFO"
            hidden = wanted != "ALL" and level != wanted
            self._table.setRowHidden(row, hidden)

    def _apply_severity_color(self, row: int, severity: str) -> None:
        color_map = {
            "DEBUG": QColor("#475569"),
            "INFO": QColor("#1f2937"),
            "WARNING": QColor("#a16207"),
            "ERROR": QColor("#b91c1c"),
            "CRITICAL": QColor("#7f1d1d"),
        }
        color = color_map.get(severity.upper(), QColor("#1f2937"))
        for col in range(4):
            item = self._table.item(row, col)
            if item is not None:
                item.setForeground(color)
