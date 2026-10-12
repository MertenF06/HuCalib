"""Import the original synchronized demo at its original FPS and analyse it.

This creates a new test session; it does not relabel encoder test recordings as
scientific input or change the official calibration's known width mismatch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mocap_app.analysis.pose2sim import run_analysis


from mocap_app.analysis.demo import import_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--assume-synchronized', action='store_true')
    args = parser.parse_args()
    import_demo(args.demo, args.calibration, args.session)
    report = run_analysis(args.session, args.output, args.demo / 'Config.toml', args.python,
                          diagnostic_resolution_mismatch=True, assume_synchronized=args.assume_synchronized)
    assert report['status'] == 'completed'
    assert len(report['inputs']) == 4
    assert all(v['decoded_frames'] == 100 and abs(v['fps'] - 60) < .01 for v in report['inputs'])
    print(json.dumps({'status': report['status'], 'stages': report['stages'],
                      'artifacts': report['artifacts'], 'warnings': report['warnings']}, indent=2))


if __name__ == '__main__':
    main()
