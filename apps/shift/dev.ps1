# Developer entry point for the shift app. Run from apps/shift.
#   ./dev.ps1 setup    create .venv and install requirements-dev.lock
#   ./dev.ps1 test     everything CI runs: ruff check, format check, tests, domain coverage gate
#   ./dev.ps1 lint     ruff check
#   ./dev.ps1 fmt      rewrite files with ruff format
#   ./dev.ps1 run      start the service on port 8443
#   ./dev.ps1 verify   verify the event hash chain of the configured database
#   ./dev.ps1 rebuild  repair projections from the event log
param([Parameter(Position = 0)][string]$Task = "test")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
$env:PYTHONDONTWRITEBYTECODE = "1"
$Db = if ($env:SHIFT_DB_PATH) { $env:SHIFT_DB_PATH } else { Join-Path $PSScriptRoot "shift.db" }

switch ($Task) {
    "setup" {
        if (-not (Test-Path $Py)) { py -3.13 -m venv (Join-Path $PSScriptRoot ".venv") }
        & $Py -m pip install -r requirements-dev.lock
    }
    "test" {
        & $Py -m ruff check .; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m ruff format --check .; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m coverage run -m pytest; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m coverage report --include="*/shift/domain/*" --fail-under=95
    }
    "lint" { & $Py -m ruff check . }
    "fmt" { & $Py -m ruff format . }
    "run" { & $Py start.py }
    "verify" { & $Py tools/verify_event_chain.py --db $Db }
    "rebuild" { & $Py tools/rebuild_projections.py --db $Db --apply }
    default { throw "unknown task: $Task" }
}
