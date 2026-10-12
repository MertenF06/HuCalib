from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from mocap_app.analysis.results import MotionResult, coordinate_label
from mocap_app.ui.analysis_dialog import AnalysisDialog, MotionResultsDialog


class AnalysisUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dialog = AnalysisDialog(self.root / 'settings.ini', None, self.root / 'results')
        self.addCleanup(self.dialog.deleteLater)

    def fake_valid_check(self):
        self.dialog.session.setText(str(self.root / 'session'))
        self.dialog.python.setText(str(self.root / 'python'))
        self.dialog.config.setText(str(self.root / 'Config.toml'))
        self.dialog.phase = 'checking'
        self.dialog._buffer = '{"videos": [{"decoded_frames": 100}], "frame_rate": 60, "warnings": []}'
        self.dialog._process_finished(0, QProcess.ExitStatus.NormalExit)

    def test_start_requires_check_and_changes_invalidate_it(self):
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.fake_valid_check()
        self.assertTrue(self.dialog.start_button.isEnabled())
        self.dialog.session.setText(str(self.root / 'different-session'))
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertFalse(self.dialog.checked)

    def test_check_error_cannot_enable_analysis(self):
        self.dialog.phase = 'checking'
        self.dialog._buffer = 'Ontbrekende frames'
        self.dialog._process_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertIn('Ontbrekende frames', self.dialog.messages.toPlainText())

    def test_diagnostic_option_requires_new_check(self):
        self.fake_valid_check()
        self.dialog.diagnostic.setChecked(True)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertIn('--diagnostic-resolution-mismatch', self.dialog._arguments())

    def test_busy_dialog_refuses_close(self):
        self.dialog.phase = 'running'
        event = QCloseEvent()
        self.dialog.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.dialog.phase = 'idle'

    def test_active_model_conversion_keeps_parent_open_and_prevents_duplicate_worker(self):
        from mocap_app.ui.gaitdynamics_dialog import GaitDynamicsDialog
        gait = GaitDynamicsDialog(None, None, self.dialog)
        self.dialog._gaitdynamics_dialog = gait
        gait.process.start(sys.executable, ['-c', 'import time; time.sleep(10)'])
        try:
            self.assertTrue(gait.process.waitForStarted(5000))
            self.assertTrue(self.dialog.busy)
            event = QCloseEvent()
            self.dialog.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.dialog.report = {'status': 'completed'}
            self.dialog.output_dir = self.root
            self.dialog._open_gaitdynamics()
            self.assertIs(self.dialog._gaitdynamics_dialog, gait)
        finally:
            gait.process.kill()
            gait.process.waitForFinished(5000)
        self.assertFalse(self.dialog.busy)

    def test_small_windows_keep_all_actions_visible_while_content_scrolls(self):
        self.dialog.show()
        self.addCleanup(self.dialog.close)
        for point_size in (10, 15):
            font = self.dialog.font()
            font.setPointSize(point_size)
            self.dialog.setFont(font)
            for width, height in ((640, 480), (864, 500), (1024, 640)):
                with self.subTest(font=point_size, size=(width, height)):
                    self.dialog.resize(width, height)
                    self.app.processEvents()
                    self.app.processEvents()
                    self.assertLessEqual(self.dialog.width(), width)
                    self.assertLessEqual(self.dialog.height(), height)
                    for button in self.dialog._action_buttons:
                        position = button.mapTo(self.dialog, QPoint(0, 0))
                        self.assertTrue(self.dialog.rect().contains(position))
                        self.assertTrue(self.dialog.rect().contains(position + button.rect().bottomRight()))
                    scroll = self.dialog.content_scroll
                    self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                    scroll.ensureWidgetVisible(self.dialog.config)
                    self.app.processEvents()
                    position = self.dialog.config.mapTo(scroll.viewport(), QPoint(0, 0))
                    self.assertTrue(scroll.viewport().rect().contains(position))
                    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                    self.app.processEvents()
                    # Scrolling inputs/logs must never move the action buttons.
                    self.assertTrue(self.dialog.close_button.isVisible())

    def test_initial_dialog_fits_available_screen(self):
        self.dialog.show()
        self.app.processEvents()
        self.addCleanup(self.dialog.close)
        self.assertTrue(self.dialog.screen().availableGeometry().contains(self.dialog.frameGeometry()))

    def test_failed_python_start_unlocks_controls(self):
        self.dialog.phase = 'checking'
        self.dialog._process_error(QProcess.ProcessError.FailedToStart)
        self.assertFalse(self.dialog.busy)
        self.assertTrue(self.dialog.close_button.isEnabled())
        self.assertFalse(self.dialog.start_button.isEnabled())

    def test_output_inside_source_is_rejected_before_process(self):
        self.dialog.session.setText(str(self.root / 'session'))
        self.dialog.output_base.setText(str(self.root / 'session' / 'results'))
        with patch.object(self.dialog, '_launch') as launch:
            self.dialog._check()
        launch.assert_not_called()
        self.assertIn('buiten', self.dialog.status.text())

    def test_all_motion_coordinates_have_selectable_data_and_correct_units(self):
        path = self.root / 'example.mot'
        path.write_text('nRows=2\nnColumns=4\ninDegrees=yes\nendheader\ntime pelvis_tx knee_angle_r wrist_flex_l\n0 1 20 10\n.1 2 25 15\n')
        result = MotionResult.read(path)
        self.assertEqual(result.unit('pelvis_tx'), 'meter')
        self.assertEqual(result.unit('knee_angle_r'), 'graden')
        view = MotionResultsDialog(path)
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.coordinate.count(), 3)
        self.assertEqual(view.coordinate.currentData(), 'knee_angle_r')
        self.assertEqual(coordinate_label('wrist_flex_l'), 'Polsflexie links')
        self.assertEqual(coordinate_label('L5_S1_Flex_Ext'), 'Rug L5–S1: buiging')
        for index in range(view.coordinate.count()):
            view.coordinate.setCurrentIndex(index)
            self.assertEqual(view.plot.name, result.names[index])
        view.show()
        self.addCleanup(view.close)
        view.resize(640, 480)
        self.app.processEvents()
        self.assertLessEqual(view.width(), 640)
        self.assertLessEqual(view.height(), 480)
        self.assertGreater(view.plot.height(), 150)

    def test_successful_analysis_makes_graphs_available(self):
        import json
        self.dialog.output_dir = self.root / 'analysis'
        self.dialog.output_dir.mkdir()
        report = {'status': 'completed', 'stages': [], 'warnings': [],
                  'result_summary': [{'coordinates': 62, 'rows': 96}]}
        (self.dialog.output_dir / 'analysis.json').write_text(json.dumps(report))
        self.dialog.phase = 'running'
        self.dialog._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertTrue(self.dialog.folder_button.isEnabled())
        self.assertTrue(self.dialog.graph_button.isEnabled())
        self.assertTrue(self.dialog.gait_button.isEnabled())
        self.assertIn('62 modelcoördinaten', self.dialog.summary.text())


if __name__ == '__main__':
    unittest.main()
