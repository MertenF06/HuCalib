"""Exercise actual codecs with the official Pose2Sim Demo_SinglePerson videos.

Run from the repository root: python tests/smoke_pose2sim_recording.py --help
This is an encoder/metadata test, not webcam acquisition or gait validation.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np

from mocap_app.io.video_recorder import VideoRecorder
from mocap_app.models.types import CameraSourceConfig, FramePacket


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True, help='Official TOML belonging to the demo videos')
    parser.add_argument('--output', type=Path, required=True, help='New or empty recording folder')
    parser.add_argument('--fps', type=float, default=25, help='Handoff rate; 25 tests the app default, 60 stresses the encoder')
    parser.add_argument('--live-worker', action='store_true', help='Also exercise actual LiveCaptureWorker file acquisition, without fault injection')
    parser.add_argument('--pose2sim-check', action='store_true', help='Also read exported matrices with installed Pose2Sim')
    args = parser.parse_args()
    videos = sorted((args.demo / 'videos').glob('*.mp4'))
    assert len(videos) == 4, 'Expected the four official single-person demo videos'
    original = tomllib.loads(args.calibration.read_text())
    ids = ['cam0', 'cam1', 'cam2', 'cam10']
    payload = {'cameras': {}}
    for camera_id, video in zip(ids, videos):
        cam = next(cam for key, cam in original.items() if key != 'metadata' and cam['name'] == video.stem)
        payload['cameras'][camera_id] = {
            'source_id': camera_id, 'intrinsics': cam['matrix'], 'distortion': cam['distortions'],
            'rotation': cam['rotation'], 'translation': cam['translation'], 'image_size': cam['size'],
        }
    recorder = VideoRecorder(args.output, args.fps, labels={key: 'Dezelfde camera' for key in ids},
                             sources=[CameraSourceConfig(key, 'video', str(video)) for key, video in zip(ids, videos)],
                             calibration_payload=payload)
    caps = {} if args.live_worker else {key: cv2.VideoCapture(str(video)) for key, video in zip(ids, videos)}
    start = time.perf_counter()
    try:
        if args.live_worker:
            from PySide6.QtCore import Qt
            from mocap_app.workers.capture_worker import LiveCaptureWorker
            worker = LiveCaptureWorker([CameraSourceConfig(key, 'video', str(video))
                                        for key, video in zip(ids, videos)], args.fps)
            batches = [0]
            def on_batch(_batch):
                batches[0] += 1
                if batches[0] == 100:
                    worker.stop()
            worker.batch_ready.connect(on_batch, Qt.ConnectionType.DirectConnection)
            worker.attach_recorder(recorder)
            worker.run()
            worker.detach_recorder()
            assert batches[0] == 100
        else:
            for frame_index in range(1, 101):
                frames, packets, failures = {}, {}, {}
                for key, cap in caps.items():
                    started = time.perf_counter_ns()
                    ok, image = cap.read()
                    ended = time.perf_counter_ns()
                    assert ok, (key, frame_index)
                    # Exactly one deliberate camera failure proves the mapping of
                    # remaining video indices to capture rounds after a gap.
                    if key == 'cam2' and frame_index == 37:
                        failures[key] = {'reason': 'simulated_capture_failure',
                                         'capture_started_monotonic_ns': started,
                                         'capture_completed_monotonic_ns': ended}
                    else:
                        frames[key] = image
                        packets[key] = FramePacket(key, frame_index, time.time(), image,
                            capture_started_monotonic_ns=started, capture_completed_monotonic_ns=ended)
                recorder.write_frames(frames, packets=packets, failures=failures,
                                      batch_id=f'demo_{frame_index}', capture_batch_index=frame_index)
                delay = start + frame_index / args.fps - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
    finally:
        for cap in caps.values():
            cap.release()
        written = recorder.close()
    recorder.correct_frame_rate(written)
    manifest = json.loads((args.output / 'session.json').read_text())
    rows = [json.loads(line) for line in (args.output / 'frames.jsonl').read_text().splitlines()]
    assert manifest['status'] == 'closed'
    assert len(rows) == 400
    assert len({(row['camera_id'], row['batch_index']) for row in rows}) == 400
    assert [c['camera_id'] for c in manifest['cameras']] == ids
    assert len(written) == 4
    for camera in manifest['cameras']:
        events = sorted((row for row in rows if row['camera_id'] == camera['camera_id'] and row['status'] == 'written'),
                        key=lambda row: row['video_frame_index'])
        assert [row['video_frame_index'] for row in events] == list(range(1, len(events) + 1))
        assert [row['source_frame_index'] for row in events] == sorted(row['source_frame_index'] for row in events)
        cap = cv2.VideoCapture(str(args.output / camera['video_file']))
        decoded = 0
        while cap.read()[0]:
            decoded += 1
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        assert decoded == len(events) == camera['counts']['written']
        assert abs(actual_fps - camera['video_fps']) < 0.01
    failures = [row for row in rows if row['status'] == 'capture_failed']
    if args.live_worker:
        assert failures == []
        assert all(row['timestamp_kind'] == 'host_read_midpoint' for row in rows)
    else:
        assert len(failures) == 1 and failures[0]['camera_id'] == 'cam2' and failures[0]['capture_batch_index'] == 37
        assert failures[0]['video_frame_index'] is None
    exported = tomllib.loads((args.output / 'calibration/calibration.toml').read_text())
    for index, camera in enumerate(manifest['cameras']):
        table = exported[camera['calibration_table']]
        assert table['name'] == ids[index]
        for exported_key, input_key in [('matrix', 'intrinsics'), ('rotation', 'rotation'), ('translation', 'translation')]:
            np.testing.assert_allclose(table[exported_key], payload['cameras'][ids[index]][input_key])
    if args.pose2sim_check:
        from Pose2Sim.common import computeP, retrieve_calib_params
        before = computeP(args.calibration)
        after = computeP(args.output / 'calibration/calibration.toml')
        for a, b in zip(before, after):
            np.testing.assert_allclose(a, b, atol=1e-8, rtol=1e-10)
        assert len(retrieve_calib_params(args.output / 'calibration/calibration.toml')['K']) == 4
    summary = {'status': 'passed', 'live_worker': args.live_worker, 'nominal_fps': args.fps, 'outcomes': dict(Counter(row['status'] for row in rows)),
               'camera_order': ids, 'video_frames': {c['camera_id']: c['counts']['written'] for c in manifest['cameras']},
               'pose2sim_matrix_check': args.pose2sim_check, 'analysis_issues': manifest['analysis_issues'],
               'scope': 'Actual demo videos/codec outputs; file capture worker or direct handoff with one deliberate capture gap; no physical webcams.'}
    (args.output / 'smoke-results.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
