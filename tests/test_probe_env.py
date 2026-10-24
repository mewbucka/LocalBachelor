"""Smoke test for scripts/probe_env.py (roadmap task 0.6).

Runs the probe as a subprocess with the interpreter executing the tests.
The exact exit code depends on the host (e.g. whether sqlite-vec is
installed), so we assert it is a valid code and that the output is
internally consistent with it.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE = REPO_ROOT / "scripts" / "probe_env.py"


def test_probe_env_runs_and_reports() -> None:
    result = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode in (0, 1), f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    out = result.stdout
    assert "LocalBachelor environment probe" in out
    for check in ("python", "sqlite_wal", "fts5", "sqlite_vec", "fts5_icu"):
        assert f"] {check}" in out, f"missing check: {check}"
    assert "critical checks:" in out
    assert "RESULT:" in out
    assert "Record the found versions" in out
    if result.returncode == 0:
        assert "RESULT: OK" in out
    else:
        assert "RESULT: FAIL" in out
    # The probe must never crash: no traceback on stderr.
    assert "Traceback" not in result.stderr
