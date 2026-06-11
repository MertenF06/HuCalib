import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow

from mocap_app import __version__
from mocap_app.ui.designed_main_window import (
    DesignedCalibrationPanel,
    _application_info_text,
)
from mocap_app.ui.gui import Ui_MainWindow


class InfoDialogTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_help_menu_contains_info_action(self) -> None:
        window = QMainWindow()
        ui = Ui_MainWindow()
        ui.setupUi(window)

        self.assertEqual(ui.actionInfo.text(), "Info")
        self.assertIn(ui.actionInfo, ui.menuHelp.actions())

    def test_info_text_contains_version_credits_and_client(self) -> None:
        text = _application_info_text()

        self.assertIn(f"Versie {__version__}", text)
        self.assertIn("Merten Flantua", text)
        self.assertIn("Huibert Verploeg", text)
        self.assertIn("Max Untersalmberge", text)
        self.assertIn("Melle Poeckling", text)
        self.assertIn("Daniël Wit Ariza", text)
        self.assertIn("Jan Piccardt Brouwer", text)
        self.assertIn("Hogeschool Utrecht - Quest project 2026", text)

    def test_show_info_opens_information_dialog(self) -> None:
        window = object()
        panel = type("PanelStub", (), {"window": window})()

        with patch(
            "mocap_app.ui.designed_main_window.QMessageBox.information"
        ) as show_information:
            DesignedCalibrationPanel._show_info(panel)

        show_information.assert_called_once_with(
            window,
            "Info over HuCalib",
            _application_info_text(),
        )
