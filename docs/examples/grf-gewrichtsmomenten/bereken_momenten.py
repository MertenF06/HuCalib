"""Standalone calculation of the GRF moment about OpenSim joint frames.

This is not inverse dynamics. No application files are modified.
Requires OpenSim 4.6, NumPy, SciPy and matplotlib.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
import opensim
from scipy.spatial.transform import Rotation
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


def read_mot(path):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    end = next(i for i, line in enumerate(lines) if line.strip() == 'endheader')
    header = dict(line.split('=', 1) for line in lines[:end] if '=' in line)
    names = lines[end + 1].split()
    values = np.loadtxt(lines[end + 2:])
    assert values.shape[1] == len(names) and len(set(names)) == len(names)
    assert np.isfinite(values).all() and np.all(np.diff(values[:, 0]) > 0)
    assert len(values) == int(header['nRows'])
    if 'nColumns' in header:
        assert len(names) == int(header['nColumns'])
    return header, names, values


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pose(model, state, names, row):
    state.setTime(float(row[0]))
    for name, value in zip(names[1:], row[1:]):
        model.getCoordinateSet().get(name).setValue(state, float(value), False)
    model.realizePosition(state)


def transform(frame, state):
    value = frame.getTransformInGround(state)
    p = np.array([value.p().get(i) for i in range(3)])
    r = np.array([[value.R().get(i, j) for j in range(3)] for i in range(3)])
    return p, r


def save_csv(path, names, values):
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(names)
        for row in values:
            writer.writerow(['' if np.isnan(value) else format(float(value), '.12g') for value in row])


def plot_moments(output, time, data, title, file_stem, local):
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 12,
        'axes.titlesize': 14, 'axes.spines.top': False, 'axes.spines.right': False,
        'pdf.fonttype': 42, 'svg.fonttype': 'none'})
    fig, axes = plt.subplots(3, 3, figsize=(15, 11), sharex=True)
    colors = ['#0072B2', '#D55E00']
    labels = ['Heup', 'Knie', 'Enkel']
    for joint in range(3):
        for axis in range(3):
            ax = axes[joint, axis]
            for side in range(2):
                ax.plot(time, data[joint, side, :, axis], color=colors[side],
                        linestyle='-' if side == 0 else '--', lw=2)
            ax.set_title(f'{labels[joint]} · {"lokale model-as" if local else "grond-as"} {"XYZ"[axis]}', loc='left', pad=10)
            ax.set_ylabel('Nm')
            ax.axhline(0, color='#586170', lw=.7, alpha=.45)
            ax.grid(alpha=.20)
            ax.set_xlim(time[0], time[-1])
            if joint == 2:
                ax.set_xlabel('Tijd (s)')
    fig.suptitle(title, x=.065, y=.974, ha='left', fontsize=19, fontweight='bold')
    fig.text(.065, .941, 'Bijdrage van voorspelde grondreactiekrachten · heup, knie en enkel · rechter- en linkerbeen',
             color='#586170', fontsize=12)
    fig.legend([Line2D([], [], color=colors[0], lw=2), Line2D([], [], color=colors[1], lw=2, linestyle='--')],
               ['Rechts', 'Links'], loc='upper left', bbox_to_anchor=(.059, .929), ncol=2, frameon=False)
    fig.text(.065, .035, 'M = (CoP − gewrichtsreferentiepunt) × F. Onderbrekingen: verticale voetkracht < 20 N; CoP is daar niet bruikbaar.',
             fontsize=10, color='#586170')
    fig.text(.065, .017, 'Modelvoorspelling, geen netto gewrichtsmomenten. Vrije contactmomenten zijn onbekend. Tekens volgen de modelassen, zonder links/rechts-spiegeling.',
             fontsize=10, color='#586170')
    fig.subplots_adjust(left=.065, right=.98, bottom=.10, top=.865, hspace=.40, wspace=.27)
    for extension in ('png', 'pdf', 'svg'):
        fig.savefig(output / f'{file_stem}.{extension}', dpi=180, facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forces', type=Path, required=True)
    parser.add_argument('--motion', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--numerical-model', type=Path, required=True)
    parser.add_argument('--duplicate-forces', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = [args.forces, args.motion, args.model, args.numerical_model]
    before = {p.name: digest(p) for p in inputs}
    if args.duplicate_forces:
        assert digest(args.duplicate_forces) == digest(args.forces), 'Force exports differ'
    _, fnames, forces = read_mot(args.forces)
    mheader, mnames, motion = read_mot(args.motion)
    assert mheader['inDegrees'] in ('yes', 'no')
    columns = dict(zip(fnames, forces.T))
    times = columns['time']
    step = np.median(np.diff(times))
    assert abs(step - .01) < 1e-8 and np.allclose(np.diff(times), .01, rtol=0, atol=1e-8)
    # Reconstruct the unrounded 100 Hz inference grid. The output MOT stores
    # times to 5 decimals; validate that it describes precisely this grid.
    grid = motion[0, 0] + np.arange(len(times)) / 100
    assert np.max(np.abs(grid - times)) <= 5.01e-6
    assert grid[0] >= motion[0, 0] and grid[-1] <= motion[-1, 0]
    values = motion.copy()
    for i, name in enumerate(mnames[1:], 1):
        if name not in ('pelvis_tx', 'pelvis_ty', 'pelvis_tz'):
            if mheader['inDegrees'] == 'yes':
                values[:, i] = np.deg2rad(values[:, i])
            values[:, i] = np.unwrap(values[:, i])
    samples = np.column_stack([grid] + [np.interp(grid, motion[:, 0], values[:, i]) for i in range(1, len(mnames))])
    model = opensim.Model(str(args.numerical_model)); state = model.initSystem()
    # Suppress visual assets only in a separate analysis copy, then compare
    # both supplied models' joint positions and rotations at every time.
    tree = ET.parse(args.model)
    for element in tree.findall('.//attached_geometry'):
        element.clear()
    comparison_path = args.output / 'model-controle-zonder-meshes.osim'
    tree.write(comparison_path, encoding='utf-8', xml_declaration=True)
    original = opensim.Model(str(comparison_path)); original_state = original.initSystem()
    positions = np.empty((3, 2, len(times), 3))
    rotations = np.empty((3, 2, len(times), 3, 3))
    gaps = np.empty((3, 2, len(times)))
    max_model_position_error = max_model_rotation_error = 0.
    for i, row in enumerate(samples):
        pose(model, state, mnames, row)
        pose(original, original_state, mnames, row)
        for j, name in enumerate(('hip', 'walker_knee', 'ankle')):
            for side, code in enumerate(('r', 'l')):
                joint = model.getJointSet().get(name + '_' + code)
                other = original.getJointSet().get(name + '_' + code)
                p, r = transform(joint.getParentFrame(), state)
                p0, r0 = transform(other.getParentFrame(), original_state)
                positions[j, side, i], rotations[j, side, i] = p, r
                child_position, _ = transform(joint.getChildFrame(), state)
                gaps[j, side, i] = np.linalg.norm(child_position - p)
                max_model_position_error = max(max_model_position_error, float(np.linalg.norm(p - p0)))
                max_model_rotation_error = max(max_model_rotation_error, float(np.linalg.norm(r - r0)))
    assert max_model_position_error < 1e-8 and max_model_rotation_error < 1e-8
    assert np.allclose(np.einsum('...ji,...jk->...ik', rotations, rotations), np.eye(3), atol=1e-10)
    assert np.allclose(np.linalg.det(rotations), 1, atol=1e-10)
    force = np.array([np.column_stack([columns[f'force{side}_v{a}'] for a in 'xyz']) for side in (1, 2)])
    cop = np.array([np.column_stack([columns[f'force{side}_p{a}'] for a in 'xyz']) for side in (1, 2)])
    torque = np.array([np.column_stack([columns[f'torque{side}_{a}'] for a in 'xyz']) for side in (1, 2)])
    assert np.all(torque == 0), 'This analysis assumes unknown free moments are omitted'
    contact = force[:, :, 1] >= 20.
    arm = cop[np.newaxis] - positions
    world = np.cross(arm, force[np.newaxis])
    local = np.einsum('...ji,...j->...i', rotations, world)
    # Independent algebraic checks on the calculation and frame projection.
    assert np.allclose(np.linalg.norm(world, axis=-1), np.linalg.norm(local, axis=-1), atol=1e-9)
    assert np.max(np.abs(np.einsum('...i,...i->...', world, force[np.newaxis]))) < 1e-8
    shift = np.array([2.1, -.4, 3.5])
    assert np.allclose(world, np.cross((cop[np.newaxis] + shift) - (positions + shift), force[np.newaxis]), atol=1e-9)
    arbitrary_rotation = Rotation.from_rotvec([.3, -.5, .2]).as_matrix()
    assert np.allclose(world @ arbitrary_rotation.T,
        np.cross(arm @ arbitrary_rotation.T, force[np.newaxis] @ arbitrary_rotation.T), atol=1e-9)
    for other in (1, 2):
        assert np.allclose(world[other] - world[0], np.cross(positions[0] - positions[other], force), atol=1e-9)
    raw_local = local.copy()
    for side in range(2):
        local[:, side, ~contact[side], :] = np.nan
        world[:, side, ~contact[side], :] = np.nan
    labels = ['heup', 'knie', 'enkel']
    sides = ['rechts', 'links']
    moment_names = [f'{labels[j]}_{sides[s]}_M{a}_Nm' for j in range(3) for s in range(2) for a in 'xyz']
    for name, matrix in [('grf-momenten-lokaal', local), ('grf-momenten-globaal', world), ('grf-momenten-lokaal-ruw', raw_local)]:
        save_csv(args.output / f'{name}.csv', ['tijd_s', *moment_names],
                 np.column_stack((times, matrix.transpose(2, 0, 1, 3).reshape(len(times), -1))))
    position_names = [f'{labels[j]}_{sides[s]}_{a}_m' for j in range(3) for s in range(2) for a in 'xyz']
    save_csv(args.output / 'gewrichtsreferentiepunten.csv', ['tijd_s', *position_names, 'Fy_rechts_N', 'Fy_links_N', 'contact_rechts', 'contact_links'],
        np.column_stack((times, positions.transpose(2, 0, 1, 3).reshape(len(times), -1), force[:, :, 1].T, contact.T)))
    np.savez_compressed(args.output / 'gewrichtsframes.npz', tijd_s=times, kinematica_tijd_s=grid,
                        positions_ground_m=positions, rotations_parent_to_ground=rotations)
    report = {'method': 'GRF contribution: (CoP - joint parent frame origin) cross ipsilateral force',
        'joint_origin': 'origin of the OpenSim joint parent PhysicalOffsetFrame',
        'joint_axes': 'orthonormal XYZ axes of that same parent joint frame, right hand rule; no side mirroring',
        'clinical_flexion_adduction_rotation_labels_assigned': False,
        'net_joint_moments_or_inverse_dynamics': False,
        'gravity_inertia_muscles_other_external_loads_included': False,
        'free_contact_moment': 'unknown; omitted because the GaitDynamics torque columns are placeholders equal to zero',
        'units': {'moment': 'Nm', 'position': 'm', 'force': 'N', 'time': 's'},
        'input_sha256': before, 'opensim_version': opensim.GetVersionAndDate(),
        'force_export_duplicates_identical': bool(args.duplicate_forces),
        'motion_rows': len(motion), 'force_rows': len(forces), 'sampling_rate_hz': 100,
        'time_range_s': [float(times[0]), float(times[-1])],
        'time_alignment': 'actual-time linear coordinate interpolation, radians unwrapped, exact 100 Hz inference grid',
        'max_force_timestamp_rounding_s': float(np.max(np.abs(times - grid))),
        'extrapolation': False, 'contact_threshold_Fy_N': 20,
        'threshold_basis': 'pinned GaitDynamics inverse_norm_cops zeros CoP when Fy < 20 N',
        'invalid_CoP_samples': {sides[s]: int(np.sum(~contact[s])) for s in range(2)},
        'csv_missing_values': 'blank cells are undefined, not zero',
        'plots': 'no filtering; no-contact samples omitted',
        'models_verified_kinematically_equivalent': True,
        'max_model_joint_position_difference_m': max_model_position_error,
        'max_model_joint_rotation_matrix_difference': max_model_rotation_error,
        'max_parent_child_frame_origin_distance_m': {f'{labels[j]}_{sides[s]}': float(np.max(gaps[j, s])) for j in range(3) for s in range(2)},
        'validation': {'orthonormal_frames': True, 'moment_norm_preserved': True,
            'moment_perpendicular_to_force': True, 'global_translation_invariance': True,
            'global_rotation_covariance': True, 'moment_shift_between_joint_points': True},
        'ranges_local_Nm': {moment_names[k]: [float(np.nanmin(local.reshape(6, len(times), 3)[k // 3, :, k % 3])),
                                               float(np.nanmax(local.reshape(6, len(times), 3)[k // 3, :, k % 3]))] for k in range(18)},
        'limitations': ['Forces and CoP are predictions, not measurements; moment accuracy is not validated.',
            'Local model XYZ components are not automatically generalized coordinate loads or anatomical clinical joint moments.',
            'The knee is a coupled joint with translation; the fixed parent joint frame origin is an explicit reference, not a unique instantaneous knee rotation centre.',
            'Low but nonzero predicted forces can still yield unstable CoP above the 20 N cutoff.']}
    (args.output / 'berekeningsrapport.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    plot_moments(args.output, times, local, 'Externe GRF-momenten rond de lokale gewrichtsassen', 'grf-momenten-lokaal', True)
    plot_moments(args.output, times, world, 'Externe GRF-momenten rond de globale grondassen', 'grf-momenten-globaal', False)
    assert before == {p.name: digest(p) for p in inputs}, 'Uploaded input modified'
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
