from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Filename of the developer-maintained default settings. A copy is shipped
# inside the app bundle and materialised in the persistent app folder so it can
# be hand-edited; the "Reset naar standaardinstellingen" button restores from it.
DEFAULTS_FILENAME = "default_settings.json"
USER_SETTINGS_FILENAME = "app_settings.json"
# Marker recording which app version last refreshed the persistent defaults, so
# an update overwrites the hand-editable copy exactly once per new version.
_DEFAULTS_VERSION_MARKER = ".defaults_version"


def _app_root() -> Path:
    """Persistent folder for writable user data (projects, results, logs,
    sessions, settings).

    Velopack runs the installed app from ``...\\HuCalib\\current\\`` and replaces
    that ``current`` folder wholesale on every update. Writable data therefore
    lives in the *parent* of ``current`` so updates never wipe a user's
    projects or settings. In a source checkout this is just the repo root.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "current":
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parents[2]


def _bundled_defaults_path() -> Path:
    """The read-only default_settings.json shipped inside the app bundle."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / DEFAULTS_FILENAME
    return Path(__file__).resolve().parents[2] / DEFAULTS_FILENAME


def _persistent_defaults_path() -> Path:
    """The hand-editable default_settings.json in the persistent app folder."""
    return _app_root() / DEFAULTS_FILENAME


def _user_settings_path() -> Path:
    """Per-user settings file, kept in the persistent folder so it survives
    updates."""
    return _app_root() / USER_SETTINGS_FILENAME


def ensure_persistent_defaults() -> Path:
    """Return the path to the developer defaults, materialising the persistent
    copy when needed.

    In a packaged build the bundled template is copied into the persistent app
    folder the first time and again whenever the app version changes, so each
    update ships fresh defaults while a user's manual edits survive between
    updates. In a source checkout the repo file is used in place (edit it
    directly).
    """
    bundled = _bundled_defaults_path()
    if not getattr(sys, "frozen", False):
        return bundled

    persistent = _persistent_defaults_path()
    marker = _app_root() / _DEFAULTS_VERSION_MARKER
    try:
        from mocap_app import __version__
    except Exception:  # noqa: BLE001 - version lookup must never break startup
        __version__ = ""

    try:
        stored = marker.read_text(encoding="utf-8").strip() if marker.exists() else None
    except OSError:
        stored = None

    if not persistent.exists() or stored != __version__:
        try:
            if bundled.exists():
                persistent.parent.mkdir(parents=True, exist_ok=True)
                persistent.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
                marker.write_text(__version__, encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("Could not refresh default settings %s: %s", persistent, exc)
            return persistent if persistent.exists() else bundled
    return persistent


def load_default_settings() -> dict[str, Any]:
    """Read the developer's default settings (basic fields + an ``advanced``
    section). Returns an empty dict when no readable file is available."""
    path = ensure_persistent_defaults()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not read default settings %s: %s", path, exc)
    return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base`` (override wins)."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# Directories that must always live inside the persistent app folder, keyed by
# the subfolder name they map to. These are derived from the module/exe
# location, never read from or written to the settings file, so a settings file
# copied between machines can't pin them to absolute paths.
_PROJECT_RELATIVE_DIRS = {
    "calibration_dir": "Projecten",
    "results_dir": "Resultaten",
    "logs_dir": "logs",
    "sessions_dir": "sessions",
    "default_sessions_dir": "sessions",
}
_DERIVED_PATH_KEYS = {"app_root", *_PROJECT_RELATIVE_DIRS}
# Persisted via the ``advanced`` JSON key rather than its raw field name.
_NON_PERSISTED_KEYS = {"advanced_settings"}


@dataclass(slots=True)
class AppConfig:
    app_name: str = "HuCalib"
    app_root: Path = field(default_factory=_app_root)
    target_fps: float = 30.0
    default_camera_csv: str = "0"
    calibration_dir: Path = field(default_factory=lambda: _app_root() / "Projecten")
    results_dir: Path = field(default_factory=lambda: _app_root() / "Resultaten")
    logs_dir: Path = field(default_factory=lambda: _app_root() / "logs")
    sessions_dir: Path = field(default_factory=lambda: _app_root() / "sessions")
    default_sessions_dir: Path = field(default_factory=lambda: _app_root() / "sessions")
    ui_scale: float = 0.70
    overlay_scale: float = 1.0
    camera_labels: dict[str, str] = field(default_factory=dict)
    # Nested advanced UI settings (live/board/workflow/aux). Merged from the
    # developer defaults and the user's saved overrides; applied to the
    # advanced-settings controls on startup.
    advanced_settings: dict[str, Any] = field(default_factory=dict)

    def ensure_directories(self) -> None:
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_paths(self) -> None:
        """Force all directories to live under the persistent app folder."""
        root = _app_root()
        self.app_root = root
        for attr, subfolder in _PROJECT_RELATIVE_DIRS.items():
            setattr(self, attr, root / subfolder)

    def _apply_settings_dict(self, data: dict[str, Any]) -> None:
        """Overlay a settings dict (basic fields + nested ``advanced``)."""
        for key, value in data.items():
            if key == "advanced" and isinstance(value, dict):
                self.advanced_settings = _deep_merge(self.advanced_settings, value)
                continue
            if (
                not hasattr(self, key)
                or key in _DERIVED_PATH_KEYS
                or key in _NON_PERSISTED_KEYS
            ):
                # Unknown keys and derived absolute paths are ignored, so a
                # settings file copied between machines can't pin them.
                continue
            current = getattr(self, key)
            if isinstance(current, Path) and isinstance(value, str):
                setattr(self, key, Path(value))
            else:
                setattr(self, key, value)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        settings_path = path or _user_settings_path()
        config = cls()
        # Layer: code defaults -> developer defaults -> user overrides.
        config._apply_settings_dict(load_default_settings())
        if settings_path.exists():
            try:
                data: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("Could not read settings %s: %s", settings_path, exc)
                data = {}
            config._apply_settings_dict(data)
        config._normalize_paths()
        return config

    def save(self, path: Path | None = None) -> None:
        settings_path = path or _user_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if key in _DERIVED_PATH_KEYS or key in _NON_PERSISTED_KEYS:
                # Never persist machine-specific absolute directories or the raw
                # advanced field (written under the "advanced" key below).
                continue
            if isinstance(value, Path):
                data[key] = str(value)
            else:
                data[key] = value
        if self.advanced_settings:
            data["advanced"] = self.advanced_settings
        settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
