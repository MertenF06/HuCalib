"""Validate a recording and run Pose2Sim in a separate Python environment."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib

import cv2
import numpy as np

from mocap_app.io.calibration_export import to_toml

STAGES = ('poseEstimation', 'synchronization', 'personAssociation', 'triangulation',
          'filtering', 'markerAugmentation', 'kinematics')
STAGE_LABELS = dict(zip(STAGES, ('pose-detectie', 'synchronisatie', 'persoonskoppeling',
                               'triangulatie', 'filtering', 'markeruitbreiding', 'gewrichtshoeken')))


class AnalysisError(ValueError):
    """An input or analysis failure with a message suitable for the user."""


def _json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _input(root: Path, name: str) -> Path:
    if not isinstance(name, str) or Path(name).is_absolute():
        raise AnalysisError('Een sessiebestand heeft geen geldig relatief pad.')
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AnalysisError(f'Sessiebestand ontbreekt of ligt buiten de opnamemap: {name}')
    return path


def summarize_result(path: Path) -> dict:
    """Read numeric result rows rather than trusting file existence/headers."""
    lines = path.read_text(encoding='utf-8').splitlines()
    if path.suffix == '.trc':
        if len(lines) < 6:
            raise AnalysisError(f'Onvolledig trajectbestand: {path.name}')
        header = dict(zip(lines[1].split(), lines[2].split()))
        declared = int(header['NumFrames'])
        markers = int(header['NumMarkers'])
        data = [[float(value) for value in row.split()] for row in lines[5:] if row.strip()]
        expected_columns, time_column = 2 + 3 * markers, 1
        detail = {'markers': markers, 'units': header['Units']}
    else:
        end = next((i for i, row in enumerate(lines) if row.strip() == 'endheader'), None)
        if end is None or len(lines) <= end + 2:
            raise AnalysisError(f'Onvolledig hoekbestand: {path.name}')
        header = dict(row.split('=', 1) for row in lines[:end] if '=' in row)
        declared = int(header['nRows'])
        expected_columns, time_column = int(header['nColumns']), 0
        names = lines[end + 1].split()
        if len(names) != expected_columns or names[0] != 'time':
            raise AnalysisError(f'Ongeldige kolomindeling: {path.name}')
        data = [[float(value) for value in row.split()] for row in lines[end + 2:] if row.strip()]
        detail = {'coordinates': expected_columns - 1, 'in_degrees': header.get('inDegrees') == 'yes'}
    if not data or any(len(row) != expected_columns for row in data):
        raise AnalysisError(f'Ongeldige resultaatregels: {path.name}')
    array = np.asarray(data)
    if not np.isfinite(array).all() or not np.all(np.diff(array[:, time_column]) > 0):
        raise AnalysisError(f'Niet-eindige waarden of ongeldige tijdvolgorde: {path.name}')
    return dict(detail, rows=len(data), declared_rows=declared, all_values_finite=True,
                time_range_sec=[float(array[0, time_column]), float(array[-1, time_column])],
                header_matches_data=declared == len(data))


def validate_session(root: Path, *, diagnostic_resolution_mismatch: bool = False) -> dict:
    """Decode all videos and verify identity, calibration and batch integrity.

    Host timestamps do not prove sensor synchronization. Frame gaps are never
    repaired by truncating videos, silently interpolating or renumbering them.
    """
    root = root.resolve()
    manifest = json.loads((root / 'session.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('status') != 'closed':
        raise AnalysisError('Alleen afgeronde opnames met sessieschema 1 zijn ondersteund.')
    cameras = manifest.get('cameras', [])
    ids = [c['camera_id'] for c in cameras]
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise AnalysisError('Minimaal twee uniek gekoppelde camera’s zijn nodig.')
    calibration = manifest.get('calibration') or {}
    if (calibration.get('translation_units') != 'meters'
            or calibration.get('extrinsics_convention') != 'world-to-camera'):
        raise AnalysisError('Kalibratie moet meters en wereld-naar-camera-extrinsics gebruiken.')
    for kind in ('profile', 'toml'):
        path = _input(root, calibration.get(f'{kind}_file'))
        if _sha(path) != calibration.get(f'{kind}_sha256'):
            raise AnalysisError('De kalibratiekopie is gewijzigd sinds de opname.')
    tables = tomllib.loads(_input(root, calibration['toml_file']).read_text(encoding='utf-8'))
    table_names = [key for key in tables if key != 'metadata']
    if table_names != [c.get('calibration_table') for c in cameras]:
        raise AnalysisError('De volgorde van camera’s en kalibratietabellen komt niet overeen.')
    rows = [json.loads(line) for line in _input(root, manifest.get('frame_log')).read_text(encoding='utf-8').splitlines()]
    batches: dict[int, dict] = {}
    per_camera = {key: [] for key in ids}
    for row in rows:
        camera_id, batch = row.get('camera_id'), row.get('batch_index')
        if camera_id not in per_camera or row.get('session_id') != manifest['session_id']:
            raise AnalysisError('Het framelog hoort niet bij deze sessie/camera’s.')
        if type(batch) is not int or batch < 1 or camera_id in batches.setdefault(batch, {}):
            raise AnalysisError('Het framelog bevat een ongeldige of dubbele opnameronde.')
        batches[batch][camera_id] = row
        per_camera[camera_id].append(row)
    if not batches or sorted(batches) != list(range(1, max(batches) + 1)):
        raise AnalysisError('Er ontbreken opnamerondes in het framelog.')
    for batch in batches.values():
        if (set(batch) != set(ids) or len({r.get('batch_id') for r in batch.values()}) != 1
                or any(not isinstance(r.get('batch_id'), str) or not r['batch_id'] for r in batch.values())):
            raise AnalysisError('Een opnameronde heeft geen volledige, eenduidige camerakoppeling.')
        if any(r.get('status') != 'written' or r.get('resized') for r in batch.values()):
            raise AnalysisError('Ontbrekende of geschaalde frames: analyse geblokkeerd om tijdverschuiving te voorkomen.')
    warnings, checked, fps_values = [], [], []
    for index, camera in enumerate(cameras):
        if camera.get('video_order') != index or camera.get('calibration_order') != index:
            raise AnalysisError('Camera-/kalibratievolgorde is inconsistent.')
        if camera.get('frame_rate_correction') in ('failed', 'in_progress'):
            raise AnalysisError('De FPS-correctie is niet succesvol afgerond.')
        if camera.get('resized_frames', 0):
            raise AnalysisError('Geschaalde frames kunnen niet met de oorspronkelijke kalibratie worden geanalyseerd.')
        counts = Counter(row['status'] for row in per_camera[camera['camera_id']])
        if any(camera['counts'].get(key) != counts[key] for key in
               ('written', 'capture_failed', 'queue_dropped', 'encode_failed')):
            raise AnalysisError('Manifesttellingen komen niet overeen met het framelog.')
        events = sorted(per_camera[camera['camera_id']], key=lambda r: r['batch_index'])
        if [r['video_frame_index'] for r in events] != list(range(1, len(events) + 1)):
            raise AnalysisError('Videoframenummers komen niet overeen met opnamerondes.')
        table = tables[camera['calibration_table']]
        if table.get('name') != camera['camera_id']:
            raise AnalysisError('Kalibratienaam komt niet overeen met de camera-ID.')
        for key, shape in (('matrix', (3, 3)), ('rotation', (3,)), ('translation', (3,))):
            values = np.asarray(table.get(key), dtype=float)
            if values.shape != shape or not np.isfinite(values).all():
                raise AnalysisError(f'Ongeldige kalibratieparameters: {key}.')
        distortion = np.asarray(table.get('distortions'), dtype=float)
        if distortion.ndim != 1 or not distortion.size or not np.isfinite(distortion).all():
            raise AnalysisError('Ongeldige lensvervorming.')
        video = _input(root, camera.get('video_file'))
        capture = cv2.VideoCapture(str(video))
        decoded, sizes = 0, set()
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            while True:
                ok, image = capture.read()
                if not ok:
                    break
                decoded += 1
                sizes.add((image.shape[1], image.shape[0]))
        finally:
            capture.release()
        if decoded != len(events) or sizes != {tuple(camera['image_size'])}:
            raise AnalysisError('De video is onvolledig of heeft een afwijkend beeldformaat.')
        if not math.isfinite(fps) or fps <= 0 or abs(fps - camera['video_fps']) > 0.02:
            raise AnalysisError('De werkelijke video-FPS wijkt af van het manifest.')
        if list(camera['image_size']) != table['size']:
            warning = {'camera_id': camera['camera_id'], 'issue': 'resolution_mismatch'}
            if not diagnostic_resolution_mismatch:
                raise AnalysisError('Video en kalibratie hebben verschillende resoluties. Gebruik een passende kalibratie.')
            warnings.append(warning)
        fps_values.append(fps)
        checked.append({'camera_id': camera['camera_id'], 'video_file': camera['video_file'],
                        'sha256': _sha(video), 'decoded_frames': decoded, 'fps': fps})
    if max(fps_values) - min(fps_values) > 0.02:
        raise AnalysisError('Camera’s hebben verschillende video-FPS; tijdkoppeling moet eerst worden opgelost.')
    return {'manifest': manifest, 'calibration': tables, 'videos': checked,
            'warnings': warnings, 'frame_rate': fps_values[0],
            'synchronization_verified': False}


def run_analysis(session: Path, output: Path, config_template: Path, python: Path, *,
                 diagnostic_resolution_mismatch: bool = False,
                 assume_synchronized: bool = False, timeout: float = 1800) -> dict:
    """Create a new analysis folder. Originals are never modified."""
    session, output = session.resolve(), output.resolve()
    # A venv interpreter is often a symlink. Resolving that symlink selects the
    # base interpreter and loses the analysis environment's installed packages.
    python, config_template = python.expanduser().absolute(), config_template.resolve()
    if output == session or output.is_relative_to(session) or session.is_relative_to(output):
        raise AnalysisError('Bewaar de analyse in een aparte map buiten de opname.')
    if timeout <= 0 or not math.isfinite(timeout):
        raise AnalysisError('De tijdslimiet moet positief zijn.')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'validating', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'source_session': str(session), 'stages': [], 'artifacts': [],
              'diagnostic_resolution_mismatch': diagnostic_resolution_mismatch,
              'synchronization': 'assumed_by_caller' if assume_synchronized else 'pose2sim_motion_estimation',
              'scientific_accuracy_validated': False}
    report_path = output / 'analysis.json'
    _json(report_path, report)
    try:
        checked = validate_session(session, diagnostic_resolution_mismatch=diagnostic_resolution_mismatch)
        report['session_id'] = checked['manifest']['session_id']
        report['inputs'] = checked['videos']
        report['warnings'] = checked['warnings']
        report['session_sha256'] = _sha(session / 'session.json')
        report['frame_log_sha256'] = _sha(_input(session, checked['manifest']['frame_log']))
        config = tomllib.loads(config_template.read_text(encoding='utf-8'))
        for section in ('project', 'pose', 'synchronization', 'filtering', 'kinematics'):
            if not isinstance(config.get(section), dict):
                raise AnalysisError(f'Configuratiesjabloon mist sectie {section}.')
        project = output / 'project'
        project.mkdir()
        videos, calibration = project / 'videos', project / 'calibration'
        videos.mkdir()
        calibration.mkdir()
        provenance = output / 'source-metadata'
        provenance.mkdir()
        manifest = checked['manifest']
        for source, name in ((session / 'session.json', 'session.json'),
                             (_input(session, manifest['frame_log']), 'frames.jsonl'),
                             (_input(session, manifest['calibration']['profile_file']), 'calibration.json'),
                             (_input(session, manifest['calibration']['toml_file']), 'calibration.toml')):
            shutil.copy2(source, provenance / name)
        report['source_metadata_sha256'] = {p.name: _sha(p) for p in provenance.iterdir()}
        mapping = []
        for index, camera in enumerate(checked['manifest']['cameras']):
            original = _input(session, camera['video_file'])
            stem = f'cam{index + 1:02d}'
            destination = videos / (stem + original.suffix.lower())
            shutil.copy2(original, destination)
            if _sha(destination) != checked['videos'][index]['sha256']:
                raise AnalysisError('De video is veranderd tijdens het voorbereiden van de analyse.')
            checked['calibration'][camera['calibration_table']]['name'] = stem
            mapping.append({'camera_id': camera['camera_id'], 'video': str(destination.relative_to(output)),
                            'calibration_table': camera['calibration_table'], 'pose2sim_name': stem})
        (calibration / 'calibration.toml').write_text(to_toml(checked['calibration']), encoding='utf-8')
        shutil.copy2(config_template, output / 'config-template.toml')
        config['project'].update(project_dir=str(project), frame_rate=checked['frame_rate'])
        config['pose'].update(device='cpu', backend='onnxruntime', parallel_workers_pose=1,
                              display_detection=False, save_video='none', overwrite_pose=True)
        config['synchronization'].update(synchronization_gui=False, display_sync_plots=False)
        # Pose2Sim creates a Qt plotWindow even when only saving filter plots.
        # Keep both off in the separate analysis process; HuCalib plots results.
        config['filtering'].update(display_figures=False, save_filt_plots=False)
        config['kinematics'].update(make_bvh=False, parallel_workers_kinematics=1)
        _json(output / 'effective-config.json', config)
        report.update(status='running', camera_mapping=mapping,
                      config_template_sha256=_sha(output / 'config-template.toml'))
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[2])
        env['PYTHONPATH'] = package_root + os.pathsep + env.get('PYTHONPATH', '')
        env['MPLBACKEND'] = 'Agg'
        for stage in STAGES:
            if stage == 'synchronization' and assume_synchronized:
                report['stages'].append({'name': stage, 'status': 'skipped', 'reason': 'explicit_caller_assumption'})
                _json(report_path, report)
                continue
            entry = {'name': stage, 'status': 'running'}
            report['stages'].append(entry)
            _json(report_path, report)
            print(f'Analyse: {STAGE_LABELS[stage]}', flush=True)
            start = time.monotonic()
            with (output / f'{stage}.log').open('w', encoding='utf-8') as log:
                result = subprocess.run([str(python), '-m', 'mocap_app.analysis.pose2sim_worker',
                                         str(output), stage], cwd=project, env=env,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            entry.update(seconds=round(time.monotonic() - start, 3), returncode=result.returncode)
            if result.returncode:
                entry['status'] = 'failed'
                raise AnalysisError(f'Pose2Sim-stap {stage} is mislukt; zie {stage}.log.')
            entry['status'] = 'completed'
            _json(report_path, report)
        artifacts = [p for p in project.rglob('*') if p.is_file() and p.suffix.lower() in ('.trc', '.mot', '.osim')]
        if not any(p.suffix == '.trc' for p in artifacts) or not any(p.suffix == '.mot' for p in artifacts):
            raise AnalysisError('Pose2Sim gaf geen complete 3D-trajecten en gewrichtshoeken terug.')
        report['artifacts'] = [{'path': str(p.relative_to(output)), 'sha256': _sha(p), 'bytes': p.stat().st_size}
                               for p in sorted(artifacts)]
        report['result_summary'] = []
        for path in sorted(artifacts):
            if path.suffix in ('.trc', '.mot'):
                summary = dict(summarize_result(path), path=str(path.relative_to(output)))
                report['result_summary'].append(summary)
                if not summary['header_matches_data']:
                    report['warnings'].append({'issue': 'result_header_frame_count_mismatch',
                                               'path': summary['path'], 'rows': summary['rows'],
                                               'declared_rows': summary['declared_rows']})
        report['environment'] = json.loads((output / 'environment.json').read_text(encoding='utf-8'))
        report['status'] = 'completed'
    except Exception as error:
        if report['stages'] and report['stages'][-1]['status'] == 'running':
            report['stages'][-1]['status'] = 'failed'
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        _json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description='Analyseer een HuCalib-opname met een aparte Pose2Sim-omgeving.')
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--config', required=True, type=Path, help='Volledig Pose2Sim Config.toml-sjabloon')
    parser.add_argument('--python', type=Path, help='Python uit de afzonderlijke Pose2Sim-omgeving')
    parser.add_argument('--check-only', action='store_true', help='Controleer opname, instellingen en analyseomgeving zonder analyse')
    parser.add_argument('--diagnostic-resolution-mismatch', action='store_true', help='Alleen diagnostiek: accepteer een resolutieverschil zonder kalibratie aan te passen')
    parser.add_argument('--assume-synchronized', action='store_true', help='Sla bewegingsgebaseerde synchronisatie over; alleen voor reeds gesynchroniseerde invoer')
    parser.add_argument('--timeout', type=float, default=1800, help='Tijdslimiet per stap in seconden')
    args = parser.parse_args()
    try:
        if args.check_only:
            checked = validate_session(args.session, diagnostic_resolution_mismatch=args.diagnostic_resolution_mismatch)
            config = tomllib.loads(args.config.read_text(encoding='utf-8'))
            for section in ('project', 'pose', 'synchronization', 'filtering', 'kinematics'):
                if not isinstance(config.get(section), dict):
                    raise AnalysisError(f'Configuratiesjabloon mist sectie {section}.')
            from Pose2Sim import Pose2Sim  # Verify the chosen environment's dependencies.
            print(json.dumps({'session_id': checked['manifest']['session_id'],
                              'videos': checked['videos'], 'warnings': checked['warnings'],
                              'frame_rate': checked['frame_rate']}))
            return 0
        if args.output is None or args.python is None:
            parser.error('--output en --python zijn nodig om een analyse te starten')
        run_analysis(args.session, args.output, args.config, args.python,
                     diagnostic_resolution_mismatch=args.diagnostic_resolution_mismatch,
                     assume_synchronized=args.assume_synchronized, timeout=args.timeout)
    except Exception as error:
        print(f'Analyse gestopt: {error}', file=sys.stderr)
        return 1
    print(f'Analyse voltooid: {args.output / "analysis.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
