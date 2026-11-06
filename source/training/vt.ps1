# Runs the Vertex training CLI with the training venv, from any folder.
# Usage:  .\training\vt.ps1 run | dataset | timeline FILE | final | ui | serve | arena bench|watch|train|eval
$venv = Join-Path $env:LOCALAPPDATA "vertex-training\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Error "No training venv yet. Run .\training\setup.ps1 first."
    exit 1
}
Push-Location $PSScriptRoot
try {
    & "$venv\Scripts\python.exe" -m vertex_training @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
