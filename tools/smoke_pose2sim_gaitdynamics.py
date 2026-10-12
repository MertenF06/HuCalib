"""Real four-camera demo -> Pose2Sim -> model conversion -> CPU GaitDynamics."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mocap_app.analysis.pose2sim import run_analysis as pose_analysis
from mocap_app.analysis.model_conversion import convert_model
from mocap_app.analysis.gaitdynamics import run_analysis as gait_analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('demo', 'output', 'runtime', 'gait-python'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    root = args.output.absolute()
    root.mkdir(parents=True, exist_ok=False)
    pose = root / 'pose2sim'
    report = pose_analysis(args.demo / 'opname', pose, args.demo / 'Config.toml',
                           Path(sys.executable), diagnostic_resolution_mismatch=True)
    motions = [pose / entry['path'] for entry in report['result_summary'] if 'coordinates' in entry]
    models = [pose / entry['path'] for entry in report['artifacts'] if entry['path'].endswith('.osim')]
    if len(motions) != 1 or len(models) != 1:
        raise RuntimeError(f'Onverwachte model-/bewegingskeuze: {motions}, {models}')
    conversion = convert_model(motions[0], models[0], root / 'conversion')
    gait = gait_analysis(root / 'conversion/motion.mot', root / 'conversion/model.osim',
        root / 'gaitdynamics', args.runtime, args.gait_python,
        height_m=1.75, mass_kg=conversion['validation']['mass_kg'], treadmill_speed=0)
    if gait['environment']['kinematics_check']['checked_frames'] != conversion['rows'] or not gait['environment']['kinematics_check']['conversion_reference_checked']:
        raise RuntimeError('Conversie of uitvoer heeft niet alle frames onafhankelijk gecontroleerd.')
    validation = {'status': 'completed', 'pose2sim_video_inputs_tested': True,
        'pose2sim_input_cameras': len(report['inputs']),
        'pose2sim_source_coordinates': conversion['source_coordinate_count'],
        'converted_coordinates': len(conversion['target_independent_coordinates']),
        'frames': conversion['rows'], 'conversion': conversion['validation'],
        'gait_kinematics': gait['environment']['kinematics_check'], 'gait_result': gait['result'],
        'gait_time_interpolation': gait['environment']['preprocessing'],
        'device': gait['environment']['device'], 'height_m': 1.75,
        'height_is_demo_assumption': True, 'mass_from_pose2sim_scaled_model_kg': conversion['validation']['mass_kg'],
        'calibration_resolution_mismatch_diagnostic': True,
        'scientific_accuracy_validated': False, 'native_windows_tested': False, 'wsl2_tested': False}
    (root / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps(validation, indent=2), flush=True)


if __name__ == '__main__':
    main()
