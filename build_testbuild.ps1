# Build a portable Windows test kit; never upload a release or invoke Velopack.
param([string]$Python = 'python', [switch]$SkipDependencyInstall)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$buildRoot = Join-Path $root '.build-testbuild'
$outputRoot = Join-Path $root 'dist-testbuild'
$kit = Join-Path $outputRoot 'HuCalib-testbuild'
Push-Location $root
try {
    & $Python -c "import sys; assert sys.platform == 'win32', 'Bouw op Windows'; assert sys.version_info[:2] == (3, 12), 'Gebruik Python 3.12'; assert sys.maxsize > 2**32, 'Gebruik 64-bit Python'"
    if ($LASTEXITCODE -ne 0) { throw 'Deze testbuild vereist Windows met Python 3.12 (64-bit).' }
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    $venv = Join-Path $root '.venv-testbuild'
    if (-not (Test-Path $venv)) {
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Buildomgeving aanmaken mislukt.' }
    }
    $buildPython = Join-Path $venv 'Scripts\python.exe'
    if (-not $SkipDependencyInstall) {
        & $buildPython -m pip install -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) { throw 'Builddependencies installeren mislukt.' }
    }
    $previousPlatform = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = 'offscreen'
    try {
        & $buildPython tools\run_build_tests.py --log (Join-Path $buildRoot 'unit-tests.log')
        if ($LASTEXITCODE -ne 0) { throw 'Tests mislukt; geen testkit gemaakt.' }
        & $buildPython -m PyInstaller HuCalib.spec --clean --noconfirm --distpath (Join-Path $buildRoot 'app') --workpath (Join-Path $buildRoot 'work')
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller-build mislukt.' }
        $appDir = Join-Path $buildRoot 'app\HuCalib'
        $revision = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Git-revisie kon niet worden bepaald.' }
        $metadata = @{ kind = 'portable-testbuild'; git_commit = $revision; created_at_utc = [DateTime]::UtcNow.ToString('o'); automatic_updates = $false }
        [IO.File]::WriteAllText((Join-Path $appDir 'TESTBUILD.json'), ($metadata | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        $smokeReport = Join-Path $buildRoot 'frozen-gui-smoke.json'
        if (Test-Path $smokeReport) { Remove-Item $smokeReport }
        $process = Start-Process -FilePath (Join-Path $appDir 'HuCalib.exe') -ArgumentList ('--test-build-smoke "' + $smokeReport + '"') -PassThru
        if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'De exe-startproef overschreed 60 seconden.' }
        $process.Refresh()
        if ($process.ExitCode -ne 0 -or -not (Test-Path $smokeReport)) { throw 'De gebouwde exe kan de analysebediening niet openen.' }
        $smoke = Get-Content $smokeReport -Raw | ConvertFrom-Json
        if ($smoke.status -ne 'passed' -or -not $smoke.frozen) { throw 'Geen geslaagde frozen-GUI-startproef.' }
    } finally {
        $env:QT_QPA_PLATFORM = $previousPlatform
    }
    if (Test-Path $kit) { throw "Uitvoer bestaat al: $kit. Bewaar of verplaats de vorige testkit eerst." }
    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
    New-Item -ItemType Directory -Path $kit | Out-Null
    Copy-Item -Recurse -Path $appDir -Destination (Join-Path $kit 'HuCalib')
    Copy-Item setup_analysis.ps1, requirements-pose2sim.txt -Destination $kit
    Copy-Item Installeer-GaitDynamics.cmd -Destination $kit
    Copy-Item docs\windows_testbuild.md -Destination (Join-Path $kit 'LEESMIJ-WINDOWS.md')
    Copy-Item docs\gaitdynamics.md -Destination $kit
    Copy-Item $smokeReport -Destination $kit
    & $buildPython -m pip freeze | Out-File (Join-Path $kit 'build-dependencies.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Dependencyversies vastleggen mislukt.' }
    & $buildPython tools\prepare_windows_demo.py --output (Join-Path $kit 'Demo')
    if ($LASTEXITCODE -ne 0) { throw 'Demo voorbereiden mislukt.' }
    $hashLines = @(Get-ChildItem -Path $kit -File -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($kit.Length + 1)
        '{0}  {1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash, $relative
    })
    $hashLines | Out-File (Join-Path $kit 'SHA256SUMS.txt') -Encoding utf8
    $zip = Join-Path $outputRoot ('HuCalib-testbuild-' + $revision.Substring(0, 8) + '.zip')
    if (Test-Path $zip) { throw "Archief bestaat al: $zip" }
    Compress-Archive -Path $kit -DestinationPath $zip
    Write-Host "Testkit gereed: $zip" -ForegroundColor Green
} catch {
    if ($env:GITHUB_ACTIONS -eq 'true') {
        $details = $_.Exception.Message
        if (Test-Path (Join-Path $buildRoot 'frozen-gui-smoke.json')) {
            $details += "`n" + (Get-Content (Join-Path $buildRoot 'frozen-gui-smoke.json') -Raw)
        }
        if ($details.Length -gt 2500) { $details = $details.Substring(0, 2500) }
        $details = $details.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
        Write-Output "::error title=Windows-testbuild::$details"
    }
    throw
} finally {
    Pop-Location
}
