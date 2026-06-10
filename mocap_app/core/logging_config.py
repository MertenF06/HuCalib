from __future__ import annotations

import logging
import sys
from pathlib import Path


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mocap_app.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # StreamHandler() binds to sys.stderr at construction. In the packaged
    # windowed build (console=False) there is no console, so sys.stderr is None
    # and every emit would raise "'NoneType' object has no attribute 'write'" --
    # which then gets printed into the in-app console once stdout/stderr are
    # redirected there. Only attach the console handler when a real stream
    # exists; the file handler below always captures the full log regardless.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

