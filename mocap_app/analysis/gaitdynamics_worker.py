"""Execute the checksum-pinned upstream inference in a separate Linux process."""
import ast
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

from mocap_app.analysis.gaitdynamics import COORDINATES, prepare_inference_motion, read_motion, verify_runtime


def validate_kinematics(namespace, skeleton, motion, reference=None):
    """Check axes, topology and the upstream polynomial FK approximations.

    Same coordinate names alone are not enough. This gate runs for every model,
    including externally supplied 23-coordinate models, before AI inference.
    """
    import numpy as np
    import torch
    from scipy.spatial.transform import Rotation
    pelvis = skeleton.getBodyNode('pelvis')
    if [pelvis.getChildJoint(i).getChildBodyNode().getName()
            for i in range(pelvis.getNumChildJoints())] != ['femur_r', 'femur_l', 'torso']:
        raise RuntimeError('Onverwachte bekken-topologie voor GaitDynamics.')
    names, rows, header = read_motion(motion)
    q = np.array([[row[names.index(name)] for name in COORDINATES] for row in rows])
    if header['inDegrees'] == 'yes':
        q[:, [i for i in range(23) if i not in (3, 4, 5)]] *= np.pi / 180
    with torch.no_grad():
        feet, _, _, rotations = namespace['forward_kinematics'](
            torch.tensor(q, dtype=torch.float32), namespace['get_model_offsets'](skeleton).float())
    feet, rotations = feet[:, 0].numpy(), rotations[:, 0].numpy()
    bodies = ('pelvis', 'femur_r', 'tibia_r', 'talus_r', 'calcn_r',
              'femur_l', 'tibia_l', 'talus_l', 'calcn_l', 'torso')
    max_foot, max_angle, max_reference_position, max_reference_angle = 0., 0., 0., 0.
    expected = json.loads(reference.read_text()) if reference is not None else None
    if expected and (expected['bodies'] != list(bodies) or expected['times'] != [row[0] for row in rows]):
        raise RuntimeError('Conversiereferentie hoort niet bij deze beweging.')
    for i, values in enumerate(q):
        skeleton.setPositions(values)
        for j, name in enumerate(bodies):
            transform = skeleton.getBodyNode(name).getWorldTransform()
            r, p = transform.rotation(), transform.translation()
            max_angle = max(max_angle, float(Rotation.from_matrix(r.T @ rotations[j, i]).magnitude()))
            if name in ('calcn_r', 'calcn_l'):
                max_foot = max(max_foot, float(np.linalg.norm(p - feet[0 if name.endswith('_r') else 2, i])))
            if expected:
                max_reference_position = max(max_reference_position,
                    float(np.linalg.norm(p - np.array(expected['positions_m'][i][j]))))
                max_reference_angle = max(max_reference_angle, float(Rotation.from_matrix(
                    np.array(expected['orientations'][i][j]).T @ r).magnitude()))
    result = {'checked_frames': len(rows), 'upstream_max_foot_position_error_m': max_foot,
        'upstream_max_body_orientation_error_degrees': max_angle * 180 / np.pi,
        'upstream_limits': {'position_m': .015, 'orientation_degrees': 5},
        'conversion_reference_checked': expected is not None,
        'opensim_nimble_max_position_error_m': max_reference_position if expected else None,
        'opensim_nimble_max_orientation_error_rad': max_reference_angle if expected else None}
    if max_foot > .015 or max_angle > np.deg2rad(5):
        raise RuntimeError('GaitDynamics-gewrichtsassen of voetkinematica wijken te veel af: ' + json.dumps(result))
    if expected and (max_reference_position > 1e-5 or max_reference_angle > 1e-5):
        raise RuntimeError('OpenSim en nimblephysics interpreteren het geconverteerde model verschillend: ' + json.dumps(result))
    return result


def main():
    if sys.platform != 'linux':
        raise RuntimeError('Deze GaitDynamics-runtime vereist Linux of WSL2; native Windows wordt niet ondersteund.')
    output, runtime = (Path(value).resolve() for value in sys.argv[1:3])
    verify_runtime(runtime)
    report = json.loads((output / 'gaitdynamics.json').read_text())
    import torch
    import nimblephysics as nimble
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    if torch.cuda.is_available():
        raise RuntimeError('Deze experimentele koppeling vereist CPU-verwerking.')
    source = runtime / 'gait_dynamics.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    # The official Colab script prompts for input even on import. Keep the
    # pinned definitions, replacing only the two interactive entry statements.
    removed = 0
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'usr_inputs':
            removed += 1
        elif isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'":
            removed += 1
        else:
            body.append(node)
    if removed != 2:
        raise RuntimeError('Onverwachte upstream-entrypoint; runtime wordt niet uitgevoerd.')
    tree.body = body
    namespace = {'__name__': '__main__', '__file__': str(source)}
    exec(compile(tree, str(source), 'exec'), namespace)
    # The verified official checkpoints serialize these two scaler classes
    # as __main__ objects. Register their original definitions for torch.load.
    for name in ('Normalizer', 'MinMaxScaler'):
        setattr(sys.modules['__main__'], name, namespace[name])
    opt = namespace['parse_opt']()
    opt.subject_data_path = str(output / 'inputs')
    opt.file_paths = [str(output / 'inputs/motion.mot')]
    opt.height_m, opt.weight_kg = report['height_m'], report['mass_kg']
    opt.treadmill_speed = report['treadmill_speed_m_s']
    opt.batch_size_inference = 4
    opt.checkpoint_bl = str(runtime / 'GaitDynamicsRefinement.pt')
    model_path = output / 'inputs/model.osim'
    import xml.etree.ElementTree as ET
    model = ET.parse(model_path)
    # Meshes are visualization assets; joint transforms and body dimensions
    # remain unchanged. This avoids fetching geometry for numerical inference.
    for element in model.findall('.//attached_geometry'):
        element.clear()
    numerical_model = output / 'inputs/model-numerical.osim'
    model.write(numerical_model, encoding='utf-8', xml_declaration=True)
    opt.subject_osim_model = str(numerical_model)
    opt.geometry_folder = str(output / 'inputs/Geometry')
    skeleton = nimble.biomechanics.OpenSimParser.parseOsim(opt.subject_osim_model, opt.geometry_folder).skeleton
    dofs = [dof.getName() for dof in skeleton.getDofs()]
    if dofs != list(COORDINATES):
        raise RuntimeError('De onafhankelijke modelcoördinaten of hun volgorde zijn niet GaitDynamics-compatibel: ' + str(dofs))
    reference = output / 'inputs/kinematics-reference.json'
    kinematics_check = validate_kinematics(namespace, skeleton, output / 'inputs/motion.mot',
                                         reference if reference.is_file() else None)
    inference_motion = output / 'inputs/inference/motion.mot'
    preprocessing = prepare_inference_motion(output / 'inputs/motion.mot', inference_motion)
    inference_kinematics_check = validate_kinematics(namespace, skeleton, inference_motion)
    opt.file_paths = [str(inference_motion)]
    (output / 'kinematics-check.json').write_text(json.dumps(kinematics_check, indent=2), encoding='utf-8')
    namespace['opt'] = opt
    os.chdir(output / 'inputs')
    with torch.no_grad():
        namespace['predict_grf_and_missing_kinematics']()
    result = output / 'inputs/motion_grf_pred___.mot'
    # Upstream writes nColumns=9 although two feet produce 19 columns. Correct
    # this metadata explicitly and retain the original header in the report.
    lines = result.read_text().splitlines()
    original_columns = next(line for line in lines if line.startswith('nColumns='))
    lines = ['nColumns=19' if line.startswith('nColumns=') else line for line in lines]
    result.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    read_motion(result)
    (output / 'environment.json').write_text(json.dumps({
        'python': sys.version, 'platform': sys.platform, 'torch': torch.__version__,
        'nimblephysics': version('nimblephysics'), 'device': 'cpu',
        'numpy': version('numpy'), 'pandas': version('pandas'), 'scipy': version('scipy'),
        'upstream_original_column_header': original_columns,
        'visual_meshes_removed_for_numerical_inference': True,
        'kinematics_check': kinematics_check,
        'inference_kinematics_check': inference_kinematics_check,
        'preprocessing': preprocessing,
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
