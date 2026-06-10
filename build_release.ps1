# Build and publish a HuCalib release with Velopack.
#
# Usage:
#   .\build_release.ps1                 # build installer + update package (local only)
#   .\build_release.ps1 -Upload         # also publish to GitHub Releases
#   .\build_release.ps1 -Upload -Token ghp_xxx
#
# The version comes from mocap_app/__init__.py (__version__) so there is one
# place to bump. See UPDATING.md for the full workflow.

param(
    [switch]$Upload,
    [string]$Token = $env:GITHUB_TOKEN
)

$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/MertenF06/HuCalib"
$root = $PSScriptRoot

# --- 1. Read the version from the single source of truth -------------------
$initPath = Join-Path $root "mocap_app\__init__.py"
$match = Select-String -Path $initPath -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $match) { throw "Could not find __version__ in $initPath" }
$version = $match.Matches[0].Groups[1].Value
Write-Host "==> Building HuCalib $version" -ForegroundColor Cyan

# --- 2. Compile with PyInstaller (onedir, required by Velopack) ------------
Write-Host "==> Running PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller HuCalib.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$packDir = Join-Path $root "dist\HuCalib"
if (-not (Test-Path (Join-Path $packDir "HuCalib.exe"))) {
    throw "Expected $packDir\HuCalib.exe was not produced"
}

# --- 3. Package the release with Velopack ----------------------------------
# Produces .\Releases\ : HuCalib-win-Setup.exe (installer), a portable zip,
# the .nupkg update package and the RELEASES feed file.
Write-Host "==> Packing with Velopack..." -ForegroundColor Cyan
vpk pack `
    --packId HuCalib `
    --packVersion $version `
    --packDir $packDir `
    --mainExe HuCalib.exe `
    --packTitle "HuCalib" `
    --icon "ui\imagesGUI\hucalib_cube_icon.ico"
if ($LASTEXITCODE -ne 0) { throw "vpk pack failed" }

Write-Host "==> Done. Output is in .\Releases\" -ForegroundColor Green

# --- 4. Optionally publish to GitHub Releases ------------------------------
if ($Upload) {
    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw "No token. Pass -Token <pat> or set `$env:GITHUB_TOKEN (needs 'repo' scope)."
    }
    Write-Host "==> Uploading to GitHub Releases (tag v$version)..." -ForegroundColor Cyan
    vpk upload github `
        --repoUrl $repoUrl `
        --publish `
        --releaseName "HuCalib $version" `
        --tag "v$version" `
        --token $Token
    if ($LASTEXITCODE -ne 0) { throw "vpk upload failed" }
    Write-Host "==> Published HuCalib $version to GitHub." -ForegroundColor Green
} else {
    Write-Host "(Run with -Upload to publish this release to GitHub.)" -ForegroundColor DarkGray
}
