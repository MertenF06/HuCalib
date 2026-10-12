# One-time setup inside an extracted Windows test kit. No webcam is required.
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$environmentDir = Join-Path $root '.venv-pose2sim'
& $Python -c "import sys; assert sys.version_info[:2] == (3, 12), 'Gebruik Python 3.12 (64-bit)'; assert sys.maxsize > 2**32, 'Gebruik 64-bit Python'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 (64-bit) is vereist.' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Installeer Git for Windows voordat je de analyseomgeving instelt.' }
if (-not (Test-Path $environmentDir)) {
    & $Python -m venv $environmentDir
    if ($LASTEXITCODE -ne 0) { throw 'De analyseomgeving kon niet worden aangemaakt.' }
}
$analysisPython = Join-Path $environmentDir 'Scripts\python.exe'
& $analysisPython -m pip install -r (Join-Path $root 'requirements-pose2sim.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pose2Sim-installatie mislukt. Bewaar de foutmelding; Windows-wheels kunnen een beperking zijn.' }
& $analysisPython -c "from Pose2Sim import Pose2Sim; import opensim, onnxruntime; print('Pose2Sim, OpenSim en ONNX Runtime kunnen worden geladen.')"
if ($LASTEXITCODE -ne 0) { throw 'Een analysedependency kan op deze computer niet worden geladen.' }
& $analysisPython -m pip freeze | Out-File (Join-Path $root 'analysis-dependencies.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Analyseversies vastleggen mislukt.' }
$settings = Join-Path $root 'HuCalib\analysis-settings.ini'
if ((Test-Path (Split-Path $settings)) -and -not (Test-Path $settings)) {
    $pythonValue = $analysisPython.Replace('\', '/')
    $configValue = (Join-Path $root 'Demo\Config.toml').Replace('\', '/')
    $content = "[General]`npython=$pythonValue`nconfig=$configValue`n"
    [IO.File]::WriteAllText($settings, $content, [Text.UTF8Encoding]::new($false))
}
Write-Host 'Analyseomgeving gereed. Start HuCalib\HuCalib.exe en kies Demo\opname.' -ForegroundColor Green
Write-Host "Analyse-Python: $analysisPython"
