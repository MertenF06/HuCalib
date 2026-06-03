from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def _app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_settings_path() -> Path:
    return _app_root() / "calibration" / "app_settings.json"


# Directories that must always live inside the project, keyed by the
# subfolder name they map to under the project root. These are derived from
# the module location, never read from or written to the settings file, so a
# settings file copied between machines can't pin them to absolute paths.
_PROJECT_RELATIVE_DIRS = {
    "calibration_dir": "calibration",
    "logs_dir": "logs",
    "sessions_dir": "sessions",
    "default_sessions_dir": "sessions",
}
_DERIVED_PATH_KEYS = {"app_root", *_PROJECT_RELATIVE_DIRS}


@dataclass(slots=True)
class AppConfig:
    app_name: str = "PhysioMotionTracker"
    app_root: Path = field(default_factory=_app_root)
    target_fps: float = 30.0
    default_camera_csv: str = "0"
    calibration_dir: Path = field(default_factory=lambda: _app_root() / "calibration")
    logs_dir: Path = field(default_factory=lambda: _app_root() / "logs")
    sessions_dir: Path = field(default_factory=lambda: _app_root() / "sessions")
    default_sessions_dir: Path = field(default_factory=lambda: _app_root() / "sessions")
    ui_scale: float = 0.70
    overlay_scale: float = 1.0
    camera_labels: dict[str, str] = field(default_factory=dict)

    def ensure_directories(self) -> None:
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_paths(self) -> None:
        """Force all directories to live under the current project root."""
        root = _app_root()
        self.app_root = root
        for attr, subfolder in _PROJECT_RELATIVE_DIRS.items():
            setattr(self, attr, root / subfolder)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        settings_path = path or _default_settings_path()
        config = cls()
        if settings_path.exists():
            try:
                data: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("Could not read settings %s: %s", settings_path, exc)
                data = {}
            for key, value in data.items():
                if not hasattr(config, key) or key in _DERIVED_PATH_KEYS:
                    # Path fields are always derived from the project root, so
                    # any absolute path persisted in the settings file is ignored.
                    continue
                current = getattr(config, key)
                if isinstance(current, Path) and isinstance(value, str):
                    setattr(config, key, Path(value))
                else:
                    setattr(config, key, value)
        config._normalize_paths()
        return config

    def save(self, path: Path | None = None) -> None:
        settings_path = path or _default_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if key in _DERIVED_PATH_KEYS:
                # Never persist machine-specific absolute directories.
                continue
            if isinstance(value, Path):
                data[key] = str(value)
            else:
                data[key] = value
        settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
