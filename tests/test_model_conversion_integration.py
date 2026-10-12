"""Real OpenSim FK tests; run in Pose2Sim's separate scientific environment."""
import importlib.util
import math
import os
from pathlib import Path
import tempfile
import time
import unittest

from mocap_app.analysis.gaitdynamics import COORDINATES, GaitDynamicsError, read_motion, sha256
from mocap_app.analysis.model_conversion import convert_model


@unittest.skipUnless(importlib.util.find_spec('opensim'), 'Vereist de afzonderlijke Pose2Sim/OpenSim-omgeving')
class ModelConversionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import opensim
        cls.opensim = opensim
        if os.environ.get('POSE2SIM_TEST_MODEL'):
            cls.source_model = Path(os.environ['POSE2SIM_TEST_MODEL'])
        else:
            spec = importlib.util.find_spec('Pose2Sim')
            if spec is None:
                raise unittest.SkipTest('Vereist het officiële Pose2Sim-model')
            cls.source_model = Path(spec.origin).parent / 'OpenSim_Setup/Model_Pose2Sim_muscles_flex.osim'
        model = opensim.Model(str(cls.source_model))
        model.initSystem()
        cls.names = [model.getCoordinateSet().get(i).getName() for i in range(model.getCoordinateSet().getSize())]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.motion = self.root / 'source.mot'
        self.write_motion()

    def write_motion(self, degrees=True, large_spine=False, gap=False, missing=False):
        names = self.names[:-1] if missing else self.names
        rows = []
        for i in range(160):
            values = {name: 0. for name in names}
            values.update(pelvis_tilt=4, pelvis_rotation=-6, pelvis_ty=1,
                hip_flexion_r=15*math.sin(i*.03), hip_adduction_l=3,
                knee_angle_r=30+20*math.sin(i*.03), knee_angle_l=25,
                ankle_angle_r=10*math.cos(i*.03), ankle_angle_l=-8)
            for name in names:
                if 'Flex_Ext' in name:
                    values[name] = 15 if large_spine else 2*math.sin(i*.03)
                if 'Lat_Bending' in name:
                    values[name] = 1*math.cos(i*.03)
                if 'axial_rotation' in name:
                    values[name] = -.8*math.sin(i*.03)
            if not degrees:
                translations = {'pelvis_tx', 'pelvis_ty', 'pelvis_tz', 'Abs_t1', 'Abs_t2'}
                values = {name: value if name in translations
                          else value*math.pi/180 for name, value in values.items()}
            rows.append([i*.01 + (.05 if gap and i > 60 else 0)] + [values[name] for name in names])
        self.motion.write_text(f'nRows=160\nnColumns={len(names)+1}\ninDegrees={"yes" if degrees else "no"}\nendheader\n' +
            '\t'.join(('time', *names)) + '\n' + '\n'.join('\t'.join(format(v, '.17g') for v in row) for row in rows) + '\n')

    def test_noncommuting_spine_and_oblique_ankles_preserve_lower_body_and_rotations(self):
        original = sha256(self.motion), sha256(self.source_model)
        result = convert_model(self.motion, self.source_model, self.root / 'converted')
        self.assertEqual(result['status'], 'completed')
        checked = result['validation']
        self.assertLess(checked['max_lower_body_position_error_m'], 1e-12)
        self.assertLess(checked['max_orientation_error_rad'], 1e-12)
        self.assertLess(checked['max_trunk_position_error_m'], .03)
        self.assertAlmostEqual(checked['mass_kg'], checked['target_mass_kg'], places=10)
        names, rows, header = read_motion(self.root / 'converted/motion.mot')
        self.assertEqual(names, ['time', *COORDINATES])
        self.assertEqual([row[0] for row in rows], [row[0] for row in read_motion(self.motion)[1]])
        self.assertEqual(header['inDegrees'], 'yes')
        self.assertEqual(original, (sha256(self.motion), sha256(self.source_model)))
        # Whole-body neutral COM is preserved by the mass aggregation.
        models = [self.opensim.Model(str(p)) for p in (self.source_model, self.root / 'converted/model.osim')]
        centres = []
        for model in models:
            state = model.initSystem()
            for i in range(model.getCoordinateSet().getSize()):
                model.getCoordinateSet().get(i).setValue(state, 0., False)
            model.realizePosition(state)
            centres.append([model.calcMassCenterPosition(state).get(i) for i in range(3)])
        for a, b in zip(*centres):
            self.assertAlmostEqual(a, b, places=12)
        with self.assertRaises(FileExistsError):
            convert_model(self.motion, self.source_model, self.root / 'converted')

    def test_radians_produce_the_same_degree_output(self):
        convert_model(self.motion, self.source_model, self.root / 'degrees')
        self.write_motion(degrees=False)
        convert_model(self.motion, self.source_model, self.root / 'radians')
        a = read_motion(self.root / 'degrees/motion.mot')[1]
        b = read_motion(self.root / 'radians/motion.mot')[1]
        for row_a, row_b in zip(a, b):
            for value_a, value_b in zip(row_a, row_b):
                self.assertAlmostEqual(value_a, value_b, places=10)

    def test_large_reduction_error_gaps_and_missing_coordinates_are_rejected(self):
        for case, options in (('spine', {'large_spine': True}), ('gap', {'gap': True}), ('missing', {'missing': True})):
            with self.subTest(case=case):
                self.write_motion(**options)
                with self.assertRaises(GaitDynamicsError):
                    convert_model(self.motion, self.source_model, self.root / case)

    @unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Vereist Qt voor de echte interfaceproef')
    def test_interface_runs_conversion_in_separate_process_and_loads_new_inputs(self):
        import sys
        from PySide6.QtCore import QProcess, QTimer
        from PySide6.QtWidgets import QApplication
        from mocap_app.ui.gaitdynamics_dialog import GaitDynamicsDialog
        app = QApplication.instance() or QApplication([])
        dialog = GaitDynamicsDialog(self.motion, self.source_model, python=Path(sys.executable))
        self.addCleanup(dialog.deleteLater)
        heartbeat = []
        timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: heartbeat.append(1)); timer.start()
        dialog.show()
        output = self.root / 'interface-conversion'
        dialog._start_conversion(output)
        self.assertFalse(dialog.check_button.isEnabled())
        self.assertTrue(all(not button.isEnabled() for button in dialog._file_buttons))
        dialog.close()
        self.assertTrue(dialog.isVisible(), 'Een lopende conversie moet bij sluiten blijven bestaan.')
        deadline = time.monotonic() + 30
        while dialog.process.state() != QProcess.ProcessState.NotRunning and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        timer.stop()
        if dialog.process.state() != QProcess.ProcessState.NotRunning:
            dialog.process.kill(); dialog.process.waitForFinished(5000)
            self.fail('Modelconversie liep vast.')
        app.processEvents()
        self.assertIsNotNone(dialog.report, dialog.details.toPlainText())
        self.assertTrue(dialog.report['compatible'], dialog.details.toPlainText())
        self.assertEqual(dialog.motion.text(), str(output / 'motion.mot'))
        self.assertEqual(dialog.model.text(), str(output / 'model.osim'))
        self.assertIn('Modelconversie gereed', dialog.details.toPlainText())
        self.assertGreater(len(heartbeat), 5, 'De interface bleef onvoldoende responsief.')
        self.assertTrue((output / 'conversion.log').is_file())
        dialog.close()


if __name__ == '__main__':
    unittest.main()
