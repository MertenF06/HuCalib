from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication
from mocap_app.ui.gaitdynamics_dialog import GaitDynamicsDialog
from mocap_app.ui.gaitdynamics_run_dialog import GaitDynamicsRunDialog, participant_values
from mocap_app.ui.gaitdynamics_setup_dialog import GaitDynamicsSetupDialog
from mocap_app.ui.analysis_dialog import MotionResultsDialog


class GaitDynamicsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_incompatible_pose2sim_input_can_be_reported_without_ai_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            motion, model = root / 'example.mot', root / 'model.osim'
            motion.write_text('nRows=2\nnColumns=2\ninDegrees=yes\nendheader\ntime knee_angle_r\n0 10\n.1 20\n')
            model.write_text('<OpenSimDocument><Model><JointSet/></Model></OpenSimDocument>')
            dialog = GaitDynamicsDialog(motion, model)
            self.addCleanup(dialog.deleteLater)
            dialog._check()
            self.assertFalse(dialog.report['compatible'])
            self.assertTrue(dialog.save_button.isEnabled())
            self.assertIn('nog niet geschikt', dialog.details.toPlainText())
            self.assertIn('lumbar', dialog.details.toPlainText())
            dialog.motion.setText(str(root / 'different.mot'))
            self.assertIsNone(dialog.report)
            self.assertFalse(dialog.save_button.isEnabled())

    def test_personal_values_are_explicit_accept_decimal_comma_and_reject_nonfinite(self):
        self.assertEqual(participant_values('1,75', '70', '0'), (1.75, 70, 0))
        for values in (('', '70', '0'), ('nan', '70', '0'), ('1.75', 'inf', '0'), ('1.75', '70', '-1')):
            with self.assertRaises(ValueError):
                participant_values(*values)
        dialog = GaitDynamicsRunDialog(Path('motion.mot'), Path('model.osim'))
        self.addCleanup(dialog.deleteLater)
        dialog._start()
        self.assertIsNone(dialog.task)
        self.assertIn('persoonsgegevens', dialog.details.toPlainText())
        self.assertFalse(dialog.view_button.isEnabled())

    def test_force_graph_shows_newtons_not_angles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'forces.mot'
            path.write_text('nRows=2\nnColumns=4\nendheader\ntime force1_vy force2_vy force1_py\n0 500 0 .1\n.01 510 5 .1\n')
            dialog = MotionResultsDialog(path, forces=True)
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog.coordinate.count(), 2)
            self.assertEqual(dialog.coordinate.currentData(), 'force1_vy')
            self.assertEqual(dialog.result.unit('force1_vy'), 'N')
            self.assertEqual(dialog.result.unit('force1_py'), 'meter')
            self.assertIn('rechts', dialog.coordinate.currentText())

    def test_busy_wizard_cannot_be_closed_with_escape(self):
        from unittest.mock import Mock
        dialog = GaitDynamicsSetupDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        dialog.task = Mock()
        dialog.task.isRunning.return_value = True
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        dialog.task.isRunning.return_value = False
        dialog.reject()
        self.assertFalse(dialog.isVisible())

    def test_install_error_details_do_not_replace_the_short_status_label(self):
        dialog = GaitDynamicsSetupDialog()
        self.addCleanup(dialog.deleteLater)
        message = 'Opdracht mislukt (4294967295): wsl.exe --install\n' + 'Helptekst\n' * 100
        dialog._result({'status': 'failed', 'message': message})
        self.assertLess(len(dialog.summary.text()), 120)
        self.assertIn('4294967295', dialog.details.toPlainText())


if __name__ == '__main__':
    unittest.main()
