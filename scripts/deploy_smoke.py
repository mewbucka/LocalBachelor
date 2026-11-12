#!/usr/bin/env python3
"""Deployment smoke test for LocalBachelor.

Validates that a freshly-checked-out copy of the repository deploys
correctly: dependencies install, migrations apply, the server starts,
and the key endpoints respond.

Modes
-----
--clean   Full end-to-end in a throwaway temp directory (default).
          Never touches the repo's config.yaml or data_dir.
--live    Check a running server (skip install/migrate/start).
          Requires --base-url.

Exit code 0: all checks passed. Non-zero: a check failed (the failing
check is named in the output).

Usage (from the repo root):
    python scripts/deploy_smoke.py --clean
    python scripts/deploy_smoke.py --live --base-url http://192.168.1.50:8000
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Bind port 0 to get an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """GET a URL; return (status_code, body_text, content_type)."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, e.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e), ""


def _wait_for_health(base_url: str, timeout: float = 30.0) -> bool:
    """Poll /healthz until it returns 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout
    url = f"{base_url}/healthz"
    while time.monotonic() < deadline:
        code, body, _ = _http_get(url, timeout=3)
        if code == 200:
            return True
        time.sleep(1.0)
    return False


# ---------------------------------------------------------------------------
# Check functions (shared by --clean and --live)
# ---------------------------------------------------------------------------

_checks_passed = 0
_checks_failed = 0


def _check(name: str, ok: bool, detail: str = "") -> bool:
    global _checks_passed, _checks_failed
    if ok:
        _checks_passed += 1
        print(f"  [PASS] {name}")
    else:
        _checks_failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
    return ok


def run_endpoint_checks(base_url: str) -> None:
    """Run the shared HTTP endpoint checks (steps 5-7)."""
    print(f"\n  Endpoint checks against {base_url}:")

    # 5. /healthz → 200, status ok
    code, body, _ = _http_get(f"{base_url}/healthz")
    try:
        status = json.loads(body).get("status")
    except (json.JSONDecodeError, AttributeError):
        status = None
    _check(
        "GET /healthz → 200 {status: ok}",
        code == 200 and status == "ok",
        f"got {code}, body={body[:120]!r}",
    )

    # 6. / (browser) → 200, HTML
    code, body, ctype = _http_get(f"{base_url}/")
    _check(
        "GET / → 200 HTML",
        code == 200 and "text/html" in ctype,
        f"got {code}, ctype={ctype!r}",
    )

    # 7. /api/auth/me → 401 (API layer + auth middleware alive)
    code, body, _ = _http_get(f"{base_url}/api/auth/me")
    _check(
        "GET /api/auth/me → 401 (auth gate)",
        code == 401,
        f"got {code}, body={body[:120]!r}",
    )


# ---------------------------------------------------------------------------
# --clean mode
# ---------------------------------------------------------------------------


def run_clean() -> int:
    """Full end-to-end smoke test in a throwaway temp directory."""
    print("=" * 60)
    print(" LocalBachelor deployment smoke test (--clean)")
    print("=" * 60)

    # Safety: never touch the repo's config.yaml or data_dir.
    if (REPO_ROOT / "config.yaml").is_file():
        print("\n  Note: repo config.yaml exists — smoke test uses a temp copy.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="lb_smoke_"))
    print(f"\n  Temp dir: {tmp_dir}")

    app_proc: subprocess.Popen | None = None
    exit_code = 1

    try:
        # --- Step 1: Write temp config ---
        print("\n[1/7] Writing temp config.yaml ...")
        secret_key = secrets.token_urlsafe(32)
        try:
            from cryptography.fernet import Fernet

            settings_key = Fernet.generate_key().decode()
        except ImportError:
            settings_key = secrets.token_urlsafe(32)  # dummy for keyless

        config_yaml = tmp_dir / "config.yaml"
        config_yaml.write_text(
            f'secret_key: "{secret_key}"\n'
            f'settings_key: "{settings_key}"\n'
            f'db_path: "local-bachelor.db"\n'
            f'data_dir: "{tmp_dir.as_posix()}"\n'
            f'host: "127.0.0.1"\n'
            f"port: {0}  # placeholder, replaced below\n",
            encoding="utf-8",
        )
        _check("temp config written", config_yaml.is_file())

        # --- Step 2: Write temp alembic.ini ---
        print("\n[2/7] Writing temp alembic.ini ...")
        port = _find_free_port()
        # Update config with the actual port.
        config_yaml.write_text(
            f'secret_key: "{secret_key}"\n'
            f'settings_key: "{settings_key}"\n'
            f'db_path: "local-bachelor.db"\n'
            f'data_dir: "{tmp_dir.as_posix()}"\n'
            f'host: "127.0.0.1"\n'
            f"port: {port}\n",
            encoding="utf-8",
        )

        # Build a temp alembic.ini that points at the repo's scripts
        # and the temp config (absolute paths).
        alembic_ini = tmp_dir / "alembic.ini"
        alembic_ini.write_text(
            "[alembic]\n"
            f"script_location = {REPO_ROOT / 'alembic'}\n"
            f"prepend_sys_path = {REPO_ROOT}\n"
            "file_template = %%(rev)s_%%(slug)s\n"
            "sqlalchemy.url = sqlite:///./local-bachelor.db\n"
            "\n"
            "[app]\n"
            f"app_config = {config_yaml}\n"
            "\n"
            "[loggers]\n"
            "keys = root,sqlalchemy,alembic\n"
            "\n"
            "[handlers]\n"
            "keys = console\n"
            "\n"
            "[formatters]\n"
            "keys = generic\n"
            "\n"
            "[logger_root]\n"
            "level = WARN\n"
            "handlers = console\n"
            "qualname =\n"
            "\n"
            "[logger_sqlalchemy]\n"
            "level = WARN\n"
            "handlers =\n"
            "qualname = sqlalchemy.engine\n"
            "\n"
            "[logger_alembic]\n"
            "level = INFO\n"
            "handlers =\n"
            "qualname = alembic\n"
            "\n"
            "[handler_console]\n"
            "class = StreamHandler\n"
            "args = (sys.stderr,)\n"
            "level = NOTSET\n"
            "formatter = generic\n"
            "\n"
            "[formatter_generic]\n"
            "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
            "datefmt = %H:%M:%S\n",
            encoding="utf-8",
        )
        _check("temp alembic.ini written", alembic_ini.is_file())

        # --- Step 3: Run alembic upgrade head ---
        print("\n[3/7] Running 'alembic upgrade head' ...")
        python = sys.executable
        alembic_result = subprocess.run(
            [python, "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if alembic_result.returncode == 0:
            _check("alembic upgrade head", True)
        else:
            detail = (alembic_result.stderr or alembic_result.stdout)[:300]
            _check("alembic upgrade head", False, detail)
            return 1

        # Verify the DB file was created.
        db_file = tmp_dir / "local-bachelor.db"
        _check(
            "database file created",
            db_file.is_file(),
            f"expected {db_file}",
        )
        if not db_file.is_file():
            return 1

        # --- Step 4: Start the app ---
        print("\n[4/7] Starting app ...")
        env = os.environ.copy()
        app_proc = subprocess.Popen(
            [python, "-m", "app.main", "--config", str(config_yaml)],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(1.0)  # give it a moment to bind

        base_url = f"http://127.0.0.1:{port}"
        if not _wait_for_health(base_url, timeout=30):
            # Try to capture the error.
            app_proc.kill()
            stderr = (app_proc.stderr or b"").read().decode("utf-8", "replace")
            _check("app started and healthy", False, f"healthz never returned 200. stderr: {stderr[:300]}")
            return 1
        _check("app started and healthy", True)

        # --- Steps 5-7: Endpoint checks ---
        run_endpoint_checks(base_url)

    except subprocess.TimeoutExpired:
        print("\n  [FAIL] Timeout waiting for subprocess")
        _checks_failed += 1
    except Exception as e:
        print(f"\n  [FAIL] Unexpected error: {e}")
        _checks_failed += 1
    finally:
        # --- Step 5: Stop the app ---
        if app_proc is not None and app_proc.poll() is None:
            print("\n  Stopping app ...")
            app_proc.terminate()
            try:
                app_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app_proc.kill()
                app_proc.wait()

        # --- Teardown ---
        print(f"\n  Removing temp dir: {tmp_dir}")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- Summary ---
    print(f"\n{'=' * 60}")
    total = _checks_passed + _checks_failed
    print(f" Result: {_checks_passed}/{total} checks passed, {_checks_failed} failed")
    print(f"{'=' * 60}")

    if _checks_failed > 0:
        print(" SMOKE TEST FAILED")
        return 1
    print(" SMOKE TEST PASSED")
    return 0


# ---------------------------------------------------------------------------
# --live mode
# ---------------------------------------------------------------------------


def run_live(base_url: str) -> int:
    """Check a running server (no install/migrate/start)."""
    base_url = base_url.rstrip("/")
    print("=" * 60)
    print(f" LocalBachelor deployment smoke test (--live)")
    print(f" Target: {base_url}")
    print("=" * 60)

    if not _wait_for_health(base_url, timeout=10):
        print("  [FAIL] Server not reachable (healthz did not respond)")
        print(" SMOKE TEST FAILED")
        return 1
    _check("server reachable", True)

    run_endpoint_checks(base_url)

    print(f"\n{'=' * 60}")
    total = _checks_passed + _checks_failed
    print(f" Result: {_checks_passed}/{total} checks passed, {_checks_failed} failed")
    print(f"{'=' * 60}")

    if _checks_failed > 0:
        print(" SMOKE TEST FAILED")
        return 1
    print(" SMOKE TEST PASSED")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LocalBachelor deployment smoke test.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--clean",
        action="store_true",
        help="Full end-to-end in a temp directory (default).",
    )
    group.add_argument(
        "--live",
        action="store_true",
        help="Check a running server (requires --base-url).",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL for --live mode (default: http://127.0.0.1:8000).",
    )
    args = parser.parse_args()

    if args.live:
        return run_live(args.base_url)
    # Default (and explicit --clean)
    return run_clean()


if __name__ == "__main__":
    sys.exit(main())
