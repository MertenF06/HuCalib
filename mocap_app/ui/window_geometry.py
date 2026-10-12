"""Size windows in Qt logical pixels, respecting screen scaling and taskbars."""
from PySide6.QtCore import QSize


def fit_window_to_screen(window, preferred: QSize) -> None:
    screen = window.parentWidget().screen() if window.parentWidget() else window.screen()
    if screen is None:
        window.resize(preferred)
        return
    available = screen.availableGeometry().adjusted(24, 24, -24, -24)
    window.resize(preferred.boundedTo(available.size()))
    window.move(available.center() - window.rect().center())
