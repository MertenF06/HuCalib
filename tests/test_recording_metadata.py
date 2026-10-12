from __future__ import annotations

import hashlib
import json
import queue
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from mocap_app.io.video_recorder import VideoRecorder
from mocap_app.models.types import CameraSourceConfig, FramePacket
from mocap_app.workers.capture_worker import LiveCaptureWorker


class FakeWriter:
    def isOpened(self):
        return True

    def write(self, frame):
        pass

    def release(self):
        pass


def source(camera_id):
    return CameraSourceConfig(camera_id, 'webcam', 0, 'Dezelfde naam')


def calibration(ids):
    return {'metadata': {'extrinsics_reference_source_id': ids[0]}, 'cameras': {
        key: {'source_id': key, 'image_size': [32, 24],
              'intrinsics': [[100., 0., 16.], [0., 100., 12.], [0., 0., 1.]],
              'distortion': [0., 0., 0., 0.], 'rotation': [0., 0., 0.],
              'translation': [float(index), 0., 0.]}
        for index, key in enumerate(ids)}}


class RecordingMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'opname'
        self.image = np.zeros((24, 32, 3), dtype=np.uint8)
        self.writer_patch = patch('mocap_app.io.video_recorder.cv2.VideoWriter', side_effect=lambda *args: FakeWriter())
        self.writer_patch.start()
        self.addCleanup(self.writer_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def read(self):
        manifest = json.loads((self.root / 'session.json').read_text())
        rows = [json.loads(line) for line in (self.root / 'frames.jsonl').read_text().splitlines()]
        return manifest, rows

    def test_camera_order_snapshot_and_rename_are_independent_of_labels(self):
        ids = ['cam10', 'cam2', 'cam0']
        payload = calibration(ids)
        recorder = VideoRecorder(self.root, 25, labels={key: 'CON / dezelfde?' for key in ids},
                                 sources=[source(key) for key in ids], calibration_payload=payload)
        payload['cameras']['cam0']['translation'][0] = 999.
        recorder.write_frames({key: self.image for key in reversed(ids)})
        written = recorder.close()
        self.assertEqual(written, recorder.close())
        manifest, rows = self.read()
        self.assertEqual([c['camera_id'] for c in manifest['cameras']], ['cam0', 'cam2', 'cam10'])
        self.assertEqual([c['video_file'] for c in manifest['cameras']], ['cam01.mp4', 'cam02.mp4', 'cam03.mp4'])
        import tomllib
        tables = tomllib.loads((self.root / 'calibration/calibration.toml').read_text())
        for index, camera in enumerate(manifest['cameras']):
            self.assertEqual(camera['calibration_table'], f'cam_{index}')
            self.assertEqual(tables[f'cam_{index}']['name'], camera['camera_id'])
            self.assertEqual(camera['calibration_match'], 'matching_resolution')
        profile = (self.root / 'calibration/calibration.json').read_bytes()
        self.assertEqual(hashlib.sha256(profile).hexdigest(), manifest['calibration']['profile_sha256'])
        toml_bytes = (self.root / 'calibration/calibration.toml').read_bytes()
        self.assertEqual(hashlib.sha256(toml_bytes).hexdigest(), manifest['calibration']['toml_sha256'])
        self.assertNotEqual(json.loads(profile)['cameras']['cam0']['translation'][0], 999.)
        self.assertEqual(len(rows), 3)
        from mocap_app.io.calibration_export import build_motion_capture_payload
        standalone, _ = build_motion_capture_payload(calibration(ids))
        self.assertEqual([cam['name'] for key, cam in standalone.items() if key != 'metadata'],
                         ['cam0', 'cam2', 'cam10'])
        renamed = self.root.rename(self.root.with_name('nieuwe naam'))
        self.assertTrue((renamed / manifest['calibration']['toml_file']).is_file())
        self.assertTrue((renamed / manifest['frame_log']).is_file())

    def test_capture_and_queue_failures_have_one_terminal_row_per_camera(self):
        recorder = VideoRecorder(self.root, 25, sources=[source('cam0'), source('cam1')])
        with patch.object(recorder._write_queue, 'put_nowait', side_effect=queue.Full):
            recorder.write_frames({'cam0': self.image}, failures={'cam1': {'reason': 'read_failed'}}, batch_id='round1')
        recorder.write_frames({}, failures={'cam0': {'reason': 'read_failed'}, 'cam1': {'reason': 'retrieve_failed'}}, batch_id='round2')
        self.assertEqual(recorder.close(), {})
        manifest, rows = self.read()
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({(r['camera_id'], r['batch_index']) for r in rows}), 4)
        self.assertEqual([r['status'] for r in rows].count('queue_dropped'), 1)
        self.assertEqual([r['status'] for r in rows].count('capture_failed'), 3)
        self.assertTrue(all(r['video_frame_index'] is None for r in rows))
        self.assertEqual(recorder.dropped_frames(), 4)
        self.assertEqual(manifest['status'], 'closed')

    def test_encoder_failure_does_not_shift_logged_video_indices(self):
        class FailingFirstWriter(FakeWriter):
            def __init__(self):
                self.calls = 0
            def write(self, frame):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError('codec failed')
        with patch('mocap_app.io.video_recorder.cv2.VideoWriter', return_value=FailingFirstWriter()):
            recorder = VideoRecorder(self.root, 25, sources=[source('cam0')])
            recorder.write_frames({'cam0': self.image}, batch_id='one')
            recorder.write_frames({'cam0': self.image}, batch_id='two')
            recorder.close()
        manifest, rows = self.read()
        self.assertEqual([r['status'] for r in rows], ['encode_failed', 'written'])
        self.assertEqual([r['video_frame_index'] for r in rows], [None, 1])
        self.assertEqual(manifest['cameras'][0]['counts']['written'], 1)
        self.assertEqual(recorder.dropped_frames(), 1)

    def test_wall_clock_changes_do_not_change_monotonic_read_times(self):
        recorder = VideoRecorder(self.root, 25, sources=[source('cam0')])
        for index, wall in enumerate([2000., 1000.], 1):
            packet = FramePacket('cam0', index, wall, self.image,
                                 capture_started_monotonic_ns=index * 40_000_000,
                                 capture_completed_monotonic_ns=index * 40_000_000 + 2_000_000)
            recorder.write_frames({'cam0': self.image}, packets={'cam0': packet})
        recorder.close()
        _, rows = self.read()
        self.assertEqual([r['source_frame_index'] for r in rows], [1, 2])
        self.assertEqual([r['video_frame_index'] for r in rows], [1, 2])
        self.assertEqual(rows[1]['timestamp_monotonic_ns'] - rows[0]['timestamp_monotonic_ns'], 40_000_000)
        self.assertEqual([r['timestamp_unix_sec'] for r in rows], [2000., 1000.])
        self.assertTrue(all(r['timestamp_kind'] == 'host_read_midpoint' for r in rows))

    def test_resolution_changes_and_missing_calibration_are_explicit(self):
        recorder = VideoRecorder(self.root, 25, sources=[source('cam0'), source('cam1')],
                                 calibration_payload=calibration(['cam0']))
        recorder.write_frames({'cam0': self.image, 'cam1': self.image})
        recorder.write_frames({'cam0': np.zeros((48, 64, 3), np.uint8), 'cam1': self.image})
        recorder.close()
        manifest, rows = self.read()
        self.assertEqual(sum(r['resized'] for r in rows), 1)
        self.assertIn({'camera_id': 'cam0', 'issue': 'frames_resized'}, manifest['analysis_issues'])
        self.assertIn({'camera_id': 'cam1', 'issue': 'calibration_missing'}, manifest['analysis_issues'])
        self.assertIsNone(manifest['cameras'][1]['calibration_table'])

    def test_existing_recording_is_never_overwritten(self):
        self.root.mkdir()
        existing = self.root / 'cam01.mp4'
        existing.write_bytes(b'user data')
        with self.assertRaises(FileExistsError):
            VideoRecorder(self.root, 25)
        self.assertEqual(existing.read_bytes(), b'user data')

    def test_duplicate_or_unknown_source_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            VideoRecorder(self.root, 25, sources=[source('cam0'), source('cam0')])
        recorder = VideoRecorder(self.root, 25, sources=[source('cam0')])
        try:
            with self.assertRaises(ValueError):
                recorder.write_frames({'different': self.image})
        finally:
            recorder.close()

    def test_manifest_io_error_is_not_reported_as_success(self):
        recorder = VideoRecorder(self.root, 25)
        with patch.object(recorder._session, '_save_manifest', side_effect=OSError('disk failure')):
            with self.assertRaises(RuntimeError):
                recorder.close()

    def test_fps_correction_updates_manifest_without_rewriting_frame_log(self):
        for success in (True, False):
            with self.subTest(success=success):
                self.root = Path(self.temp.name) / str(success)
                recorder = VideoRecorder(self.root, 25, sources=[source('cam0')])
                for index in range(1, 4):
                    packet = FramePacket('cam0', index, 100., self.image,
                        capture_started_monotonic_ns=index * 100_000_000,
                        capture_completed_monotonic_ns=index * 100_000_000)
                    recorder.write_frames({'cam0': self.image}, packets={'cam0': packet})
                written = recorder.close()
                before = (self.root / 'frames.jsonl').read_bytes()
                with patch.object(recorder, '_reencode_to_fps', return_value=success):
                    recorder.correct_frame_rate(written)
                manifest, _ = self.read()
                camera = manifest['cameras'][0]
                self.assertEqual(camera['frame_rate_correction'], 'corrected' if success else 'failed')
                self.assertEqual(camera['video_fps'], 10. if success else 25.)
                self.assertEqual(before, (self.root / 'frames.jsonl').read_bytes())

    def test_record_button_passes_current_calibration_and_sources(self):
        from unittest.mock import Mock
        from mocap_app.io.calibration_io import CalibrationRepository
        from mocap_app.models.types import CalibrationBundle, CameraCalibration
        from mocap_app.ui.main_window import MainWindow
        values = calibration(['cam0'])['cameras']['cam0']
        bundle = CalibrationBundle(cameras={'cam0': CameraCalibration(
            source_id='cam0', image_size=(32, 24), intrinsics=values['intrinsics'],
            distortion=values['distortion'], rotation=values['rotation'], translation=values['translation'])})
        harness = SimpleNamespace(
            _video_recorder=None, _live_worker=Mock(), _active_sources=[source('cam0')],
            _runtime_tuning=SimpleNamespace(capture_fps=25), _calibration_panel=Mock(),
            _current_calibration_bundle=bundle, _calibration_manager=Mock(),
            _calibration_repo=CalibrationRepository(),
            _choose_recording_base_dir=lambda: self.root,
            _next_recording_output_dir=MainWindow._next_recording_output_dir,
            _set_dropped_frames=Mock(), _set_status=Mock(), _show_error=Mock(),
        )
        MainWindow._on_record_toggled(harness, True)
        recorder = harness._video_recorder
        self.assertIsNotNone(recorder)
        recorder.write_frames({'cam0': self.image})
        recorder.close()
        manifest = json.loads((recorder.output_dir / 'session.json').read_text())
        self.assertEqual(manifest['cameras'][0]['calibration_table'], 'cam_0')
        self.assertTrue((recorder.output_dir / manifest['calibration']['profile_file']).is_file())
        harness._live_worker.attach_recorder.assert_called_once_with(recorder)

    def test_live_worker_passes_read_times_and_camera_failure_to_recorder(self):
        recorder = VideoRecorder(self.root, 25, sources=[source('cam0'), source('cam1')])
        worker = LiveCaptureWorker([source('cam0'), source('cam1')], 25)
        worker.attach_recorder(recorder)
        image = self.image
        class Capture:
            def isOpened(self): return True
            def set(self, *args): return True
            def release(self): pass
            def grab(self): return True
            def retrieve(self): return True, image
        class MissingCapture(Capture):
            def retrieve(self):
                worker.stop()
                return False, None
        with patch.object(worker, '_open_capture', side_effect=[Capture(), MissingCapture()]):
            worker.run()
        worker.detach_recorder()
        recorder.close()
        _, rows = self.read()
        rows = {r['camera_id']: r for r in rows}
        self.assertEqual(rows['cam0']['status'], 'written')
        self.assertEqual(rows['cam1']['status'], 'capture_failed')
        self.assertEqual(rows['cam1']['reason'], 'retrieve_failed')
        self.assertEqual(rows['cam0']['batch_id'], rows['cam1']['batch_id'])
        self.assertIsInstance(rows['cam0']['capture_started_monotonic_ns'], int)
        self.assertGreaterEqual(rows['cam0']['capture_completed_monotonic_ns'], rows['cam0']['capture_started_monotonic_ns'])


if __name__ == '__main__':
    unittest.main()
