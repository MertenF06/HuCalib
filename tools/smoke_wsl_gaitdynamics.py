"""Exercise the exact WSL exec/payload adapter on Linux with real CPU inference.

This tests the mounted-file transport and result verification, not WSL boot,
Windows elevation or hardware virtualization. Those need a Windows computer.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mocap_app.analysis import gaitdynamics_wsl as bridge
from mocap_app.analysis.gaitdynamics import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--motion', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='HU3D WSL transport ') as temporary:
        root = Path(temporary)
        prefix = root / 'linux-home'
        prefix.mkdir()
        (prefix / 'venv').symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
        (prefix / 'runtime').symlink_to(args.runtime.absolute(), target_is_directory=True)
        identity = bridge.payload_id()
        app = prefix / ('app-' + identity)
        shutil.copytree(Path(__file__).resolve().parents[1] / 'mocap_app', app / 'mocap_app', ignore=shutil.ignore_patterns('__pycache__'))
        inputs = root / "Windows map — met ' spaties"
        inputs.mkdir()
        motion, model = inputs / args.motion.name, inputs / args.model.name
        shutil.copy2(args.motion, motion)
        shutil.copy2(args.model, model)
        for name in ('conversion.json', 'kinematics-reference.json'):
            if (args.motion.parent / name).is_file():
                shutil.copy2(args.motion.parent / name, inputs / name)
        hashes = sha256(motion), sha256(model)
        def linux_exec(arguments, **options):
            if arguments[:3] == ['wsl.exe', '--list', '--verbose']:
                return subprocess.CompletedProcess(arguments, 0, '* Ubuntu-24.04 Running 2\n'.encode('utf-16-le'))
            command = arguments[arguments.index('--exec') + 1:]
            if command[0] == 'wslpath':
                return subprocess.CompletedProcess(arguments, 0, str(Path(command[-1]).absolute()) + '\n')
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
            result.stdout = bridge.decode_output(result.stdout)
            if result.stdout.strip():
                print(result.stdout[-3000:], flush=True)
            return result
        backend = bridge.WslBackend(print, runner=linux_exec, state_dir=root / 'state')
        with patch.object(bridge, 'PREFIX', str(prefix)), patch.object(bridge.sys, 'platform', 'win32'):
            result = backend.analyze(motion, model, args.output, 1.75, 70, 0)
        if hashes != (sha256(motion), sha256(model)):
            raise RuntimeError('Original inputs changed during transport')
        if result['status'] != 'completed':
            raise RuntimeError('Transport did not finish')
        (args.output / 'transport-validation.json').write_text(json.dumps(dict(
            status='passed', real_cpu_inference=True, original_inputs_unchanged=True,
            paths_with_spaces_and_unicode=True, wsl_exec_simulated_on_linux=True,
            windows_wsl_installation_tested=False, hardware_virtualization_tested=False), indent=2))
        print('Real CPU transport passed; Windows/WSL2 installation remains untested.', flush=True)


if __name__ == '__main__':
    main()
