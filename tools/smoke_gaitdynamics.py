"""Real CPU inference on the official complete-kinematics example."""
import argparse
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mocap_app.analysis.gaitdynamics import run_analysis, sha256

SPACE_COMMIT = 'c905e7af6f7a32e8cc99f413ff6559dd897a91b5'
DEMO_HASHES = {
    'example_mot_complete_kinematics.mot': '98fe4d149a70a1780d72166666b68fca042ee91bd461a7597646ae507736bb97',
    'example_opensim_model.osim': '96f54df34ef68ea7f3e9295ae2995dc686521053cc473d88d391d374ebfbb566',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    examples = args.source or args.output.parent / 'examples'
    if not args.source:
        examples.mkdir(parents=True, exist_ok=False)
        for name in DEMO_HASHES:
            with urllib.request.urlopen(f'https://huggingface.co/spaces/alanttan/GaitDynamics/raw/{SPACE_COMMIT}/{name}', timeout=45) as response:
                (examples / name).write_bytes(response.read())
    for name, expected in DEMO_HASHES.items():
        if sha256(examples / name) != expected:
            raise ValueError('Onjuiste voorbeeld-checksum: ' + name)
    # The official example header says 2000 rows, but its data has 499 rows.
    # Correct only the copied header; retain the verified original untouched.
    original = examples / 'example_mot_complete_kinematics.mot'
    lines = original.read_text().splitlines()
    lines = ['nRows=499' if line.strip().startswith('nRows=') else line for line in lines]
    corrected = args.output.parent / 'example-header-corrected.mot'
    corrected.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    # Demonstration values, not asserted demographics for the example subject.
    report = run_analysis(corrected,
        examples / 'example_opensim_model.osim', args.output, args.runtime,
        Path(sys.executable), height_m=1.75, mass_kg=70, treadmill_speed=0)
    if report['result']['rows'] != 499 or report['environment']['device'] != 'cpu':
        raise RuntimeError('Onverwachte proefuitvoer.')
    report['validation'] = {'official_example': True, 'demonstration_anthropometrics': True,
        'pose2sim_inputs_tested': False, 'native_windows_tested': False, 'wsl2_tested': False}
    report['validation'].update(original_motion_sha256=DEMO_HASHES[original.name],
                                original_header_rows=2000, actual_rows=499, header_corrected=True)
    (args.output / 'gaitdynamics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report['result']), flush=True)


if __name__ == '__main__':
    main()
