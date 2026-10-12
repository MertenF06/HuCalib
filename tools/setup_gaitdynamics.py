"""Download the pinned official inference runtime and verify all files."""
import argparse
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mocap_app.analysis.gaitdynamics import UPSTREAM_COMMIT, SOURCE_SHA256, MODEL_HASHES, sha256


def setup(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    files = {'gait_dynamics.py': (SOURCE_SHA256, 'raw', 'example_usage/gait_dynamics.py'),
             'LICENSE': ('6bc1614dbbe8f8254d7176a27f43f9633f678672bf4f4cd97f468354d02189d6', 'raw', 'LICENSE')}
    files.update({name: (checksum, 'media', 'example_usage/' + name) for name, checksum in MODEL_HASHES.items()})
    for name, (checksum, host, remote) in files.items():
        existing = output / name
        if existing.exists():
            if sha256(existing) != checksum:
                raise ValueError(f'Bestaand bestand heeft een onjuiste checksum: {name}. Verplaats dit bestand en probeer opnieuw.')
            print(f'{name}: reeds aanwezig, checksum gecontroleerd', flush=True)
            continue
        url = (f'https://raw.githubusercontent.com/stanfordnmbl/GaitDynamics/{UPSTREAM_COMMIT}/{remote}'
               if host == 'raw' else f'https://media.githubusercontent.com/media/stanfordnmbl/GaitDynamics/{UPSTREAM_COMMIT}/{remote}')
        temporary = output / (name + '.download')
        with urllib.request.urlopen(url, timeout=45) as source, temporary.open('wb') as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        if sha256(temporary) != checksum:
            temporary.unlink()
            raise ValueError(f'Onjuiste checksum: {name}')
        temporary.rename(output / name)
        print(f'{name}: checksum gecontroleerd', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    setup(parser.parse_args().output)
