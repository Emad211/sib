# Developer entry point for the shift app. Run from apps/shift.
#   ./dev.ps1 setup   create .venv and install requirements-dev.lock
#   ./dev.ps1 test    everything CI runs: ruff check, format check, pytest
#   ./dev.ps1 lint    ruff check
#   ./dev.ps1 fmt     rewrite files with ruff format
#   ./dev.ps1 run     start the service on port 8443
param([Parameter(Position = 0)][string]$Task = "test")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
$env:PYTHONDONTWRITEBYTECODE = "1"

switch ($Task) {
    "setup" {
        if (-not (Test-Path $Py)) { py -3.13 -m venv (Join-Path $PSScriptRoot ".venv") }
        & $Py -m pip install -r requirements-dev.lock
    }
    "test" {
        & $Py -m ruff check .; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m ruff format --check .; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m pytest
    }
    "lint" { & $Py -m ruff check . }
    "fmt" { & $Py -m ruff format . }
    "run" { & $Py start.py }
    default { throw "unknown task: $Task" }
}
