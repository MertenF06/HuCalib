"""Experimental GaitDynamics bridge; no torch/nimble imports in the GUI."""
from __future__ import annotations

import argparse
import bisect
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET

UPSTREAM_COMMIT = '8dc5aed58161a129fd2d58ff071cb802f2654be9'
SOURCE_SHA256 = 'd7573e69b522b18f12a9703571989538697f98d72938160bd1a402118eb7c682'
MODEL_HASHES = {
    'GaitDynamicsRefinement.pt': '34a659ce485ea48eb11e1400dac53c46892e1487bcf347c16c529c463dd011fb',
    'GaitDynamicsDiffusion.pt': '709f7229013313b512585c35a6457155be2a7ce650a7ad08d6e2d85c04c4a260',
}
COORDINATES = ('pelvis_tilt', 'pelvis_list', 'pelvis_rotation', 'pelvis_tx', 'pelvis_ty', 'pelvis_tz',
    'hip_flexion_r', 'hip_adduction_r', 'hip_rotation_r', 'knee_angle_r', 'ankle_angle_r',
    'subtalar_angle_r', 'mtp_angle_r', 'hip_flexion_l', 'hip_adduction_l', 'hip_rotation_l',
    'knee_angle_l', 'ankle_angle_l', 'subtalar_angle_l', 'mtp_angle_l',
    'lumbar_extension', 'lumbar_bending', 'lumbar_rotation')
DEPENDENT_COORDINATES = {'knee_angle_r_beta', 'knee_angle_l_beta'}


class GaitDynamicsError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_motion(path: Path) -> tuple[list[str], list[list[float]], dict]:
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    try:
        end = next(i for i, line in enumerate(lines) if line.strip() == 'endheader')
    except StopIteration:
        raise GaitDynamicsError('MOT-bestand mist endheader.') from None
    header = {}
    for line in lines[:end]:
        if '=' in line:
            key, value = line.split('=', 1)
            header[key.strip()] = value.strip()
    data = [line.split() for line in lines[end + 1:] if line.strip()]
    if not data or data[0][0] != 'time':
        raise GaitDynamicsError('MOT-bestand mist de tijdkolom.')
    names = data[0]
    if len(set(names)) != len(names):
        raise GaitDynamicsError('MOT-bestand bevat dubbele kolommen.')
    rows = [[float(value) for value in line] for line in data[1:]]
    if len(rows) < 2 or any(len(row) != len(names) for row in rows):
        raise GaitDynamicsError('MOT-bestand heeft onvoldoende of onvolledige meetregels.')
    if not all(math.isfinite(value) for row in rows for value in row):
        raise GaitDynamicsError('MOT-bestand bevat niet-eindige waarden.')
    for key, count in (('nRows', len(rows)), ('nColumns', len(names))):
        if key in header and int(header[key]) != count:
            raise GaitDynamicsError(f'MOT-header {key} komt niet overeen met de meetgegevens.')
    return names, rows, header


def inspect_inputs(motion: Path, model: Path) -> dict:
    """Conservative first bridge: full 23-coordinate gait model and kinematics."""
    names, rows, header = read_motion(motion)
    errors = []
    if header.get('inDegrees') not in ('yes', 'no'):
        errors.append('Hoekeenheden ontbreken: inDegrees moet yes of no zijn.')
    deltas = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
    step = statistics.median(deltas)
    if step <= 0 or any(delta <= 0 or abs(delta - step) > max(step * .01, 1e-5) for delta in deltas):
        errors.append('Tijdstappen moeten oplopend en regelmatig zijn; framegaten zijn niet toegestaan.')
    duration = rows[-1][0] - rows[0][0]
    if duration < 1.52:
        errors.append('GaitDynamics vereist minimaal 1,52 seconden aaneengesloten kinematica.')
    missing = [name for name in COORDINATES if name not in names]
    if missing:
        errors.append('Ontbrekende GaitDynamics-coördinaten: ' + ', '.join(missing) +
                      '. Rugsegmenten mogen niet zomaar worden hernoemd naar lumbar-coördinaten.')
    tree = ET.parse(model)
    coordinates = [element.get('name') for element in tree.findall('.//JointSet//Coordinate')]
    missing_model = [name for name in COORDINATES if name not in coordinates]
    extra_model = [name for name in coordinates if name not in COORDINATES and name not in DEPENDENT_COORDINATES]
    bodies = {element.get('name') for element in tree.findall('.//BodySet/objects/Body')}
    required_bodies = {'pelvis', 'femur_r', 'tibia_r', 'talus_r', 'calcn_r',
                       'femur_l', 'tibia_l', 'talus_l', 'calcn_l', 'torso'}
    if missing_model or extra_model or not required_bodies.issubset(bodies):
        errors.append('Het OpenSim-model is geen ondersteund GaitDynamics-model met 23 onafhankelijke '
                      'coördinaten. Het huidige Pose2Sim-model met een gesegmenteerde rug vereist eerst modelconversie.')
    return {'schema_version': 1, 'compatible': not errors, 'errors': errors,
            'motion': str(motion.absolute()), 'model': str(model.absolute()),
            'rows': len(rows), 'duration_seconds': duration,
            'sampling_rate': 1 / step if step > 0 else None,
            'in_degrees': header.get('inDegrees'), 'missing_coordinates': missing,
            'missing_model_coordinates': missing_model, 'extra_model_coordinates': extra_model,
            'runtime': 'afzonderlijke Linux-omgeving of WSL2; nimblephysics heeft geen Windows-wheels',
            'scientific_accuracy_validated': False}


def verify_runtime(runtime: Path) -> None:
    for name, expected in {'gait_dynamics.py': SOURCE_SHA256, **MODEL_HASHES}.items():
        if not (runtime / name).is_file() or sha256(runtime / name) != expected:
            raise GaitDynamicsError(f'Officieel runtimebestand ontbreekt of heeft een onjuiste checksum: {name}')


def prepare_inference_motion(source: Path, target: Path) -> dict:
    """Explicit actual-time interpolation to the refinement model's 100 Hz grid.

    Upstream rounds the source FPS to an integer before resampling. In
    particular 59.94 Hz would silently drift. Keep the source file untouched,
    interpolate from its real timestamps, and give upstream exactly 100 Hz.
    """
    names, rows, header = read_motion(source)
    if header.get('inDegrees') not in ('yes', 'no'):
        raise GaitDynamicsError('Onbekende hoekeenheid voor tijdinterpolatie.')
    times = [row[0] for row in rows]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise GaitDynamicsError('Tijdstappen moeten oplopend zijn.')
    values = [[row[names.index(name)] for name in COORDINATES] for row in rows]
    period = 360 if header['inDegrees'] == 'yes' else 2 * math.pi
    for column, name in enumerate(COORDINATES):
        if name in COORDINATES[3:6]:
            continue
        raw_previous, previous = values[0][column], values[0][column]
        for row in values[1:]:
            raw = row[column]
            row[column] = previous + (raw - raw_previous + period / 2) % period - period / 2
            raw_previous, previous = raw, row[column]
    count = math.floor((times[-1] - times[0]) * 100 + 1e-8) + 1
    if count < 152:
        raise GaitDynamicsError('Te weinig 100 Hz-tijdstappen voor GaitDynamics.')
    output = []
    for i in range(count):
        time = times[0] + i / 100
        index = min(max(bisect.bisect_right(times, time) - 1, 0), len(times) - 2)
        fraction = (time - times[index]) / (times[index + 1] - times[index])
        if fraction < -1e-8 or fraction > 1 + 1e-8:
            raise GaitDynamicsError('Tijdinterpolatie zou buiten de opname extrapoleren.')
        fraction = min(max(fraction, 0.), 1.)
        output.append([time] + [a + fraction * (b - a) for a, b in zip(values[index], values[index + 1])])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        stream.write(f'HU3D explicit 100 Hz inference input\nnRows={count}\nnColumns=24\ninDegrees={header["inDegrees"]}\nendheader\n')
        stream.write('\t'.join(('time', *COORDINATES)) + '\n')
        stream.write('\n'.join('\t'.join(format(value, '.17g') for value in row) for row in output) + '\n')
    return {'method': 'linear interpolation of continuous coordinates using actual source times',
        'source_frames': len(rows), 'output_frames': count, 'target_sampling_rate_hz': 100,
        'source_time_range_s': [times[0], times[-1]], 'output_time_range_s': [output[0][0], output[-1][0]],
        'source_sampling_rate_hz': (len(rows) - 1) / (times[-1] - times[0]),
        'source_sha256': sha256(source), 'inference_sha256': sha256(target),
        'extrapolation': False, 'generated_missing_frames': False,
        'angular_wraps_removed_before_interpolation': True}


def run_analysis(motion: Path, model: Path, output: Path, runtime: Path, python: Path,
                 height_m: float, mass_kg: float, treadmill_speed: float, timeout=1800) -> dict:
    motion, model, output, runtime = [path.expanduser().absolute() for path in (motion, model, output, runtime)]
    checked = inspect_inputs(motion, model)
    if not checked['compatible']:
        raise GaitDynamicsError('\n'.join(checked['errors']))
    if not all(math.isfinite(v) for v in (height_m, mass_kg, treadmill_speed)) or not (
            1 <= height_m <= 2.5 and 30 <= mass_kg <= 200 and 0 <= treadmill_speed <= 10):
        raise GaitDynamicsError('Vul lengte (1–2,5 m), massa (30–200 kg) en loopbandsnelheid (0–10 m/s) in.')
    verify_runtime(runtime)
    if motion.is_relative_to(output) or model.is_relative_to(output) or runtime.is_relative_to(output):
        raise GaitDynamicsError('Kies een nieuwe uitvoermap buiten de invoer en runtime.')
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / 'inputs'
    inputs.mkdir()
    report = {'schema_version': 1, 'status': 'preparing', 'upstream_commit': UPSTREAM_COMMIT,
              'device': 'cpu', 'height_m': height_m, 'mass_kg': mass_kg,
              'treadmill_speed_m_s': treadmill_speed, 'input_check': checked,
              'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'scientific_accuracy_validated': False, 'generated_missing_kinematics': False,
              'warnings': ['Dit zijn modelvoorspellingen, geen gemeten krachten.',
                           'De rekeninvoer wordt op de werkelijke tijdstippen geïnterpoleerd naar 100 Hz; dit zijn geen extra opgenomen frames.',
                           'Vrije momenten zijn niet voorspeld; de upstream torque-kolommen bevatten nullen.']}
    report_path = output / 'gaitdynamics.json'
    def save():
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    save()
    try:
        for original, target in ((motion, inputs / 'motion.mot'), (model, inputs / 'model.osim')):
            # DrvFS can deny copying Unix permissions/timestamps even when
            # file content is writable by our WSL service user. Only bytes
            # are relevant to inference; their integrity is checked below.
            shutil.copyfile(original, target)
            if sha256(original) != sha256(target):
                raise GaitDynamicsError('De invoer is gewijzigd tijdens het kopiëren.')
        conversion_path = motion.parent / 'conversion.json'
        if conversion_path.is_file():
            conversion = json.loads(conversion_path.read_text(encoding='utf-8'))
            reference = motion.parent / 'kinematics-reference.json'
            artifacts = conversion.get('artifacts', {})
            if conversion.get('status') != 'completed' or any(
                    artifacts.get(path.name) != sha256(path) for path in (motion, model, reference)):
                raise GaitDynamicsError('Conversierapport en invoer horen niet aantoonbaar bij elkaar.')
            shutil.copyfile(reference, inputs / reference.name)
            report['conversion'] = conversion
            report['warnings'].extend(conversion['warnings'])
        report['input_sha256'] = {p.name: sha256(p) for p in inputs.iterdir()}
        report['model_sha256'] = {name: sha256(runtime / name) for name in MODEL_HASHES}
        report['status'] = 'running'
        save()
        environment = os.environ.copy()
        environment.update(CUDA_VISIBLE_DEVICES='', ACCELERATE_USE_CPU='true', MPLBACKEND='Agg',
                           PYTHONPATH=str(Path(__file__).resolve().parents[2]) + os.pathsep + environment.get('PYTHONPATH', ''))
        with (output / 'gaitdynamics.log').open('w', encoding='utf-8') as log:
            process = subprocess.run([str(python.expanduser().absolute()), '-m',
                'mocap_app.analysis.gaitdynamics_worker', str(output), str(runtime)],
                cwd=inputs, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if process.returncode:
            raise GaitDynamicsError('GaitDynamics is mislukt; zie gaitdynamics.log.')
        result = inputs / 'motion_grf_pred___.mot'
        names, rows, _ = read_motion(result)
        environment_report = json.loads((output / 'environment.json').read_text(encoding='utf-8'))
        preprocessing = environment_report['preprocessing']
        if len(rows) != preprocessing['output_frames'] or any(
                abs(row[0] - round(preprocessing['output_time_range_s'][0] + i / 100, 5)) > 1e-8
                for i, row in enumerate(rows)):
            raise GaitDynamicsError('GaitDynamics-uitvoertijden komen niet overeen met de gecontroleerde 100 Hz-rekeninvoer.')
        expected = ['time'] + [f'{prefix}{side}_{axis}' for side in (1, 2)
            for prefix, axes in (('force', ('vx', 'vy', 'vz', 'px', 'py', 'pz')), ('torque', ('x', 'y', 'z')))
            for axis in axes]
        if names != expected:
            raise GaitDynamicsError('GaitDynamics-resultaat heeft onverwachte kolommen.')
        target = output / 'ground_reaction_forces.mot'
        shutil.copyfile(result, target)
        report.update(status='completed', result={'path': target.name, 'sha256': sha256(target),
            'rows': len(rows), 'sampling_rate_hz': 100, 'time_range_s': [rows[0][0], rows[-1][0]],
            'force_unit': 'N', 'cop_unit': 'm', 'right_foot': 'force1', 'left_foot': 'force2'})
        report['environment'] = environment_report
    except Exception as error:
        details = traceback.format_exc()
        report.update(status='failed', error=f'{type(error).__name__}: {error}', traceback=details)
        try:
            with (output / 'gaitdynamics.log').open('a', encoding='utf-8') as log:
                log.write('\n' + details)
        except OSError as log_error:
            report['diagnostic_log_error'] = str(log_error)
        raise
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description='Experimentele GaitDynamics-koppeling op CPU')
    for name in ('motion', 'model'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    for name in ('output', 'runtime', 'python'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--height', type=float)
    parser.add_argument('--mass', type=float)
    parser.add_argument('--treadmill-speed', type=float, default=0)
    args = parser.parse_args()
    try:
        if args.check_only:
            report = inspect_inputs(args.motion, args.model)
            print(json.dumps(report, indent=2))
            return 0 if report['compatible'] else 1
        if any(getattr(args, key) is None for key in ('output', 'runtime', 'python', 'height', 'mass')):
            parser.error('--output, --runtime, --python, --height en --mass zijn nodig')
        report = run_analysis(args.motion, args.model, args.output, args.runtime, args.python,
                              args.height, args.mass, args.treadmill_speed)
        print(json.dumps(report, indent=2))
        return 0
    except Exception as error:
        print(f'GaitDynamics gestopt: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
