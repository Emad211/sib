"""Domain stays pure Python: no IO, framework or adapter imports, and a small per-file size cap."""

import ast
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "src" / "shift" / "domain"
# permissions.py is a pure role × permission data table: it legitimately grows with each rule
# (H-12 added the nursing flow's start and its sign), so it carries its own cap. Logic stays at 150.
# rewards.py is the one documented home of the reward rules (S01 §5, shf5-DESIGN.md §3): its
# docstrings are where the master's provisional money decisions are recorded, so the file carries
# that documentation on top of its ~90 lines of arithmetic and gets its own cap.
# SHF-4's two new files are each one document of the purchasing chain (S03 §3's payload vocabulary and
# S03 §4's parcel states), so splitting either would hide the chain instead of shortening it.
# permission_grants.py is one role × permission row per role, and owner decision 9 gave two shift roles
# a new line each (the flow's own first count): the rows are the file, so it carries its own cap.
LINE_CAPS = {
    "permissions.py": 180,
    "rewards.py": 200,
    "purchase_payloads.py": 200,
    "purchase_deliveries.py": 200,
    "permission_grants.py": 160,
}
DEFAULT_LINE_CAP = 150
FORBIDDEN_ROOTS = {
    "io",
    "os",
    "pathlib",
    "sqlite3",
    "sqlalchemy",
    "fastapi",
    "starlette",
    "uvicorn",
    "logging",
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "subprocess",
    "importlib",
    "shutil",
    "tempfile",
    "pickle",
}
FORBIDDEN_PREFIXES = ("shift.adapters", "shift.services", "shift.api", "shift.config")


def _domain_files() -> list[Path]:
    return sorted(DOMAIN_DIR.glob("*.py"))


def test_domain_has_files() -> None:
    assert _domain_files()


@pytest.mark.parametrize("path", _domain_files(), ids=lambda path: path.name)
def test_domain_file_line_limit(path: Path) -> None:
    assert len(path.read_text(encoding="utf-8").splitlines()) <= LINE_CAPS.get(path.name, DEFAULT_LINE_CAP)


@pytest.mark.parametrize("path", _domain_files(), ids=lambda path: path.name)
def test_domain_imports_no_io_or_framework(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    for module in imports:
        assert module.split(".", 1)[0] not in FORBIDDEN_ROOTS, f"{path.name} imports {module}"
        assert not module.startswith(FORBIDDEN_PREFIXES), f"{path.name} imports {module}"


@pytest.mark.parametrize("path", _domain_files(), ids=lambda path: path.name)
def test_domain_never_opens_files(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", f"{path.name} calls open()"
