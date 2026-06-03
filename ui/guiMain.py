"""Standalone entry point for the designed PhysioMotionTracker UI.

For the normal workflow run ``python run.py`` from the project root. This
script remains so the UI team can launch the designed window directly:

    python -m ui.guiMain
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # Allow ``python ui/guiMain.py`` from any cwd by ensuring the project
    # root is on sys.path before importing the backend modules.
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from mocap_app.main import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
