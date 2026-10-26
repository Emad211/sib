# Developer entry point for the accounting app (ACC-0.2). Run from apps/accounting.
#   ./dev.ps1 setup       create ../../.venv and install requirements-dev.lock
#   ./dev.ps1 test        everything CI runs (lint + tests on synthetic data)
#   ./dev.ps1 test-prod   production-snapshot gate (LOCAL ONLY, needs sib/_data/prod_snapshot)
#   ./dev.ps1 golden      re-run the golden master compare on the newest snapshot
#   ./dev.ps1 synth       write a synthetic DB to ../../_data/synthetic.db
#   ./dev.ps1 run         run the app on the synthetic DB (port 8080) — never on real data
#   ./dev.ps1 build       build dist/HesabdariSib.exe
param([Parameter(Position = 0)][string]$Task = "test")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Root = Resolve-Path "$PSScriptRoot/../.."
$Py = Join-Path $Root ".venv/Scripts/python.exe"
$env:PYTHONDONTWRITEBYTECODE = "1"

function Newest-Snapshot {
    $s = Get-ChildItem (Join-Path $Root "_data/prod_snapshot/*.db") -ErrorAction SilentlyContinue |
        Sort-Object Name | Select-Object -Last 1
    if (-not $s) { throw "no snapshot in _data/prod_snapshot (see docs/engineering/E02)" }
    return $s.FullName
}

switch ($Task) {
    "setup" {
        if (-not (Test-Path $Py)) { py -3.13 -m venv (Join-Path $Root ".venv") }
        & $Py -m pip install -r requirements-dev.lock
    }
    "test" {
        & $Py -m ruff check .; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m ruff format --check .; if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $Py -m pytest
    }
    "test-prod" { & $Py -m pytest -m prod_snapshot tests/test_prod_snapshot.py }
    "golden" { & $Py tools/golden_master.py compare --db (Newest-Snapshot) }
    "synth" { & $Py tools/make_synthetic_db.py (Join-Path $Root "_data/synthetic.db") }
    "run" {
        $db = Join-Path $Root "_data/synthetic.db"
        if (-not (Test-Path $db)) { & $Py tools/make_synthetic_db.py $db }
        & $Py -c "import sys; sys.path.insert(0,'.'); from src.config.settings import Config; Config.DATABASE_PATH=r'$db'; from src.app import create_app; create_app().run(host='127.0.0.1', port=8080)"
    }
    "build" { & $Py -m PyInstaller --noconfirm HesabdariSib.spec }
    default { throw "unknown task: $Task" }
}
