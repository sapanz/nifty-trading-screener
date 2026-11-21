"""The page's "Update data" panel names workflows by file and by display name: keep them in step with .github/workflows."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _jobs():
    html = (ROOT / "docs" / "index.html").read_text()
    return re.findall(r'file: "([\w.]+\.yml)", wf: "([^"]+)"', html)


def test_every_job_in_the_panel_is_a_real_manually_runnable_workflow_with_the_same_name():
    jobs = _jobs()
    assert {f for f, _ in jobs} == {"dashboard.yml", "dashboard_history.yml", "dashboard_swing.yml", "dashboard_setups.yml"}
    for file, name in jobs:
        text = (ROOT / ".github" / "workflows" / file).read_text()
        assert re.search(rf"^name: {re.escape(name)}\s*$", text, re.M), (file, name)
        assert "workflow_dispatch" in text, f"{file} cannot be started by hand"


def test_the_panel_never_offers_the_login_builds_during_market_hours():
    html = (ROOT / "docs" / "index.html").read_text()
    assert "8 * 60 + 40" in html and "15 * 60 + 45" in html and "would cut off the live dashboard" in html
    assert "localStorage.setItem(\"dashGhToken\"" in html and "api.github.com/repos/" in html

