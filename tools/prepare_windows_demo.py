"""Prepare a pinned, checksum-verified Pose2Sim demo for a portable test kit."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mocap_app.analysis.demo import import_demo

COMMIT = '68304b47195dac80bc341cdfd9669140609358c4'
HASHES = {
    'videos/cam01.mp4': 'cf4f2816c86bea4bfbea255333d8626ff32a3ecabdab8b0fb147cea9167fde3a',
    'videos/cam02.mp4': '94ff87cf978b8406e8aa36ab3c5362e72848994e87652f0dc7a33c315e8e0442',
    'videos/cam03.mp4': 'b8b329f832f178f27ecb78d024d5026b96c6adb36c387bfe2c0af85eab8a11ca',
    'videos/cam04.mp4': 'b5c0e7863fd70d008e7178d3c556b045b0ea5f78fcd61ca548a0b2e807e641f7',
}


def prepare_demo(output: Path, source: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    originals = output / 'original'
    for name, expected in HASHES.items():
        target = originals / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source:
            shutil.copy2(source / name, target)
        else:
            url = f'https://raw.githubusercontent.com/perfanalytics/pose2sim/{COMMIT}/Pose2Sim/Demo_SinglePerson/{name}'
            with urllib.request.urlopen(url, timeout=120) as response, target.open('wb') as stream:
                shutil.copyfileobj(response, stream)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Demo checksum mismatch: {name}')
    config = originals / 'Config.toml'
    if source:
        shutil.copy2(source / 'Config.toml', config)
    else:
        url = f'https://raw.githubusercontent.com/perfanalytics/pose2sim/{COMMIT}/Pose2Sim/Demo_SinglePerson/Config.toml'
        with urllib.request.urlopen(url, timeout=120) as response:
            config.write_bytes(response.read())
    fixtures = Path(__file__).resolve().parents[1] / 'testdata/pose2sim-demo'
    if hashlib.sha256(config.read_bytes()).hexdigest() != '88c0324e4625b0977abc1bfc48542522a6b4efddb1e48542c67cebfe29dfd084':
        raise ValueError('Demo configuration checksum mismatch')
    if hashlib.sha256((fixtures / 'Calib_qualisys.toml').read_bytes()).hexdigest() != '4d2ac5ffc43224b6d8704038d73cc2ce576e4c2857375a2f0dc8535e508935f8':
        raise ValueError('Demo calibration checksum mismatch')
    import_demo(originals, fixtures / 'Calib_qualisys.toml', output / 'opname')
    shutil.copy2(config, output / 'Config.toml')
    shutil.copy2(fixtures / 'NOTICE.md', output / 'HERKOMST.md')
    shutil.copy2(fixtures / 'LICENSE-Pose2Sim', output / 'LICENSE-Pose2Sim')
    shutil.rmtree(originals)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, help='Existing official demo; otherwise download the pinned files')
    args = parser.parse_args()
    prepare_demo(args.output, args.source)
    print(f'Demo prepared: {args.output / "opname"}')
