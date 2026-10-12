"""Windows transport for the verified Linux CPU adapter (no Qt/AI imports)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DISTRIBUTION = 'Ubuntu-24.04'
PREFIX = '/opt/hu3d-gaitdynamics'
USER = 'hu3d-gait'


def decode_output(data):
    if isinstance(data, str):
        return data
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-16-le' if b'\x00' in data else 'utf-8')
    return data.decode(encoding, errors='replace').lstrip('\ufeff')


def payload_root():
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]


def payload_files():
    root = payload_root()
    package = root / ('analysis_runtime/mocap_app' if getattr(sys, 'frozen', False) else 'mocap_app')
    setup = root / 'gaitdynamics_setup' if getattr(sys, 'frozen', False) else root
    return package, setup / 'requirements-gaitdynamics.txt', setup / 'tools/setup_gaitdynamics.py'


def payload_id():
    package, requirements, downloader = payload_files()
    digest = hashlib.sha256()
    paths = sorted((package / 'analysis').glob('gaitdynamics*.py')) + [requirements, downloader]
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class WslBackend:
    def __init__(self, notify=lambda message: None, runner=None, state_dir=None):
        self.notify = notify
        self.runner = runner or self._run
        self.distribution = DISTRIBUTION
        self.state_dir = Path(state_dir or Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'HU3D-kinematics' / 'GaitDynamics')
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _run(self, arguments, timeout=120, input=None):
        self.notify('Uitvoeren: ' + arguments[0] + (' ' + arguments[1] if len(arguments) > 1 else ''))
        result = subprocess.run(arguments, input=input, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result.stdout = decode_output(result.stdout)
        if result.stdout.strip():
            self.notify(result.stdout[-12000:])
        return result

    def _command(self, arguments, timeout=120, input=None):
        result = self.runner(arguments, timeout=timeout, input=input)
        result.stdout = decode_output(result.stdout)
        if result.returncode:
            command = subprocess.list2cmdline(arguments)
            raise RuntimeError(f'Opdracht mislukt ({result.returncode}): {command}\n{result.stdout[-4000:]}')
        return result.stdout

    def _linux(self, arguments, user=USER, timeout=120, input=None):
        return self._command(['wsl.exe', '--distribution', self.distribution, '--user', user, '--exec', *arguments], timeout, input)

    def _state(self, status, **values):
        state = dict(status=status, distribution=self.distribution, **values)
        (self.state_dir / 'setup.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
        return state

    def probe(self):
        self.distribution = DISTRIBUTION
        if sys.platform != 'win32':
            return dict(status='unavailable', message='Deze installatiewizard is voor Windows 10/11 x64.')
        try:
            result = self.runner(['wsl.exe', '--list', '--verbose'], timeout=60, input=None)
        except FileNotFoundError:
            return dict(status='install_required', message='WSL2 is nog niet geïnstalleerd.')
        text = decode_output(result.stdout)
        if result.returncode:
            return dict(status='install_required', message='WSL is nog niet gereed. Installatie of een herstart kan nodig zijn.', diagnostic=text)
        line = next((line for line in text.splitlines() if DISTRIBUTION in line.split()), None)
        if line is None:
            # A distribution's registration name does not establish its OS
            # version. Reuse Ubuntu only after reading its actual release.
            ubuntu = next((entry for entry in text.splitlines() if 'Ubuntu' in entry.split() and entry.split()[-1] == '2'), None)
            if ubuntu is not None:
                release = self.runner(['wsl.exe', '-d', 'Ubuntu', '-u', 'root', '--exec',
                    '/bin/sh', '-c', '. /etc/os-release; printf "%s %s\\n" "$ID" "$VERSION_ID"'], timeout=90, input=None)
                if release.returncode == 0 and decode_output(release.stdout).strip() == 'ubuntu 24.04':
                    self.distribution, line = 'Ubuntu', ubuntu
        if line is None:
            return dict(status='distribution_required', message=f'{DISTRIBUTION} moet nog worden geïnstalleerd.')
        if line.split()[-1] != '2':
            return dict(status='unsupported', message=f'{DISTRIBUTION} gebruikt WSL1. Deze app verandert geen bestaande WSL1-installatie. Gebruik WSL2; zie de installatiehandleiding.')
        try:
            identity = payload_id()
            script = (f"import sys; sys.path.insert(0, '{PREFIX}/app-{identity}'); "
                      f"from mocap_app.analysis.gaitdynamics import verify_runtime; verify_runtime(__import__('pathlib').Path('{PREFIX}/runtime')); "
                      "import torch, nimblephysics; assert not torch.cuda.is_available(); print('HU3D_READY')")
            result = self.runner(['wsl.exe', '-d', self.distribution, '-u', USER, '--exec', f'{PREFIX}/venv/bin/python', '-c', script], timeout=90, input=None)
            if result.returncode == 0 and 'HU3D_READY' in decode_output(result.stdout):
                return dict(status='ready', message='GaitDynamics is gereed voor CPU-verwerking via WSL2.')
        except (OSError, ValueError):
            pass
        return dict(status='environment_required', message='WSL2 is aanwezig. De GaitDynamics-rekenomgeving moet nog worden klaargezet.')

    def update(self):
        if sys.platform != 'win32':
            return dict(status='unavailable', message='WSL bijwerken is alleen beschikbaar op Windows.')
        self.notify('WSL wordt bijgewerkt via Microsoft. Windows kan om beheerderstoestemming vragen. Sluit lopende Docker- en WSL-taken voordat je bijwerkt.')
        help_text = self._command(['wsl.exe', '--help'])
        arguments = "'--update','--web-download'" if '--web-download' in help_text else "'--update'"
        script = ("$ErrorActionPreference='Stop'; $p=Start-Process -FilePath wsl.exe -Verb RunAs "
                  f"-ArgumentList {arguments} -Wait -PassThru; exit $p.ExitCode")
        result = self.runner(['powershell.exe', '-NoProfile', '-Command', script], timeout=1800, input=None)
        if result.returncode not in (0, 3010):
            raise RuntimeError('WSL kon niet worden bijgewerkt: ' + decode_output(result.stdout)[-3000:])
        if result.returncode == 0:
            catalog = self._command(['wsl.exe', '--list', '--online'], timeout=120)
            if not any(DISTRIBUTION in line.split() for line in catalog.splitlines()):
                # Some inbox clients update only the kernel with --update.
                # Open Microsoft's WSL product in the original user's Store,
                # instead of treating a successful kernel update as a fix.
                self._command(['powershell.exe', '-NoProfile', '-Command',
                    "Start-Process 'ms-windows-store://pdp/?ProductId=9P9TQF7MRM4R'"])
                return self._state('store_update_required', message='Microsoft Store is geopend voor Windows Subsystem for Linux. Klik daar op Bijwerken of Installeren. Open daarna de app opnieuw en klik op Installeren / doorgaan. Ubuntu 24.04 is nog niet beschikbaar in jouw WSL-catalogus.')
        return self._state('update_completed', message='De WSL-updateopdracht is voltooid. Herstart Windows als dit is gevraagd; klik daarna op Installeren / doorgaan. De app controleert opnieuw welke distributies beschikbaar zijn.')

    def install(self):
        status = self.probe()
        if status['status'] in ('ready', 'unsupported', 'unavailable'):
            return status
        if status['status'] == 'install_required':
            self.notify('Windows vraagt nu mogelijk beheerderstoestemming voor WSL2 en Ubuntu 24.04. Accepteer die om door te gaan.')
            # Fixed arguments only: no shell interpolation of user paths/data.
            # Only machine-wide WSL components are elevated. Installing a
            # distribution with another admin's credentials would put it in
            # that admin's profile instead of the requesting Windows user's.
            script = "$ErrorActionPreference='Stop'; $p=Start-Process -FilePath wsl.exe -Verb RunAs -ArgumentList '--install','--no-distribution' -Wait -PassThru; exit $p.ExitCode"
            result = self.runner(['powershell.exe', '-NoProfile', '-Command', script], timeout=1800, input=None)
            if result.returncode not in (0, 3010):
                raise RuntimeError('WSL-installatie niet voltooid. Is de Windows-toestemming geaccepteerd? ' + decode_output(result.stdout)[-3000:])
            return self._state('restart_required', message='Herstart Windows als de installatie daarom vraagt. Open daarna HU3D kinematics en klik opnieuw op Installeren / doorgaan. De app controleert wat al klaar is.')
        if status['status'] == 'distribution_required':
            self.notify('Ubuntu 24.04 wordt onder jouw Windows-account geïnstalleerd, zonder interactieve eerste start.')
            # Older WSL clients reject --version on --install, printing their
            # entire help and returning 0xffffffff. Check WSL2 after installing.
            try:
                self._command(['wsl.exe', '--install', '--distribution', DISTRIBUTION, '--no-launch'], timeout=1800)
            except RuntimeError as error:
                if 'WSL_E_DISTRO_NOT_FOUND' not in str(error):
                    raise
                self._command(['wsl.exe', '--list', '--online'], timeout=120)
                return self._state('wsl_update_required', message='Deze WSL-versie biedt Ubuntu 24.04 niet aan. Klik op WSL bijwerken en accepteer eventuele Windows-toestemming. Klik daarna op Installeren / doorgaan.')
            status = self.probe()
            if status['status'] == 'distribution_required':
                # Legacy Store distributions are downloaded by --install but
                # registered only by their launcher. --no-launch skips that.
                # Register Canonical's Ubuntu 24.04 noninteractively as root;
                # do not launch/modify the user's other Ubuntu or Docker.
                self.notify('De Ubuntu 24.04-app is gedownload; de eerste registratie wordt nu afgerond zonder Linux-accountvragen.')
                script = ("$ErrorActionPreference='Stop'; "
                    "$p=Get-AppxPackage -Name 'CanonicalGroupLimited.Ubuntu24.04LTS'; "
                    "if (-not $p) { throw 'Ubuntu 24.04 is nog niet geregistreerd en het Ubuntu-app-pakket is niet gevonden.' }; "
                    "$launchers=@(Get-ChildItem -LiteralPath $p.InstallLocation -Filter 'ubuntu*.exe' -File); "
                    "if ($launchers.Count -ne 1) { throw 'De Ubuntu 24.04-startmodule kon niet eenduidig worden gevonden.' }; "
                    "& ($launchers[0].FullName) install --root; exit $LASTEXITCODE")
                self._command(['powershell.exe', '-NoProfile', '-Command', script], timeout=1800)
                status = self.probe()
            if status['status'] == 'unsupported':
                # Only convert the distribution created by this installation
                # step. Existing distributions/defaults are never changed.
                self.notify('De zojuist geïnstalleerde Ubuntu 24.04 wordt omgezet naar WSL2.')
                self._command(['wsl.exe', '--set-version', DISTRIBUTION, '2'], timeout=1800)
                status = self.probe()
            if status['status'] != 'environment_required':
                if status['status'] == 'ready':
                    return status
                raise RuntimeError('Ubuntu is nog niet gereed. Herstart Windows als dit is gevraagd en probeer opnieuw. ' + status['message'])
        self._state('installing')
        self.notify('Ubuntu wordt voorbereid. Dit kan enkele minuten duren. Andere Python-omgevingen blijven behouden.')
        bootstrap = f'''set -eu
test "$(uname -m)" = x86_64 || {{ echo 'GaitDynamics vereist een Windows x64-computer; ARM64 wordt niet ondersteund.'; exit 1; }}
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3.12-venv python3-pip libegl1 libopengl0 ca-certificates
if ! id {USER} >/dev/null 2>&1; then
    test ! -e {PREFIX}
    useradd --system --create-home --home-dir {PREFIX} --shell /usr/sbin/nologin {USER}
fi
test "$(getent passwd {USER} | cut -d: -f6)" = {PREFIX}
test "$(id -u {USER})" != 0
'''
        self._linux(['/bin/bash', '-s'], user='root', timeout=900, input=bootstrap.encode())
        identity = payload_id()
        package, requirements, downloader = payload_files()
        with tempfile.TemporaryDirectory(prefix='HU3D-setup-') as folder:
            stage = Path(folder)
            (stage / 'mocap_app' / 'analysis').mkdir(parents=True)
            shutil.copy2(package / '__init__.py', stage / 'mocap_app')
            for file in (package / 'analysis').glob('gaitdynamics*.py'):
                shutil.copy2(file, stage / 'mocap_app' / 'analysis')
            shutil.copy2(package / 'analysis' / '__init__.py', stage / 'mocap_app' / 'analysis')
            (stage / 'tools').mkdir()
            shutil.copy2(downloader, stage / 'tools' / downloader.name)
            shutil.copy2(requirements, stage / requirements.name)
            source = self.linux_path(stage)
            app = f'{PREFIX}/app-{identity}'
            self._linux(['mkdir', '-p', app])
            self._linux(['cp', '-R', '--', source + '/.', app])
        self.notify('CPU-pakketten worden geïnstalleerd. Er is internet nodig; de eerste installatie kan lang duren.')
        self._linux(['python3.12', '-m', 'venv', f'{PREFIX}/venv'], timeout=120)
        python = f'{PREFIX}/venv/bin/python'
        self._linux([python, '-m', 'pip', 'install', '--disable-pip-version-check', 'torch==2.8.0', '--index-url', 'https://download.pytorch.org/whl/cpu'], timeout=2400)
        self._linux([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', app + '/requirements-gaitdynamics.txt'], timeout=2400)
        self.notify('De twee AI-modellen worden gedownload en met SHA256 gecontroleerd.')
        self._linux([python, app + '/tools/setup_gaitdynamics.py', '--output', PREFIX + '/runtime'], timeout=1800)
        status = self.probe()
        if status['status'] != 'ready':
            raise RuntimeError('De eindcontrole is niet geslaagd. Probeer Installeren / doorgaan opnieuw. ' + status['message'])
        return self._state('ready', payload=identity, message=status['message'])

    def linux_path(self, path):
        path = Path(path).absolute()
        if str(path).startswith(('\\\\', '//')):
            raise ValueError('Gebruik een lokale Windows-map; netwerkschijven worden niet ondersteund.')
        return self._linux(['wslpath', '-a', '-u', str(path)]).strip()

    def analyze(self, motion, model, output, height, mass, speed):
        status = self.probe()
        if status['status'] != 'ready':
            raise RuntimeError('Installeer eerst de rekenomgeving: ' + status['message'])
        from mocap_app.analysis.gaitdynamics import inspect_inputs
        report = inspect_inputs(Path(motion), Path(model))
        if not report['compatible']:
            raise ValueError('\n'.join(report['errors']))
        if not (1 <= height <= 2.5 and 30 <= mass <= 200 and 0 <= speed <= 10):
            raise ValueError('Controleer lengte (1–2,5 m), massa (30–200 kg) en bandsnelheid (0–10 m/s).')
        output = Path(output).absolute()
        if output.exists():
            raise ValueError('De resultaatmap bestaat al; kies een nieuwe map.')
        args = [f'{PREFIX}/venv/bin/python', '-m', 'mocap_app.analysis.gaitdynamics',
                '--motion', self.linux_path(motion), '--model', self.linux_path(model),
                '--output', self.linux_path(output), '--runtime', PREFIX + '/runtime',
                '--python', PREFIX + '/venv/bin/python', '--height', str(height),
                '--mass', str(mass), '--treadmill-speed', str(speed)]
        self.notify('Krachten worden op de CPU berekend. Het venster blijft open; resultaten komen terug in je Windows-map.')
        try:
            self._linux(['/usr/bin/env', 'PYTHONPATH=' + PREFIX + '/app-' + payload_id(),
                         'CUDA_VISIBLE_DEVICES=', 'ACCELERATE_USE_CPU=true', *args], timeout=2100)
            result = json.loads((output / 'gaitdynamics.json').read_text())
            from mocap_app.analysis.gaitdynamics import sha256
            if result.get('status') != 'completed' or sha256(output / 'ground_reaction_forces.mot') != result['result']['sha256']:
                raise ValueError('De teruggegeven krachtresultaten zijn niet volledig of beschadigd.')
            (output / 'windows-transport.json').write_text(json.dumps(dict(distribution=self.distribution,
                windows_motion=str(Path(motion).absolute()), windows_model=str(Path(model).absolute()),
                windows_output=str(output), height_m=height, mass_kg=mass, treadmill_speed_m_s=speed,
                payload=payload_id()), indent=2), encoding='utf-8')
            return dict(status='completed', output=str(output), message=f'Krachtberekening gereed. Resultaten: {output}')
        finally:
            self.notify(f'Resultaatmap: {output}. Foutdetails staan daar in gaitdynamics.log en gaitdynamics.json, als de rekenmodule is gestart.')
