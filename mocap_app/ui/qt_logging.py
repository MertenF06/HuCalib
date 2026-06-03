from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Signal


class QtLogEmitter(QObject):
    record_emitted = Signal(object)


class QtSignalLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = QtLogEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        self.emitter.record_emitted.emit(payload)
