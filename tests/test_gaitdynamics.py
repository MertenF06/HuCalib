from pathlib import Path
import errno
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mocap_app.analysis.gaitdynamics import (COORDINATES, GaitDynamicsError,
    MODEL_HASHES, inspect_inputs, prepare_inference_motion, read_motion, run_analysis, sha256, verify_runtime)


class GaitDynamicsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.motion = self.root / 'motion.mot'
        self.model = self.root / 'model.osim'
        bodies = ('pelvis', 'femur_r', 'tibia_r', 'talus_r', 'calcn_r',
                  'femur_l', 'tibia_l', 'talus_l', 'calcn_l', 'torso')
        self.model.write_text('<OpenSimDocument><Model><BodySet><objects>' +
            ''.join(f'<Body name="{name}"/>' for name in bodies) + '</objects></BodySet><JointSet><objects><CustomJoint><coordinates>' +
            ''.join(f'<Coordinate name="{name}"/>' for name in COORDINATES) +
            '</coordinates></CustomJoint></objects></JointSet></Model></OpenSimDocument>')
        self.write_motion()

    def write_motion(self, names=COORDINATES, rows=200, irregular=False):
        self.motion.write_text(f'nRows={rows}\nnColumns={len(names)+1}\ninDegrees=yes\nendheader\n' +
            '\t'.join(('time', *names)) + '\n' + '\n'.join(
                '\t'.join((str(i * .01 + (.05 if irregular and i > 50 else 0)), *['0'] * len(names))) for i in range(rows)) + '\n')

    def test_full_inputs_are_checked_without_loading_ai_dependencies(self):
        report = inspect_inputs(self.motion, self.model)
        self.assertTrue(report['compatible'])
        self.assertAlmostEqual(report['sampling_rate'], 100)
        self.assertFalse(report['scientific_accuracy_validated'])

    def test_segmented_spine_is_not_silently_renamed(self):
        self.write_motion(names=COORDINATES[:20] + ('L5_S1_Flex_Ext', 'L5_S1_Lat_Bending', 'L5_S1_axial_rotation'))
        self.assertFalse(inspect_inputs(self.motion, self.model)['compatible'])
        before = self.motion.read_bytes()
        with self.assertRaisesRegex(GaitDynamicsError, 'lumbar'):
            run_analysis(self.motion, self.model, self.root / 'output', self.root / 'runtime', Path('python'), 1.75, 70, 0)
        self.assertEqual(self.motion.read_bytes(), before)
        self.assertFalse((self.root / 'output').exists())

    def test_pose2sim_model_with_additional_spine_coordinates_is_blocked(self):
        self.model.write_text(self.model.read_text().replace('</coordinates>', '<Coordinate name="L5_S1_Flex_Ext"/></coordinates>'))
        self.assertFalse(inspect_inputs(self.motion, self.model)['compatible'])

    def test_short_or_irregular_motion_is_blocked(self):
        self.write_motion(rows=100)
        self.assertFalse(inspect_inputs(self.motion, self.model)['compatible'])
        self.write_motion(irregular=True)
        self.assertFalse(inspect_inputs(self.motion, self.model)['compatible'])

    def test_unknown_units_nonfinite_and_incorrect_headers_are_rejected(self):
        self.motion.write_text(self.motion.read_text().replace('inDegrees=yes', 'inDegrees=unknown'))
        self.assertFalse(inspect_inputs(self.motion, self.model)['compatible'])
        self.write_motion()
        self.motion.write_text(self.motion.read_text().replace('nRows=200', 'nRows=1000'))
        with self.assertRaises(GaitDynamicsError):
            read_motion(self.motion)
        self.write_motion()
        self.motion.write_text(self.motion.read_text().replace('0.01', 'nan', 1))
        with self.assertRaises(GaitDynamicsError):
            read_motion(self.motion)

    def test_unverified_runtime_and_model_files_are_rejected(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'gait_dynamics.py').write_text('print("wrong code")')
        with self.assertRaises(GaitDynamicsError):
            verify_runtime(runtime)

    def test_anthropometrics_must_be_finite_and_explicit(self):
        with self.assertRaises(GaitDynamicsError):
            run_analysis(self.motion, self.model, self.root / 'output', self.root, Path('python'), 1.75, float('nan'), 0)

    def test_wsl_windows_files_can_complete_when_copying_metadata_is_forbidden(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        for name in MODEL_HASHES:
            (runtime / name).write_bytes(b'test model')
        reference = self.root / 'kinematics-reference.json'
        reference.write_text('{}')
        (self.root / 'conversion.json').write_text(json.dumps(dict(status='completed', warnings=[],
            artifacts={path.name: sha256(path) for path in (self.motion, self.model, reference)})))
        originals = {path: path.read_bytes() for path in (self.motion, self.model, reference)}
        output = self.root / 'output'
        columns = ['time'] + [f'{prefix}{side}_{axis}' for side in (1, 2)
            for prefix, axes in (('force', ('vx', 'vy', 'vz', 'px', 'py', 'pz')), ('torque', ('x', 'y', 'z')))
            for axis in axes]
        def worker(command, **options):
            # AI execution is stubbed; exercise actual input copies, reference
            # verification, output validation and the final result copy.
            for original in originals:
                self.assertEqual((output / 'inputs' / original.name).read_bytes(), originals[original])
            (output / 'inputs/motion_grf_pred___.mot').write_text('nRows=200\nnColumns=19\nendheader\n' +
                '\t'.join(columns) + '\n' + '\n'.join('\t'.join([str(i / 100)] + ['0'] * 18) for i in range(200)))
            (output / 'environment.json').write_text(json.dumps(dict(
                preprocessing=dict(output_frames=200, output_time_range_s=[0, 1.99]))))
            return subprocess.CompletedProcess(command, 0)
        with patch('mocap_app.analysis.gaitdynamics.verify_runtime'), \
                patch('shutil.copystat', side_effect=PermissionError(errno.EPERM, 'Operation not permitted')) as metadata, \
                patch('mocap_app.analysis.gaitdynamics.subprocess.run', side_effect=worker):
            result = run_analysis(self.motion, self.model, output, runtime, Path(sys.executable), 1.75, 70, 0)
        metadata.assert_not_called()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['sha256'], sha256(output / 'ground_reaction_forces.mot'))
        self.assertEqual(result['result']['rows'], 200)
        for original, data in originals.items():
            self.assertEqual(original.read_bytes(), data)

    def test_pre_worker_permission_error_records_traceback_without_reporting_success(self):
        output = self.root / 'output'
        error = PermissionError(errno.EPERM, 'Operation not permitted', str(output / 'inputs/motion.mot'))
        with patch('mocap_app.analysis.gaitdynamics.verify_runtime'), \
                patch('mocap_app.analysis.gaitdynamics.shutil.copyfile', side_effect=error), \
                patch('mocap_app.analysis.gaitdynamics.subprocess.run') as worker:
            with self.assertRaises(PermissionError):
                run_analysis(self.motion, self.model, output, self.root / 'runtime', Path(sys.executable), 1.75, 70, 0)
        worker.assert_not_called()
        report = json.loads((output / 'gaitdynamics.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertIn('PermissionError', report['traceback'])
        self.assertIn('copyfile', report['traceback'])
        self.assertIn('Operation not permitted', (output / 'gaitdynamics.log').read_text())
        self.assertFalse((output / 'ground_reaction_forces.mot').exists())

    def test_noninteger_camera_rate_uses_real_time_without_extrapolation_or_clock_drift(self):
        self.write_motion(rows=600)
        names, rows, _ = read_motion(self.motion)
        for i, row in enumerate(rows):
            row[0] = 2.25 + i / 59.94
            row[names.index('pelvis_tx')] = 3 * row[0]  # Known constant physical velocity.
        self.motion.write_text('nRows=600\nnColumns=24\ninDegrees=yes\nendheader\n' +
            '\t'.join(names) + '\n' + '\n'.join('\t'.join(str(v) for v in row) for row in rows))
        original = self.motion.read_bytes()
        target = self.root / 'inference.mot'
        report = prepare_inference_motion(self.motion, target)
        self.assertAlmostEqual(report['source_sampling_rate_hz'], 59.94)
        output_names, result, _ = read_motion(target)
        self.assertEqual(result[0][0], 2.25)
        self.assertLessEqual(result[-1][0], rows[-1][0])
        self.assertLess(rows[-1][0] - result[-1][0], .01)
        for row in result:
            self.assertAlmostEqual(row[output_names.index('pelvis_tx')], 3 * row[0], places=10)
        self.assertEqual(self.motion.read_bytes(), original)
        self.assertFalse(report['generated_missing_frames'])
        with self.assertRaises(FileExistsError):
            prepare_inference_motion(self.motion, target)

    def test_angular_wrap_does_not_create_a_spurious_half_turn_during_interpolation(self):
        self.write_motion(rows=160)
        text = self.motion.read_text().splitlines()
        start = text.index('endheader') + 2
        for i in range(160):
            row = text[start+i].split()
            angle = 179 if i <= 50 else -179
            row[1] = str(angle)
            text[start+i] = '\t'.join(row)
        self.motion.write_text('\n'.join(text))
        prepare_inference_motion(self.motion, self.root / 'unwrapped.mot')
        _, result, _ = read_motion(self.root / 'unwrapped.mot')
        self.assertEqual(result[50][1], 179)
        self.assertAlmostEqual(result[51][1], 181)


if __name__ == '__main__':
    unittest.main()
