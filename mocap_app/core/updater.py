"""In-app auto-update via Velopack + GitHub Releases.

The installed app polls the GitHub Releases of the repo below for a newer
Velopack package. If one is found the user is asked whether to install it; on
confirmation the update is downloaded in the background and applied, after
which the app restarts itself on the new version.

The silent startup check retries transient failures (see _RETRY_DELAYS_MS) and
then re-checks every _PERIODIC_INTERVAL_MS, so an update published while the
app is open is still offered. A version the user declines is skipped by the
periodic checks for the rest of the session; the manual menu check always
reports and offers everything.

All network/disk work runs on background QThreads so the UI never blocks.
Nothing happens when running from a source checkout (not frozen), because
Velopack can only update an installed build.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

LOGGER = logging.getLogger(__name__)

# The GitHub repository that hosts the published releases. `vpk upload github`
# pushes the update feed (RELEASES file + .nupkg packages) here, and the
# installed app reads it back from the same place.
GITHUB_REPO_URL = "https://github.com/MertenF06/HuCalib"

# The silent check runs once shortly after startup. A single transient network
# failure (sleeping wifi, captive portal, GitHub rate limit) would otherwise
# mean no update prompt for the entire session, so failures are retried with
# these delays before falling back to the periodic interval.
_RETRY_DELAYS_MS = (60_000, 300_000, 900_000)  # 1 min, 5 min, 15 min

# Long-running sessions re-check on this interval so a release published while
# the app is open still gets offered without a restart.
_PERIODIC_INTERVAL_MS = 4 * 60 * 60 * 1000  # 4 hours


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

    ## Emitted with the velopack ``UpdateInfo`` when a newer version exists.
    update_found = Signal(object)
    ## Emitted when the feed was reachable but no newer version exists.
    no_update = Signal()
    ## Emitted with an error message when the check could not complete.
    failed = Signal(str)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        """Query the release feed once and emit exactly one of the signals."""
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

    ## Download progress in whole percents (0-100).
    progress = Signal(int)
    ## Emitted with the ``UpdateInfo`` to apply once the download completed.
    finished_ok = Signal(object)
    ## Emitted with an error message when the download failed.
    failed = Signal(str)

    def __init__(self, update_info: object) -> None:
        """@param update_info  The velopack ``UpdateInfo`` to download."""
        super().__init__()
        self._update_info = update_info

    def run(self) -> None:  # noqa: D401 - QThread entry point
        """Download the update, forwarding progress, then emit the outcome."""
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
        """@param window  Main window used as dialog parent and QObject parent."""
        super().__init__(window)
        self._window = window
        self._check_worker: _CheckWorker | None = None
        self._download_worker: _DownloadWorker | None = None
        self._progress: QProgressDialog | None = None
        # When True (manual "Check for updates" menu action) we also report the
        # "you're up to date" / "check failed" cases; the silent startup check
        # stays quiet so it never nags.
        self._notify_when_current = False
        # Position in _RETRY_DELAYS_MS for the silent check; reset on success.
        self._retry_index = 0
        # Version the user declined this session: the periodic re-check skips
        # it instead of asking again, a manual check still offers it.
        self._declined_version: str | None = None
        # Single pending timer for the next silent check (retry or periodic);
        # restarting it replaces the previous schedule so checks never stack.
        self._next_check_timer = QTimer(self)
        self._next_check_timer.setSingleShot(True)
        self._next_check_timer.timeout.connect(self.start_background_check)

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
        """Start a check worker unless updates are unsupported or one is
        already in flight."""
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

    def _schedule_silent_check(self, delay_ms: int) -> None:
        """(Re)arm the single-shot timer for the next silent check, replacing
        any previously scheduled one."""
        self._next_check_timer.start(delay_ms)

    def _on_no_update(self) -> None:
        """Handle "already up to date": keep the periodic chain alive and only
        notify the user after a manual check."""
        self._retry_index = 0
        # Always keep the periodic chain alive, also after a manual check (the
        # pending timer may have fired into the early-return of _begin_check
        # while this check was running).
        self._schedule_silent_check(_PERIODIC_INTERVAL_MS)
        if self._notify_when_current:
            QMessageBox.information(
                self._window, "Updates", "Je gebruikt al de nieuwste versie."
            )

    def _on_check_failed(self, message: str) -> None:
        """Handle a failed check: warn after a manual check, otherwise retry
        the silent check on the escalating delays before going periodic.

        @param message  Human-readable failure reason from the worker.
        """
        if self._notify_when_current:
            self._schedule_silent_check(_PERIODIC_INTERVAL_MS)
            QMessageBox.warning(
                self._window,
                "Updates",
                "Kon niet controleren op updates:\n" + message,
            )
            return
        if self._retry_index < len(_RETRY_DELAYS_MS):
            delay_ms = _RETRY_DELAYS_MS[self._retry_index]
            self._retry_index += 1
            LOGGER.info(
                "Silent update check failed, retrying in %ds (attempt %d/%d)",
                delay_ms // 1000,
                self._retry_index,
                len(_RETRY_DELAYS_MS),
            )
        else:
            delay_ms = _PERIODIC_INTERVAL_MS
            LOGGER.info("Silent update check kept failing; next try in %d min", delay_ms // 60000)
        self._schedule_silent_check(delay_ms)

    def _on_update_found(self, update_info: object) -> None:
        """Offer the found version to the user and start the download when
        accepted; remember a declined version so silent re-checks stay quiet.

        @param update_info  The velopack ``UpdateInfo`` describing the release.
        """
        self._retry_index = 0
        version = str(update_info.TargetFullRelease.Version)
        if not self._notify_when_current and version == self._declined_version:
            # Already declined this version this session; don't nag on the
            # periodic re-check. A newer version will prompt again.
            self._schedule_silent_check(_PERIODIC_INTERVAL_MS)
            return
        answer = QMessageBox.question(
            self._window,
            "Update beschikbaar",
            f"Versie {version} is beschikbaar.\n\nNu downloaden en installeren? "
            "De applicatie herstart automatisch zodra de update klaar is.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._declined_version = version
            self._schedule_silent_check(_PERIODIC_INTERVAL_MS)
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
        """Close the progress dialog and warn; the periodic re-check will
        offer the same version again later.

        @param message  Human-readable failure reason from the worker.
        """
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        # The version was not declined, so the periodic re-check will offer it
        # again — a transient download failure shouldn't end updates for the
        # whole session.
        self._schedule_silent_check(_PERIODIC_INTERVAL_MS)
        QMessageBox.warning(
            self._window,
            "Update mislukt",
            "De update kon niet worden gedownload:\n" + message,
        )

    def _on_download_finished(self, update_info: object) -> None:
        """Apply the downloaded update and restart the application.

        @param update_info  The velopack ``UpdateInfo`` that was downloaded.
        """
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
