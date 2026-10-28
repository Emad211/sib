"""CI gate tests: the workflow must run the JS and Playwright suites (review S7)."""

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "shift.yml"
_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


def test_workflow_runs_the_node_outbox_units() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "actions/setup-node" in workflow
    assert "node --test tests/js/outbox.test.js" in workflow


def test_workflow_runs_playwright_e2e_with_an_installed_browser() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "playwright" in workflow
    assert "playwright install chromium" in workflow
    assert "pytest tests/e2e" in workflow


def test_e2e_marker_is_registered() -> None:
    conftest = _CONFTEST.read_text(encoding="utf-8")

    assert "addinivalue_line" in conftest
    assert '"e2e:' in conftest


def test_workflow_still_guards_tracked_databases() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "database files are tracked" in workflow
