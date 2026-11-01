$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== WiZ Ambient Sync - Windows build ===" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.12 x64 and enable Add Python to PATH."
}

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" build.py

if (-not (Test-Path ".\dist\1.2.2-beta.1\WiZAmbientSync.exe")) {
    throw "GUI executable was not produced."
}
if (-not (Test-Path ".\dist\1.2.2-beta.1\wiz-engine.exe")) {
    throw "WiZ engine executable was not produced."
}

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $candidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $candidate) { $iscc = @{ Source = $candidate } }
}
if (-not $iscc) {
    Write-Host ""
    Write-Host "The EXE files are ready in .\dist\." -ForegroundColor Green
    Write-Host "To create the installer, install Inno Setup 6 and rerun this script."
    exit 0
}

& $iscc.Source ".\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host "  Portable app: .\dist\1.2.2-beta.1\WiZAmbientSync.exe (keep wiz-engine.exe beside it)"
Write-Host "  Installer:    .\installer-output\WiZ-Ambient-Sync-Beta-Setup-1.2.2-beta.1-x64.exe"
