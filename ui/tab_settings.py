"""Advanced settings tab - exposes calibration board geometry.

The board settings are pushed into the existing CalibrationManager so the
detection pipeline uses the same numbers as the operator picked here.
"""

from __future__ import annotations

from PySide6 import QtWidgets


class TabSettings:
    def __init__(self, logic_instance) -> None:
        self.logic = logic_instance
        self.window = logic_instance.window

    def setup(self) -> None:
        # Seed the form from the calibration manager if available.
        manager = getattr(self.logic, "calibration_manager", None)
        if manager is not None:
            settings = manager.board_settings()
            self.window.doubleSpinBox.setValue(settings.chessboard_square_size_m * 1000.0)
            self.window.spin_chess_cols.setValue(settings.chessboard_cols)
            self.window.spin_chess_rows.setValue(settings.chessboard_rows)
            self.window.spin_charuco_x.setValue(settings.charuco_squares_x)
            self.window.spin_charuco_y.setValue(settings.charuco_squares_y)
            self.window.spin_charuco_marker.setValue(settings.charuco_marker_size_m * 1000.0)
            self.window.spin_charuco_square.setValue(settings.charuco_square_size_m * 1000.0)

        self.window.btn_advanced_apply.clicked.connect(self.apply_to_board)

    def apply_to_board(self) -> None:
        manager = getattr(self.logic, "calibration_manager", None)
        if manager is None:
            QtWidgets.QMessageBox.warning(
                self.window,
                "Kalibratie",
                "De kalibratiemodule is nog niet geladen.",
            )
            return

        from mocap_app.models.types import CalibrationBoardSettings

        chessboard_square_mm = float(self.window.doubleSpinBox.value())
        try:
            new_settings = CalibrationBoardSettings(
                chessboard_cols=int(self.window.spin_chess_cols.value()),
                chessboard_rows=int(self.window.spin_chess_rows.value()),
                chessboard_square_size_m=chessboard_square_mm / 1000.0,
                charuco_squares_x=int(self.window.spin_charuco_x.value()),
                charuco_squares_y=int(self.window.spin_charuco_y.value()),
                charuco_square_size_m=float(self.window.spin_charuco_square.value()) / 1000.0,
                charuco_marker_size_m=float(self.window.spin_charuco_marker.value()) / 1000.0,
            )
        except (TypeError, ValueError) as exc:
            QtWidgets.QMessageBox.critical(self.window, "Ongeldige invoer", str(exc))
            return

        if new_settings.charuco_marker_size_m >= new_settings.charuco_square_size_m:
            QtWidgets.QMessageBox.warning(
                self.window,
                "Charuco-marker",
                "De Charuco-marker moet kleiner zijn dan de Charuco-square.",
            )
            return

        manager.apply_board_settings(new_settings)
        self.logic.log_to_console(
            f"Systeem: Kalibratiebord bijgewerkt "
            f"(chess {new_settings.chessboard_cols}x{new_settings.chessboard_rows} "
            f"@ {new_settings.chessboard_square_size_m * 1000:.1f} mm)."
        )
        QtWidgets.QMessageBox.information(
            self.window,
            "Toegepast",
            "Bordinstellingen toegepast op de kalibratiemodule.",
        )
