"""Stylesheet for the HuCalib UI.

The colour palette is built around the HU accent blue (#0078D4) used in
the navigation. The content surface stays light for readability, while
the navigation and console use a dark surface so the layout feels
balanced.
"""

from __future__ import annotations


STYLESHEET = """
QMainWindow, QWidget {
    background-color: #eef2f7;
    color: #1f2937;
    font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 11pt;
}

QMenuBar {
    background-color: #1f2937;
    color: #e5e7eb;
    padding: 4px 6px;
    border: none;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    margin: 0 2px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background: #374151;
}
QMenu {
    background-color: #1f2937;
    color: #e5e7eb;
    border: 1px solid #374151;
    padding: 4px;
}
QMenu::item {
    padding: 6px 22px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #0078D4;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #374151;
    margin: 4px 6px;
}

QStatusBar {
    background-color: #e5e7eb;
    color: #1f2937;
    border-top: 1px solid #cbd5e1;
}

QSplitter::handle {
    background-color: #cbd5e1;
}
QSplitter::handle:hover {
    background-color: #0078D4;
}
QSplitter::handle:horizontal {
    width: 8px;
}
QSplitter::handle:vertical {
    height: 8px;
}

/* Navigation rail */
QFrame#frame_menu {
    background-color: #111827;
    border-right: 1px solid #1f2937;
}
QFrame#frame_menu QLabel {
    color: #e5e7eb;
    background: transparent;
}
QPushButton[nav="true"] {
    background-color: #1e293b;
    color: #d1d5db;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 8px 12px;
    text-align: center;
    font-weight: 600;
}
QPushButton[nav="true"]:hover {
    background-color: #334155;
    color: #ffffff;
    border-color: #475569;
}
QPushButton[nav="true"]:checked,
QPushButton[nav="true"][active="true"] {
    background-color: #0078D4;
    color: #ffffff;
    border-color: #005A9E;
}
QPushButton[nav="true"]:disabled {
    color: #6b7280;
}

/* Content cards */
QFrame[card="true"] {
    background-color: #ffffff;
    border: 1px solid #d4dde8;
    border-radius: 8px;
}
QFrame[value="true"] {
    background-color: #f8fafc;
    border: 1px solid #d4dde8;
    border-radius: 6px;
}

QLabel[display="true"] {
    color: #0f172a;
    font-size: 26pt;
    font-weight: 700;
}
QLabel[subtitle="true"] {
    color: #475569;
    font-size: 12pt;
}
QLabel[section="true"] {
    color: #1f2937;
    font-size: 11pt;
    font-weight: 700;
    background: transparent;
}
QLabel[hint="true"] {
    color: #6b7280;
    font-size: 9pt;
}

/* Buttons in content area */
QPushButton {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 26px;
}
QPushButton:hover {
    border-color: #94a3b8;
    background-color: #f1f5f9;
}
QPushButton:pressed {
    background-color: #e2e8f0;
}
QPushButton:disabled {
    background-color: #f1f5f9;
    color: #9ca3af;
    border-color: #e2e8f0;
}
QPushButton[accent="true"] {
    background-color: #0078D4;
    color: #ffffff;
    border: 1px solid #005A9E;
    font-weight: 600;
}
QPushButton[accent="true"]:hover {
    background-color: #1487da;
}
QPushButton[accent="true"]:pressed {
    background-color: #006bbf;
}
QPushButton[accent="true"]:disabled {
    background-color: #93c5fd;
    border-color: #93c5fd;
}
QPushButton[danger="true"] {
    background-color: #ef4444;
    color: #ffffff;
    border: 1px solid #b91c1c;
    font-weight: 600;
}
QPushButton[danger="true"]:hover {
    background-color: #f05a5a;
}
QPushButton[danger="true"]:pressed {
    background-color: #d63a3a;
}
QPushButton:checked {
    background-color: #0078D4;
    color: #ffffff;
    border-color: #005A9E;
}

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: 4px 8px;
    selection-background-color: #0078D4;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #0078D4;
}
QComboBox::drop-down {
    width: 22px;
    border-left: 1px solid #cbd5e1;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1f2937;
    selection-background-color: #0078D4;
    selection-color: #ffffff;
    border: 1px solid #cbd5e1;
}

/* Console */
QFrame#frame_console {
    background-color: #0f172a;
    border-top: 1px solid #1f2937;
}
QFrame#frame_console QLabel {
    color: #e5e7eb;
    background: transparent;
}
QPlainTextEdit#plaintextedit_console {
    background-color: #0b1220;
    color: #d1fae5;
    border: 1px solid #1f2937;
    border-radius: 6px;
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 10pt;
    padding: 8px 10px;
}
QLineEdit#lineedit_console_input {
    background-color: #111827;
    color: #f9fafb;
    border: 1px solid #1f2937;
    border-radius: 6px;
    padding: 6px 10px;
    font-family: "Consolas", "JetBrains Mono", monospace;
}
QLineEdit#lineedit_console_input:focus {
    border-color: #0078D4;
}

/* Trees / lists */
QTreeWidget, QTreeView, QListWidget, QTableWidget {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #d4dde8;
    selection-background-color: #cfe5fb;
    selection-color: #0f172a;
}
QHeaderView::section {
    background-color: #eef2f7;
    color: #1f2937;
    border: 1px solid #d4dde8;
    padding: 6px 8px;
    font-weight: 600;
}

/* Progress bars */
QProgressBar {
    background-color: #e2e8f0;
    color: #1f2937;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    min-height: 18px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #0078D4;
    border-radius: 4px;
}

/* Scrollbars: keep slim and unobtrusive */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #cbd5e1;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #94a3b8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    background: transparent;
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #cbd5e1;
    border-radius: 5px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover {
    background: #94a3b8;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    background: transparent;
    width: 0;
}

/* The camera page wrapper is just a container: the individual camera tiles
   are the visible cards, so keep this transparent instead of a full-width
   white card. */
QFrame#frame_cam,
QFrame#frame_directory,
QFrame#frame_diagnostics,
QFrame#frame_results_grid {
    background-color: transparent;
    border: none;
}

/* Compact buttons for the small camera-card control row */
QPushButton[compact="true"] {
    padding: 2px 7px;
    min-height: 22px;
    font-size: 9pt;
}

/* Camera tile internals (set by tab_cameras) */
QFrame[camera-tile="true"] {
    background-color: #ffffff;
    border: 1px solid #d4dde8;
    border-radius: 8px;
}
QLabel[camera-video="true"] {
    background-color: #0b1220;
    color: #f1f5f9;
    border-radius: 6px;
}
"""


def stylesheet_for_scale(scale: float) -> str:
    scale = max(0.30, min(1.6, float(scale)))
    font_pt = 11.0 * scale
    console_pt = 10.0 * scale
    display_pt = 26.0 * scale
    subtitle_pt = 12.0 * scale
    hint_pt = 9.0 * scale
    button_height = int(round(26 * scale))
    nav_padding_y = int(round(8 * scale))
    nav_padding_x = int(round(12 * scale))
    button_padding_y = int(round(6 * scale))
    button_padding_x = int(round(12 * scale))
    progress_height = int(round(18 * scale))
    return (
        STYLESHEET
        + f"""

/* Runtime UI scale overrides */
QMainWindow, QWidget {{
    font-size: {font_pt:.2f}pt;
}}
QLabel[display="true"] {{
    font-size: {display_pt:.2f}pt;
}}
QLabel[subtitle="true"] {{
    font-size: {subtitle_pt:.2f}pt;
}}
QLabel[hint="true"] {{
    font-size: {hint_pt:.2f}pt;
}}
QPushButton {{
    min-height: {button_height}px;
    padding: {button_padding_y}px {button_padding_x}px;
}}
QPushButton[nav="true"] {{
    padding: {nav_padding_y}px {nav_padding_x}px;
}}
QPlainTextEdit#plaintextedit_console,
QLineEdit#lineedit_console_input {{
    font-size: {console_pt:.2f}pt;
}}
QProgressBar {{
    min-height: {progress_height}px;
}}
"""
    )


class apply_styles:
    """Compatibility shim - mirrors the team's original API."""

    def __init__(self, window, scale: float = 1.0) -> None:
        self.window = window
        self.scale = scale
        self.set_custom_style()

    def set_custom_style(self) -> None:
        self.window.setStyleSheet(stylesheet_for_scale(self.scale))
