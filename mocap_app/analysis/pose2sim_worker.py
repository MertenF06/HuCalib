"""Entry point inside the separately installed Pose2Sim environment."""
from importlib.metadata import version
import json
from pathlib import Path
import sys


def main():
    root, stage = Path(sys.argv[1]), sys.argv[2]
    from mocap_app.analysis.pose2sim import STAGES
    if stage not in STAGES:
        raise ValueError('Unknown analysis stage')
    from Pose2Sim import Pose2Sim
    import rtoml
    config = json.loads((root / 'effective-config.json').read_text(encoding='utf-8'))
    rtoml.dump(config, root / 'project' / 'Config.toml')
    (root / 'environment.json').write_text(json.dumps({
        'python': sys.version, 'executable': sys.executable,
        'pose2sim': version('pose2sim'), 'onnxruntime': version('onnxruntime'),
        'device': 'cpu', 'backend': 'onnxruntime',
    }, indent=2), encoding='utf-8')
    getattr(Pose2Sim, stage)(config)


if __name__ == '__main__':
    main()
