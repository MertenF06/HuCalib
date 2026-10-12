"""Geometric reduction of Pose2Sim kinematics; OpenSim runs outside the GUI.

This is a kinematic conversion, not a musculoskeletal/dynamic equivalence.
The original legs are retained; the segmented spine is replaced by a rigid
trunk. Every input frame is checked with forward kinematics before acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from mocap_app.analysis.gaitdynamics import (COORDINATES, DEPENDENT_COORDINATES,
    GaitDynamicsError, inspect_inputs, read_motion, sha256)

LEG_BODIES = tuple(name + '_' + side for side in ('r', 'l')
                   for name in ('femur', 'tibia', 'patella', 'talus', 'calcn', 'toes'))
BODY_NAMES = ('pelvis', 'femur_r', 'tibia_r', 'talus_r', 'calcn_r',
              'femur_l', 'tibia_l', 'talus_l', 'calcn_l', 'torso')
# Engineering rejection limits, not clinical accuracy claims.
MAX_TRUNK_POSITION_ERROR_M = .03
MAX_LOWER_POSITION_ERROR_M = 1e-6
MAX_ORIENTATION_ERROR_RAD = 1e-6


def _vector(value):
    return [value.get(i) for i in range(3)]


def _transform(frame, state, np):
    transform = frame.getTransformInGround(state)
    return (np.array([[transform.R().get(i, j) for j in range(3)] for i in range(3)]),
            np.array(_vector(transform.p())))


def _set_state(model, state, values):
    for name, value in values.items():
        model.getCoordinateSet().get(name).setValue(state, float(value), False)
    model.realizePosition(state)


def _numbers(node, tag, values):
    element = node.find(tag)
    if element is None:
        element = ET.SubElement(node, tag)
    element.text = ' '.join(format(float(value), '.17g') for value in values)


def _joint(name, parent, child, parent_point, child_point, parent_rotation,
           child_rotation, coordinates, Rotation, pin=False):
    joint = ET.Element('PinJoint' if pin else 'CustomJoint', name=name)
    ET.SubElement(joint, 'socket_parent_frame').text = parent + '_offset'
    ET.SubElement(joint, 'socket_child_frame').text = child + '_offset'
    coordinate_set = ET.SubElement(joint, 'coordinates')
    for name in coordinates:
        coord = ET.SubElement(coordinate_set, 'Coordinate', name=name)
        ET.SubElement(coord, 'default_value').text = '0'
        ET.SubElement(coord, 'range').text = '-3.141592653589793 3.141592653589793'
        ET.SubElement(coord, 'clamped').text = 'false'
        ET.SubElement(coord, 'locked').text = 'false'
    frames = ET.SubElement(joint, 'frames')
    for body, point, rotation in ((parent, parent_point, parent_rotation),
                                   (child, child_point, child_rotation)):
        frame = ET.SubElement(frames, 'PhysicalOffsetFrame', name=body + '_offset')
        ET.SubElement(frame, 'socket_parent').text = '/bodyset/' + body
        _numbers(frame, 'translation', point)
        _numbers(frame, 'orientation', Rotation.from_matrix(rotation).as_euler('XYZ'))
    if not pin:
        spatial = ET.SubElement(joint, 'SpatialTransform')
        for i, axis in enumerate(((0, 0, 1), (1, 0, 0), (0, 1, 0),
                                   (1, 0, 0), (0, 1, 0), (0, 0, 1))):
            transform = ET.SubElement(spatial, 'TransformAxis',
                name=('rotation' if i < 3 else 'translation') + str(i % 3 + 1))
            ET.SubElement(transform, 'coordinates').text = coordinates[i] if i < 3 else ''
            _numbers(transform, 'axis', axis)
            if i < 3:
                _numbers(ET.SubElement(transform, 'LinearFunction'), 'coefficients', (1, 0))
            else:
                ET.SubElement(ET.SubElement(transform, 'Constant'), 'value').text = '0'
    return joint


def convert_model(motion: Path, model_path: Path, output: Path) -> dict:
    """Create a new model/MOT pair or leave a failed, explicitly labelled report.

    Requires the existing Pose2Sim Python environment (OpenSim/numpy/scipy).
    No resampling, gap filling or inference of missing joint angles is done.
    """
    import numpy as np
    import opensim
    from scipy.spatial.transform import Rotation

    motion, model_path, output = [p.expanduser().absolute() for p in (motion, model_path, output)]
    names, rows, header = read_motion(motion)
    if header.get('inDegrees') not in ('yes', 'no'):
        raise GaitDynamicsError('Hoekeenheden ontbreken: inDegrees moet yes of no zijn.')
    if motion.is_relative_to(output) or model_path.is_relative_to(output):
        raise GaitDynamicsError('Kies een nieuwe uitvoermap buiten de bronbestanden.')
    original_hashes = {'motion': sha256(motion), 'model': sha256(model_path)}
    source = opensim.Model(str(model_path))
    state = source.initSystem()
    coordinates = source.getCoordinateSet()
    source_coordinates = [coordinates.get(i).getName() for i in range(coordinates.getSize())]
    missing = set(source_coordinates) - set(names) - DEPENDENT_COORDINATES
    if missing:
        raise GaitDynamicsError('Beweging mist modelcoördinaten: ' + ', '.join(sorted(missing)))
    if set(names) - {'time'} - set(source_coordinates):
        raise GaitDynamicsError('Beweging bevat coördinaten die niet in het bronmodel staan.')
    body_set = source.getBodySet()
    body_names = [body_set.get(i).getName() for i in range(body_set.getSize())]
    if not set(LEG_BODIES + ('pelvis', 'torso')).issubset(body_names):
        raise GaitDynamicsError('Dit is geen ondersteund Pose2Sim-lichaamsmodel.')
    if not set(COORDINATES[:20]).issubset(source_coordinates):
        raise GaitDynamicsError('De vereiste been- en bekkencoördinaten ontbreken.')
    # Use zero independent coordinates, not subject-specific default pelvis pose.
    neutral = {name: 0. for name in source_coordinates}
    _set_state(source, state, neutral)
    default_transforms = {name: _transform(body_set.get(name), state, np) for name in body_names}
    pelvis_r, pelvis_p = default_transforms['pelvis']
    torso_r, torso_p = default_transforms['torso']
    tree = ET.parse(model_path)
    model = tree.find('Model')
    joints = model.find('JointSet/objects')
    spinal = next((joint for joint in joints if joint.get('name') == 'L5_S1_IVDjnt'), None)
    if spinal is None:
        raise GaitDynamicsError('Alleen het Pose2Sim-model met gesegmenteerde rug wordt ondersteund.')
    pivot = _transform(source.getJointSet().get(spinal.get('name')).getParentFrame(), state, np)[1]
    parent_point = pelvis_r.T @ (pivot - pelvis_p)
    child_point = torso_r.T @ (pivot - torso_p)
    child_rotation = torso_r.T @ pelvis_r
    source_lookup = {name: i for i, name in enumerate(names)}
    angular = {name: coordinates.get(name).getMotionType() == opensim.Coordinate.Rotational
               for name in source_coordinates}
    # A segmented spine has no exact single anatomical pivot. Register a fixed
    # effective pivot from alternate frames while keeping its neutral pose and
    # all rotations exact. Remaining frames independently check the reduction.
    # Bound the registration so a poor fit cannot invent a remote pivot.
    source_values, relative_rotations, relative_positions = [], [], []
    for row in rows:
        values = {name: row[source_lookup[name]] * (np.pi / 180 if angular[name] and header['inDegrees'] == 'yes' else 1)
                  for name in source_coordinates if name in source_lookup}
        source_values.append(values)
        _set_state(source, state, values)
        pr, pp = _transform(body_set.get('pelvis'), state, np)
        tr, tp = _transform(body_set.get('torso'), state, np)
        relative_rotations.append(pr.T @ tr)
        relative_positions.append(pr.T @ (tp - pp))
    neutral_relative = pelvis_r.T @ torso_r
    neutral_position = pelvis_r.T @ (torso_p - pelvis_p)
    design = np.concatenate([neutral_relative - r for r in relative_rotations[::2]])
    residual = np.concatenate([p - (parent_point - r @ child_point)
                               for p, r in zip(relative_positions[::2], relative_rotations[::2])])
    # Small regularization chooses the anatomical pivot when rotations do not
    # identify a unique point, e.g. a constant trunk pose or planar movement.
    regularization = np.sqrt(len(rows) / 2) * .02
    delta, _, _, _ = np.linalg.lstsq(np.vstack((design, regularization * np.eye(3))),
                                    np.concatenate((residual, np.zeros(3))), rcond=None)
    max_pivot_shift = .05
    if np.linalg.norm(delta) > max_pivot_shift:
        delta *= max_pivot_shift / np.linalg.norm(delta)
    initial_errors = [float(np.linalg.norm(p - (parent_point - r @ child_point)))
                      for p, r in zip(relative_positions, relative_rotations)]
    child_point += delta
    parent_point = neutral_position + neutral_relative @ child_point
    _set_state(source, state, neutral)

    # Merge discarded upper-body mass properties at the neutral pose. Moving
    # arms/head are NOT dynamically equivalent to this single rigid trunk.
    discarded = [name for name in body_names if name not in LEG_BODIES + ('pelvis', 'torso')]
    merged = discarded + ['torso']
    centres, masses, inertias = [], [], []
    for name in merged:
        body = body_set.get(name)
        rotation, position = default_transforms[name]
        relative = torso_r.T @ rotation
        centre = torso_r.T @ (position + rotation @ np.array(_vector(body.getMassCenter())) - torso_p)
        moments = _vector(body.getInertia().getMoments())
        products = _vector(body.getInertia().getProducts())
        inertia = np.array([[moments[0], products[0], products[1]],
                            [products[0], moments[1], products[2]],
                            [products[1], products[2], moments[2]]])
        centres.append(centre); masses.append(body.getMass()); inertias.append(relative @ inertia @ relative.T)
    total = sum(masses)
    centre = sum(mass * point for mass, point in zip(masses, centres)) / total
    inertia = sum(matrix + mass * (np.dot(point - centre, point - centre) * np.eye(3)
                      - np.outer(point - centre, point - centre))
                  for matrix, mass, point in zip(inertias, masses, centres))
    bodies = model.find('BodySet/objects')
    for body in list(bodies):
        if body.get('name') in discarded:
            bodies.remove(body)
        elif body.get('name') == 'torso':
            _numbers(body, 'mass', (total,))
            _numbers(body, 'mass_center', centre)
            _numbers(body, 'inertia', (inertia[0, 0], inertia[1, 1], inertia[2, 2],
                                     inertia[0, 1], inertia[0, 2], inertia[1, 2]))
    kept_joints = {'ground_pelvis'} | {name + '_' + side for side in ('r', 'l')
        for name in ('hip', 'walker_knee', 'patellofemoral', 'ankle', 'subtalar', 'mtp')}
    if kept_joints - {joint.get('name') for joint in joints}:
        raise GaitDynamicsError('De beengewrichten wijken af van het ondersteunde Pose2Sim-model.')
    for joint in list(joints):
        if joint.get('name') not in kept_joints:
            joints.remove(joint)
    # GaitDynamics assumes a PinJoint Z axis at the ankle. Re-express the
    # original oblique-axis CustomJoint through rotated offset frames. Copying
    # its angle without this change would produce a different foot orientation.
    for side in ('r', 'l'):
        joint_name, coordinate = 'ankle_' + side, 'ankle_angle_' + side
        parent, child = 'tibia_' + side, 'talus_' + side
        original = source.getJointSet().get(joint_name)
        pr, pp = default_transforms[parent]; cr, cp = default_transforms[child]
        relative = pr.T @ cr
        point = _transform(original.getParentFrame(), state, np)[1]
        point_parent, point_child = pr.T @ (point - pp), cr.T @ (point - cp)
        _set_state(source, state, {coordinate: 1e-5})
        rotated = _transform(body_set.get(child), state, np)[0]
        axis = Rotation.from_matrix(pr.T @ rotated @ relative.T).as_rotvec() / 1e-5
        if abs(np.linalg.norm(axis) - 1) > 1e-5:
            raise GaitDynamicsError('De enkelrotatie kan niet veilig als scharnier worden geconverteerd.')
        axis /= np.linalg.norm(axis)
        helper = np.array([1., 0., 0.]) if abs(axis[0]) < .9 else np.array([0., 1., 0.])
        x = helper - np.dot(helper, axis) * axis; x /= np.linalg.norm(x)
        parent_rotation = np.column_stack((x, np.cross(axis, x), axis))
        child_rot = relative.T @ parent_rotation
        replacement = _joint(joint_name, parent, child, point_parent, point_child,
                             parent_rotation, child_rot, (coordinate,), Rotation, pin=True)
        old = next(j for j in joints if j.get('name') == joint_name)
        index = list(joints).index(old); joints.remove(old); joints.insert(index, replacement)
        _set_state(source, state, {coordinate: 0.})
    joints.append(_joint('back', 'pelvis', 'torso', parent_point, child_point,
                         np.eye(3), child_rotation, COORDINATES[20:], Rotation))
    constraints = model.find('ConstraintSet/objects')
    if constraints is not None:
        for constraint in list(constraints):
            if constraint.findtext('dependent_coordinate_name') not in DEPENDENT_COORDINATES:
                constraints.remove(constraint)
    for tag in ('ForceSet', 'MarkerSet', 'ContactGeometrySet', 'ControllerSet', 'ComponentSet', 'ProbeSet'):
        element = model.find(tag)
        if element is not None:
            element.clear()
    for geometry in model.findall('.//attached_geometry'):
        geometry.clear()
    model.set('name', 'HU3D_GaitDynamics_reduced')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'running', 'method': 'forward-kinematics rigid-trunk reduction',
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'opensim_version': opensim.GetVersion(),
        'input': {'motion': str(motion), 'model': str(model_path), 'sha256': original_hashes},
        'scientific_accuracy_validated': False, 'rows': len(rows), 'source_coordinate_count': len(source_coordinates),
        'target_independent_coordinates': list(COORDINATES), 'units': {'angles': 'degrees', 'translations': 'm', 'time': 's'},
        'limits': {'trunk_position_m': MAX_TRUNK_POSITION_ERROR_M, 'lower_body_position_m': MAX_LOWER_POSITION_ERROR_M,
                   'orientation_rad': MAX_ORIENTATION_ERROR_RAD},
        'pivot_registration': {'fit_frames': list(range(0, len(rows), 2)),
            'held_out_frames': list(range(1, len(rows), 2)),
            'neutral_pose_preserved': True, 'pivot_shift_m': float(np.linalg.norm(delta)),
            'max_allowed_pivot_shift_m': max_pivot_shift,
            'anatomical_pivot_max_trunk_position_error_m': max(initial_errors),
            'parent_point_m': parent_point.tolist(), 'child_point_m': child_point.tolist()},
        'warnings': ['Experimentele kinematische vereenvoudiging, geen klinisch gevalideerde conversie.',
                     'Rugsegmenten, hoofd en armen worden één starre romp; afzonderlijke bewegingen gaan verloren.',
                     'Romp-massa, massamiddelpunt en traagheid zijn samengevoegd in de neutrale houding.',
                     'Het vaste rompdraaipunt is op de beweging afgestemd; lumbar-hoeken zijn equivalente rompstanden, geen afzonderlijke wervelhoeken.',
                     'Dit model heeft geen spieren en is niet geschikt voor het berekenen van gewrichtsmomenten.']}
    report_file = output / 'conversion.json'
    try:
        target_path = output / 'model.osim'
        tree.write(target_path, encoding='utf-8', xml_declaration=True)
        target = opensim.Model(str(target_path)); target_state = target.initSystem()
        source_mass = sum(body_set.get(name).getMass() for name in body_names)
        target_bodies = target.getBodySet()
        target_mass = sum(target_bodies.get(i).getMass() for i in range(target_bodies.getSize()))
        if abs(source_mass - target_mass) > 1e-8:
            raise GaitDynamicsError('Lichaamsmassa is niet behouden in de conversie.')
        # Normalize through OpenSim for parsers using the current model format.
        target.printToXML(str(target_path))
        target_rows, positions, orientations, trunk_errors = [], [], [], []
        previous_lumbar = None
        lower_errors, orientation_errors = [], []
        for row, values in zip(rows, source_values):
            _set_state(source, state, values)
            sr, sp = _transform(body_set.get('pelvis'), state, np)
            tr, tp = _transform(body_set.get('torso'), state, np)
            lumbar = Rotation.from_matrix(sr.T @ tr @ child_rotation).as_euler('ZXY')
            if abs(np.cos(lumbar[1])) < 1e-4:
                raise GaitDynamicsError('Rompstand ligt te dicht bij een Euler-singulariteit.')
            if previous_lumbar is not None:
                lumbar = previous_lumbar + (lumbar - previous_lumbar + np.pi) % (2 * np.pi) - np.pi
            previous_lumbar = lumbar.copy()
            reduced = {name: values[name] for name in COORDINATES[:20]}
            reduced.update(zip(COORDINATES[20:], lumbar))
            _set_state(target, target_state, reduced)
            target_rows.append([row[0]] + [reduced[name] * (1 if name in COORDINATES[3:6] else 180 / np.pi)
                                           for name in COORDINATES])
            frame_positions, frame_orientations = [], []
            for name in BODY_NAMES:
                expected_r, expected_p = _transform(body_set.get(name), state, np)
                actual_r, actual_p = _transform(target_bodies.get(name), target_state, np)
                error_p = float(np.linalg.norm(expected_p - actual_p))
                error_r = float(Rotation.from_matrix(expected_r.T @ actual_r).magnitude())
                (trunk_errors if name == 'torso' else lower_errors).append(error_p)
                orientation_errors.append(error_r)
                frame_positions.append(actual_p.tolist()); frame_orientations.append(actual_r.tolist())
            positions.append(frame_positions); orientations.append(frame_orientations)
        report['validation'] = {'mass_kg': source_mass, 'target_mass_kg': target_mass,
            'max_lower_body_position_error_m': max(lower_errors), 'max_orientation_error_rad': max(orientation_errors),
            'max_trunk_position_error_m': max(trunk_errors), 'rms_trunk_position_error_m': float(np.sqrt(np.mean(np.square(trunk_errors)))),
            'checked_frames': len(rows), 'time_samples_preserved': True, 'ankles_reexpressed_as_pin_joints': True}
        if max(lower_errors) > MAX_LOWER_POSITION_ERROR_M or max(orientation_errors) > MAX_ORIENTATION_ERROR_RAD:
            raise GaitDynamicsError('Beenbeweging of oriëntatie is niet behouden; conversie afgewezen.')
        if max(trunk_errors) > MAX_TRUNK_POSITION_ERROR_M:
            raise GaitDynamicsError(f'Rompafwijking {max(trunk_errors)*1000:.1f} mm overschrijdt de grens van 30 mm; conversie afgewezen.')
        motion_path = output / 'motion.mot'
        motion_path.write_text('HU3D GaitDynamics conversion\nversion=1\n' +
            f'nRows={len(rows)}\nnColumns=24\ninDegrees=yes\nendheader\n' +
            '\t'.join(('time', *COORDINATES)) + '\n' +
            '\n'.join('\t'.join(format(value, '.17g') for value in row) for row in target_rows) + '\n', encoding='utf-8')
        checked = inspect_inputs(motion_path, target_path)
        if not checked['compatible']:
            raise GaitDynamicsError('\n'.join(checked['errors']))
        if original_hashes != {'motion': sha256(motion), 'model': sha256(model_path)}:
            raise GaitDynamicsError('Bronbestanden zijn tijdens de conversie gewijzigd.')
        # Independent cross-parser/FK validation in the Linux runtime uses these
        # transforms; do not assume that matching coordinate names imply axes.
        reference = output / 'kinematics-reference.json'
        reference.write_text(json.dumps({'bodies': BODY_NAMES, 'positions_m': positions,
            'orientations': orientations, 'times': [row[0] for row in rows]}, allow_nan=False), encoding='utf-8')
        report.update(status='completed', compatibility=checked, artifacts={
            path.name: sha256(path) for path in (target_path, motion_path, reference)})
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        report_file.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description='Pose2Sim-model omzetten voor GaitDynamics')
    for name in ('motion', 'model', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = convert_model(args.motion, args.model, args.output)
        print(json.dumps(report['validation']), flush=True)
        return 0
    except Exception as error:
        print(f'Modelconversie mislukt: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
