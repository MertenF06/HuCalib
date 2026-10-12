"""Versioned recording metadata and a journal of terminal frame outcomes.

Paths in the manifest are relative to the session folder so renaming/moving a
recording preserves the camera/video/calibration association. Device indices
identify inputs within a session, not physical webcams across reconnects.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mocap_app.io.calibration_export import build_motion_capture_payload, camera_sort_key, to_toml
from mocap_app.models.types import CameraSourceConfig


class RecordingSession:
    """Own the frame journal and atomically replace the session manifest."""

    def __init__(self, output_dir: Path, fps: float, labels: dict[str, str],
                 sources: list[CameraSourceConfig] | None,
                 calibration_payload: dict[str, Any] | None) -> None:
        self._root = output_dir
        self._lock = threading.RLock()
        self._cameras: dict[str, dict[str, Any]] = {}
        self._strict_sources = sources is not None
        source_list = [source for source in (sources or []) if source.enabled]
        if len({s.source_id for s in source_list}) != len(source_list):
            raise ValueError('Camera source IDs must be unique.')
        self.manifest: dict[str, Any] = {
            'schema_version': 1, 'session_id': str(uuid.uuid4()),
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'recording', 'nominal_fps': fps,
            'time_origin': {'monotonic_ns': time.perf_counter_ns(), 'unix_sec': time.time()},
            'timing_convention': 'Host read times; not sensor exposure timestamps or hardware synchronization.',
            'video_integrity_check': 'not_performed',
            'frame_log': 'frames.jsonl', 'frame_indices': '1-based within each video',
            'calibration': None, 'cameras': [],
        }
        for source in sorted(source_list, key=lambda s: camera_sort_key(s.source_id)):
            self._add_camera(source.source_id, labels.get(source.source_id, source.label),
                             {'kind': source.kind, 'uri': source.uri})
        if sources is None:
            initial_ids = labels or (calibration_payload or {}).get('cameras', {})
            for source_id in sorted(initial_ids, key=camera_sort_key):
                self._add_camera(source_id, labels.get(source_id, source_id), {'kind': 'unknown', 'uri': None})
        if calibration_payload is not None:
            # Serialize now: subsequent changes to the in-memory calibration
            # cannot alter the snapshot used for this recording.
            calibration_bytes = json.dumps(calibration_payload, indent=2, allow_nan=False).encode('utf-8')
            calibration = json.loads(calibration_bytes)
            folder = self._root / 'calibration'
            folder.mkdir()
            (folder / 'calibration.json').write_bytes(calibration_bytes)
            mapping, skipped = build_motion_capture_payload(calibration, source_ids=list(self._cameras))
            toml_text = to_toml(mapping)
            # Store exactly the bytes we hash. Windows text mode would convert
            # LF to CRLF and make a freshly captured calibration fail validation.
            (folder / 'calibration.toml').write_bytes(toml_text.encode('utf-8'))
            self.manifest['calibration'] = {
                'profile_file': 'calibration/calibration.json',
                'profile_sha256': hashlib.sha256(calibration_bytes).hexdigest(),
                'toml_file': 'calibration/calibration.toml',
                'toml_sha256': hashlib.sha256(toml_text.encode('utf-8')).hexdigest(),
                'skipped_camera_ids': skipped, 'translation_units': 'meters',
                'extrinsics_convention': 'world-to-camera',
                'reference_camera_id': calibration.get('metadata', {}).get('extrinsics_reference_source_id'),
            }
            tables = [(key, cam) for key, cam in mapping.items() if key != 'metadata']
            for index, (table, cam) in enumerate(tables):
                camera = self._cameras[cam['name']]
                camera['calibration_table'] = table
                camera['calibration_image_size'] = cam['size']
                camera['calibration_order'] = index
        self._journal = (self._root / 'frames.jsonl').open('x', encoding='utf-8')
        self._save_manifest()

    def _add_camera(self, source_id: str, label: str, source: dict[str, Any]) -> None:
        index = len(self._cameras) + 1
        camera = {
            'camera_id': source_id, 'label': label or source_id,
            'video_stem': f'cam{index:02d}', 'video_order': index - 1,
            'source': source, 'hardware_identity_verified': False,
            'identity_scope': 'opencv_device_index' if source['kind'] == 'webcam' else source['kind'],
            'calibration_table': None, 'calibration_order': None,
            'calibration_image_size': None, 'calibration_match': 'missing',
            'video_file': None, 'image_size': None, 'video_fps': None,
            'resized_frames': 0,
            'frame_rate_correction': 'not_needed',
            'counts': {'written': 0, 'capture_failed': 0, 'queue_dropped': 0, 'encode_failed': 0},
        }
        self._cameras[source_id] = camera
        self.manifest['cameras'].append(camera)

    def camera(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            if source_id not in self._cameras:
                if self._strict_sources:
                    raise ValueError(f'Unknown recording camera: {source_id}')
                self._add_camera(source_id, source_id, {'kind': 'unknown', 'uri': None})
            return self._cameras[source_id]

    @property
    def camera_ids(self) -> list[str]:
        with self._lock:
            return list(self._cameras)

    def video_opened(self, source_id: str, path: Path, size: tuple[int, int], fps: float) -> None:
        with self._lock:
            camera = self.camera(source_id)
            camera.update(video_file=path.name, image_size=list(size), video_fps=fps)
            if camera['calibration_table'] is not None:
                camera['calibration_match'] = ('matching_resolution' if list(size) == camera['calibration_image_size']
                                                else 'resolution_mismatch')
            self._save_manifest()

    def event(self, row: dict[str, Any], status: str, *, video_frame_index: int | None = None,
              reason: str | None = None, resized: bool = False) -> None:
        with self._lock:
            camera = self.camera(row['camera_id'])
            event = dict(row, session_id=self.manifest['session_id'], status=status,
                         video_frame_index=video_frame_index, reason=reason, resized=resized)
            self._journal.write(json.dumps(event, allow_nan=False) + '\n')
            self._journal.flush()
            camera['counts'][status] += 1
            if resized:
                camera['resized_frames'] += 1

    def finish(self) -> None:
        with self._lock:
            if not self._journal.closed:
                self._journal.close()
            self.manifest['status'] = 'closed'
            self.manifest['closed_at_utc'] = datetime.now(timezone.utc).isoformat()
            self.manifest['pose2sim_camera_order'] = [c['camera_id'] for c in self.manifest['cameras']]
            # A readable TOML alone does not make an analysis reliable. Keep
            # missing calibration, resolution mismatches and frame gaps explicit.
            self.manifest['analysis_issues'] = [
                {'camera_id': camera['camera_id'], 'issue': issue}
                for camera in self.manifest['cameras']
                for issue in (
                    (['calibration_missing'] if camera['calibration_table'] is None else [])
                    + (['resolution_mismatch'] if camera['calibration_match'] == 'resolution_mismatch' else [])
                    + (['calibration_order_mismatch'] if camera['calibration_order'] is not None
                       and camera['calibration_order'] != camera['video_order'] else [])
                    + (['video_missing'] if not camera['video_file'] or not camera['counts']['written'] else [])
                    + (['missing_frames'] if any(camera['counts'][key] for key in
                       ('capture_failed', 'queue_dropped', 'encode_failed')) else [])
                    + (['frames_resized'] if camera['resized_frames'] else [])
                )
            ]
            self._save_manifest()

    def correction(self, source_id: str, fps: float | None, status: str) -> None:
        with self._lock:
            camera = self.camera(source_id)
            camera['frame_rate_correction'] = status
            if fps is not None:
                camera['video_fps'] = fps
            issues = self.manifest.setdefault('analysis_issues', [])
            issues[:] = [entry for entry in issues if not (
                entry['camera_id'] == source_id and entry['issue'].startswith('fps_correction_'))]
            if status in ('failed', 'in_progress'):
                issues.append({'camera_id': source_id, 'issue': f'fps_correction_{status}'})
            self._save_manifest()

    def _save_manifest(self) -> None:
        target = self._root / 'session.json'
        temporary = self._root / 'session.json.tmp'
        temporary.write_text(json.dumps(self.manifest, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(target)
