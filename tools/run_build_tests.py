"""Keep native stderr out of PowerShell's error pipeline; retain full test logs."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--log', type=Path, required=True)
args = parser.parse_args()
with args.log.open('w', encoding='utf-8') as log:
    result = subprocess.run([sys.executable, '-X', 'faulthandler', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                            stdout=log, stderr=subprocess.STDOUT)
print(f'Unit-tests exitcode {result.returncode}; log: {args.log}')
if result.returncode and os.environ.get('GITHUB_ACTIONS') == 'true':
    # Public annotation lets reviewers see the failing tests without needing
    # to download an authenticated artifact. Tests only use temporary fixtures.
    detail = args.log.read_text(encoding='utf-8', errors='replace')[-2500:]
    detail = detail.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::error title=Windows unit-tests exitcode {result.returncode}::' + detail)
raise SystemExit(result.returncode)
