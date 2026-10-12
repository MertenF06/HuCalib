"""Detect portable test packages without changing production release settings."""
from pathlib import Path
import sys


def is_test_build() -> bool:
    return bool(getattr(sys, 'frozen', False)
                and (Path(sys.executable).parent / 'TESTBUILD.json').is_file())
