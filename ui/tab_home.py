"""Home tab - landing page with project actions."""

from __future__ import annotations


class TabHome:
    def __init__(self, logic_instance) -> None:
        self.logic = logic_instance
        self.window = logic_instance.window

    def setup(self) -> None:
        # Greeting follows the dynamic project state but stays useful on first run.
        self.window.label_main_text.setText("PhysioMotionTracker")
        self.window.label_home_subtitle.setText(
            "Start een nieuw kalibratieproject of laad een bestaand project om verder te gaan."
        )
