"""Import the official synchronized demo as a versioned HuCalib test session."""
from pathlib import Path
import shutil
import tomllib
import cv2
from mocap_app.io.recording_session import RecordingSession
from mocap_app.models.types import CameraSourceConfig


def import_demo(demo: Path, calibration: Path, output: Path) -> None:
    originals = sorted((demo / 'videos').glob('*.mp4'))
    if len(originals) != 4:
        raise ValueError('Expected four official single-person demo videos')
    tables = tomllib.loads(calibration.read_text(encoding='utf-8'))
    payload = {'cameras': {}}
    for video in originals:
        table = next(t for k, t in tables.items() if k != 'metadata' and t['name'] == video.stem)
        payload['cameras'][video.stem] = {
            'source_id': video.stem, 'intrinsics': table['matrix'], 'distortion': table['distortions'],
            'rotation': table['rotation'], 'translation': table['translation'], 'image_size': table['size'],
        }
    output.mkdir(parents=True, exist_ok=False)
    session = RecordingSession(output, 60, {},
        [CameraSourceConfig(v.stem, 'video', str(v)) for v in originals], payload)
    session.manifest['import_provenance'] = {
        'kind': 'official_pose2sim_demo', 'original_fps_preserved': True,
        'synchronization': 'assumed_from_official_demo',
        'timestamps': 'media timeline, not host acquisition or sensor exposure',
    }
    try:
        for video in originals:
            destination = output / video.name
            shutil.copy2(video, destination)
            capture = cv2.VideoCapture(str(destination))
            try:
                fps = capture.get(cv2.CAP_PROP_FPS)
                index = 0
                while True:
                    ok, image = capture.read()
                    if not ok:
                        break
                    index += 1
                    if index == 1:
                        session.video_opened(video.stem, destination, (image.shape[1], image.shape[0]), fps)
                    session.event({'camera_id': video.stem, 'batch_id': f'demo_{index}',
                        'batch_index': index, 'capture_batch_index': None, 'source_frame_index': index,
                        'timestamp_kind': 'media_timeline', 'timestamp_media_sec': (index - 1) / fps,
                        'timestamp_monotonic_ns': None}, 'written', video_frame_index=index)
            finally:
                capture.release()
    finally:
        session.finish()
