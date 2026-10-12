from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from mocap_app.analysis.pose2sim import AnalysisError, STAGES, run_analysis, summarize_result, validate_session
from mocap_app.io.recording_session import RecordingSession
from mocap_app.models.types import CameraSourceConfig


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = self.root / 'session'
        self.session.mkdir()
        payload = {'cameras': {key: {'source_id': key, 'image_size': [64, 48],
            'intrinsics': [[60, 0, 32], [0, 60, 24], [0, 0, 1]],
            'distortion': [0, 0, 0, 0, 0], 'rotation': [0, 0, 0],
            'translation': [index, 0, 0]} for index, key in enumerate(('cam2', 'cam10'))}}
        recording = RecordingSession(self.session, 30, {},
            [CameraSourceConfig(key, 'video', key) for key in payload['cameras']], payload)
        for index, key in enumerate(payload['cameras']):
            path = self.session / f'cam{index + 1:02d}.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (64, 48))
            self.assertTrue(writer.isOpened())
            for _ in range(6):
                writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
            writer.release()
            recording.video_opened(key, path, (64, 48), 30)
            for batch in range(1, 7):
                recording.event({'camera_id': key, 'batch_index': batch,
                    'batch_id': f'batch_{batch}'}, 'written', video_frame_index=batch)
        recording.finish()
        self.config = self.root / 'Config.toml'
        self.config.write_text('[project]\n[pose]\n[synchronization]\n[filtering]\n[kinematics]\n')

    def change_manifest(self, change):
        path = self.session / 'session.json'
        manifest = json.loads(path.read_text())
        change(manifest)
        path.write_text(json.dumps(manifest))

    def change_rows(self, change):
        path = self.session / 'frames.jsonl'
        rows = [json.loads(row) for row in path.read_text().splitlines()]
        change(rows)
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')

    def test_valid_session_is_decoded_and_hashed(self):
        result = validate_session(self.session)
        self.assertEqual([v['camera_id'] for v in result['videos']], ['cam2', 'cam10'])
        self.assertEqual([v['decoded_frames'] for v in result['videos']], [6, 6])
        self.assertFalse(result['synchronization_verified'])

    def test_unfinished_recording_is_rejected(self):
        self.change_manifest(lambda m: m.update(status='recording'))
        with self.assertRaisesRegex(AnalysisError, 'afgeronde'):
            validate_session(self.session)

    def test_missing_frame_is_never_overridden(self):
        self.change_rows(lambda rows: rows[0].update(status='capture_failed', video_frame_index=None))
        with self.assertRaisesRegex(AnalysisError, 'Ontbrekende'):
            validate_session(self.session, diagnostic_resolution_mismatch=True)

    def test_duplicate_event_is_rejected(self):
        self.change_rows(lambda rows: rows.append(dict(rows[0])))
        with self.assertRaisesRegex(AnalysisError, 'dubbele'):
            validate_session(self.session)

    def test_camera_missing_from_batch_is_rejected(self):
        self.change_rows(lambda rows: rows.pop(0))
        with self.assertRaisesRegex(AnalysisError, 'volledige'):
            validate_session(self.session)

    def test_wrong_batch_video_index_is_rejected(self):
        self.change_rows(lambda rows: rows[0].update(video_frame_index=2))
        with self.assertRaisesRegex(AnalysisError, 'Videoframenummers'):
            validate_session(self.session)

    def test_calibration_tampering_is_rejected(self):
        with (self.session / 'calibration/calibration.toml').open('a') as stream:
            stream.write('\n# altered\n')
        with self.assertRaisesRegex(AnalysisError, 'gewijzigd'):
            validate_session(self.session)

    def test_outside_path_is_rejected(self):
        self.change_manifest(lambda m: m.update(frame_log='../Config.toml'))
        with self.assertRaisesRegex(AnalysisError, 'buiten'):
            validate_session(self.session)

    def test_actual_decoder_count_not_only_metadata(self):
        path = self.session / 'cam01.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (64, 48))
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
        writer.release()
        with self.assertRaisesRegex(AnalysisError, 'onvolledig'):
            validate_session(self.session)

    def test_failed_fps_correction_is_rejected(self):
        self.change_manifest(lambda m: m['cameras'][0].update(frame_rate_correction='failed'))
        with self.assertRaisesRegex(AnalysisError, 'FPS-correctie'):
            validate_session(self.session)

    def test_default_blocks_resolution_mismatch_diagnostic_records_warning(self):
        # Replace the encoded video and truthful manifest size, preserving the
        # original calibration. Merely changing metadata must not fool decoding.
        path = self.session / 'cam01.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (80, 48))
        for _ in range(6):
            writer.write(np.zeros((48, 80, 3), dtype=np.uint8))
        writer.release()
        self.change_manifest(lambda m: m['cameras'][0].update(image_size=[80, 48]))
        with self.assertRaisesRegex(AnalysisError, 'resoluties'):
            validate_session(self.session)
        self.assertEqual(validate_session(self.session, diagnostic_resolution_mismatch=True)['warnings'],
                         [{'camera_id': 'cam2', 'issue': 'resolution_mismatch'}])

    def test_failure_is_saved_and_stops_following_stages(self):
        output = self.root / 'analysis'
        with patch('mocap_app.analysis.pose2sim.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaisesRegex(AnalysisError, 'poseEstimation'):
                run_analysis(self.session, output, self.config, Path(sys.executable))
        self.assertEqual(run.call_count, 1)
        report = json.loads((output / 'analysis.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['stages'][0]['status'], 'failed')

    def test_timeout_is_saved(self):
        output = self.root / 'analysis'
        with patch('mocap_app.analysis.pose2sim.subprocess.run', side_effect=subprocess.TimeoutExpired('python', 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_analysis(self.session, output, self.config, Path(sys.executable), timeout=1)
        self.assertEqual(json.loads((output / 'analysis.json').read_text())['status'], 'failed')

    def test_virtual_environment_interpreter_symlink_is_preserved(self):
        selected = self.root / 'venv-python'
        try:
            selected.symlink_to(sys.executable)
        except OSError:
            self.skipTest('This platform/account does not permit interpreter symlinks')
        with patch('mocap_app.analysis.pose2sim.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaises(AnalysisError):
                run_analysis(self.session, self.root / 'analysis', self.config, selected)
        self.assertEqual(run.call_args.args[0][0], str(selected))

    def test_existing_output_and_session_contents_preserved(self):
        with self.assertRaises(AnalysisError):
            run_analysis(self.session, self.session / 'analysis', self.config, Path(sys.executable))
        with self.assertRaises(AnalysisError):
            run_analysis(self.session, self.session, self.config, Path(sys.executable))
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'keep.txt').write_text('keep')
        with self.assertRaises(FileExistsError):
            run_analysis(self.session, existing, self.config, Path(sys.executable))
        self.assertEqual((existing / 'keep.txt').read_text(), 'keep')

    def test_complete_run_is_isolated_and_maps_camera_names(self):
        output = self.root / 'analysis'
        # The official demo enables saved filter plots, which also require Qt.
        self.config.write_text(self.config.read_text().replace(
            '[filtering]', '[filtering]\ndisplay_figures = true\nsave_filt_plots = true'))
        original = (self.session / 'session.json').read_bytes()
        def fake_stage(*args, **kwargs):
            project = output / 'project'
            (project / 'result.trc').write_text('PathFileType\nNumFrames NumMarkers Units\n2 1 m\nFrame# Time Point\nX Y Z\n1 0.0 1 2 3\n2 0.1 2 3 4\n')
            (project / 'result.mot').write_text('nRows=2\nnColumns=2\ninDegrees=yes\nendheader\ntime angle\n0.0 10\n0.1 20\n')
            (output / 'environment.json').write_text('{"test_environment": true}')
            return subprocess.CompletedProcess(args[0], 0)
        with patch('mocap_app.analysis.pose2sim.subprocess.run', side_effect=fake_stage) as run:
            report = run_analysis(self.session, output, self.config, Path(sys.executable))
        self.assertEqual(run.call_count, len(STAGES))
        self.assertEqual(report['status'], 'completed')
        self.assertEqual((self.session / 'session.json').read_bytes(), original)
        self.assertEqual([m['pose2sim_name'] for m in report['camera_mapping']], ['cam01', 'cam02'])
        self.assertEqual([m['camera_id'] for m in report['camera_mapping']], ['cam2', 'cam10'])
        config = json.loads((output / 'effective-config.json').read_text())
        self.assertEqual(config['pose']['device'], 'cpu')
        self.assertFalse(config['filtering']['display_figures'])
        self.assertFalse(config['filtering']['save_filt_plots'])
        # Windows may expand an 8.3 temporary path (RUNNER~1) to its long name.
        # Verify the actual directory rather than its platform-specific spelling.
        configured_project = Path(config['project']['project_dir'])
        self.assertTrue(configured_project.is_absolute())
        self.assertTrue(configured_project.samefile(output / 'project'))

    def test_result_header_mismatch_is_explicit(self):
        path = self.root / 'result.trc'
        path.write_text('PathFileType\nNumFrames NumMarkers Units\n3 1 m\nFrame# Time Point\nX Y Z\n1 0.0 1 2 3\n2 0.1 2 3 4\n')
        self.assertFalse(summarize_result(path)['header_matches_data'])

    def test_nonfinite_result_is_rejected(self):
        path = self.root / 'result.mot'
        path.write_text('nRows=1\nnColumns=2\nendheader\ntime angle\n0.0 nan\n')
        with self.assertRaisesRegex(AnalysisError, 'Niet-eindige'):
            summarize_result(path)


if __name__ == '__main__':
    unittest.main()
