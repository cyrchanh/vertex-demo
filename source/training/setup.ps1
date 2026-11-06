# Creates the training virtual environment outside OneDrive and installs requirements.
# Usage:  .\training\setup.ps1
$ErrorActionPreference = "Stop"
$venv = Join-Path $env:LOCALAPPDATA "vertex-training\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    python -m venv $venv
}
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
Write-Host "Ready. Run:  .\training\vt.ps1 run"
