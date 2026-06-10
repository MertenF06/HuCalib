"""In-app auto-update via Velopack + GitHub Releases.

The installed app polls the GitHub Releases of the repo below for a newer
Velopack package. If one is found the user is asked whether to install it; on
confirmation the update is downloaded in the background and applied, after
which the app restarts itself on the new version.

All network/disk work runs on background QThreads so the UI never blocks.
Nothing happens when running from a source checkout (not frozen), because
Velopack can only update an installed build.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

LOGGER = logging.getLogger(__name__)

# The GitHub repository that hosts the published releases. `vpk upload github`
# pushes the update feed (RELEASES file + .nupkg packages) here, and the
# installed app reads it back from the same place.
GITHUB_REPO_URL = "https://github.com/MertenF06/HuCalib"


def updates_supported() -> bool:
    """Velopack only works from an installed/packaged build. Running from a
    source checkout has nothing to update, so we skip silently."""
    return getattr(sys, "frozen", False)


def _make_manager():
    """Build a fresh UpdateManager pointed at the GitHub release feed.

    The manager is cheap to recreate and holds no per-call state, so each
    worker makes its own rather than sharing one across threads.
    """
    import velopack

    return velopack.UpdateManager(velopack.GithubSource(GITHUB_REPO_URL))


class _CheckWorker(QThread):
    """Polls the release feed for a newer version (blocking call, off-thread)."""

    update_found = Signal(object)  # emits a velopack UpdateInfo
    no_update = Signal()
    failed = Signal(str)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            info = _make_manager().check_for_updates()
        except Exception as exc:  # network down, rate limited, no feed yet, ...
            LOGGER.info("Update check failed: %s", exc)
            self.failed.emit(str(exc))
            return
        if info is None:
            self.no_update.emit()
        else:
            self.update_found.emit(info)


class _DownloadWorker(QThread):
    """Downloads the pending update, reporting 0-100% progress."""

    progress = Signal(int)
    finished_ok = Signal(object)  # emits the UpdateInfo to apply
    failed = Signal(str)

    def __init__(self, update_info: object) -> None:
        super().__init__()
        self._update_info = update_info

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            manager = _make_manager()
            manager.download_updates(
                self._update_info,
                lambda pct: self.progress.emit(int(pct)),
            )
        except Exception as exc:
            LOGGER.exception("Update download failed")
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(self._update_info)


class UpdateController(QObject):
    """Orchestrates the check -> ask -> download -> apply flow.

    Keep a reference to the instance alive for the duration of the app (passing
    the main window as parent is enough). Threads are parented to this object so
    they are cleaned up with it.
    """

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._check_worker: _CheckWorker | None = None
        self._download_worker: _DownloadWorker | None = None
        self._progress: QProgressDialog | None = None
        # When True (manual "Check for updates" menu action) we also report the
        # "you're up to date" / "check failed" cases; the silent startup check
        # stays quiet so it never nags.
        self._notify_when_current = False

    # -- public entry points ------------------------------------------------

    def start_background_check(self) -> None:
        """Silent check used at startup. Only prompts if an update exists."""
        self._notify_when_current = False
        self._begin_check()

    def check_now(self) -> None:
        """Manual check (menu action). Always reports the outcome."""
        self._notify_when_current = True
        self._begin_check()

    # -- internals ----------------------------------------------------------

    def _begin_check(self) -> None:
        if not updates_supported():
            if self._notify_when_current:
                QMessageBox.information(
                    self._window,
                    "Updates",
                    "Automatische updates zijn alleen beschikbaar in de "
                    "geïnstalleerde versie van HuCalib.",
                )
            return
        if self._check_worker is not None and self._check_worker.isRunning():
            return  # a check is already in flight
        worker = _CheckWorker(self)
        worker.update_found.connect(self._on_update_found)
        worker.no_update.connect(self._on_no_update)
        worker.failed.connect(self._on_check_failed)
        self._check_worker = worker
        worker.start()

    def _on_no_update(self) -> None:
        if self._notify_when_current:
            QMessageBox.information(
                self._window, "Updates", "Je gebruikt al de nieuwste versie."
            )

    def _on_check_failed(self, message: str) -> None:
        if self._notify_when_current:
            QMessageBox.warning(
                self._window,
                "Updates",
                "Kon niet controleren op updates:\n" + message,
            )

    def _on_update_found(self, update_info: object) -> None:
        version = update_info.TargetFullRelease.Version
        answer = QMessageBox.question(
            self._window,
            "Update beschikbaar",
            f"Versie {version} is beschikbaar.\n\nNu downloaden en installeren? "
            "De applicatie herstart automatisch zodra de update klaar is.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog(
            f"Versie {version} downloaden…", None, 0, 100, self._window
        )
        progress.setWindowTitle("Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)  # applying mid-download would corrupt it
        progress.setMinimumDuration(0)
        progress.setValue(0)
        self._progress = progress

        worker = _DownloadWorker(update_info)
        worker.setParent(self)
        worker.progress.connect(progress.setValue)
        worker.finished_ok.connect(self._on_download_finished)
        worker.failed.connect(self._on_download_failed)
        self._download_worker = worker
        worker.start()

    def _on_download_failed(self, message: str) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        QMessageBox.warning(
            self._window,
            "Update mislukt",
            "De update kon niet worden gedownload:\n" + message,
        )

    def _on_download_finished(self, update_info: object) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        # apply_updates_and_restart hands control to Update.exe, which waits for
        # this process to exit, swaps in the new version and relaunches it.
        try:
            _make_manager().apply_updates_and_restart(update_info)
        except Exception as exc:
            LOGGER.exception("Applying update failed")
            QMessageBox.warning(
                self._window,
                "Update mislukt",
                "De update kon niet worden toegepast:\n" + str(exc),
            )
