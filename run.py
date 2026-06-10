import sys


def _velopack_startup() -> None:
    """Run Velopack's startup hooks before anything else.

    When the app is launched by the installer/updater it is passed special
    arguments (install, update, uninstall, first-run). Velopack handles those
    and exits the process, so this must run *before* the heavy PySide6/OpenCV
    imports below to keep install/update fast. In a dev checkout (not frozen)
    there are no hooks, so we skip it entirely.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import velopack

        velopack.App().run()
    except Exception:
        # Never let an updater hiccup stop the app from starting.
        pass


_velopack_startup()

from mocap_app.main import run


if __name__ == "__main__":
    raise SystemExit(run())
