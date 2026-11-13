#!/usr/bin/env python3
"""Deployment smoke test for LocalBachelor.

Validates that a freshly-checked-out copy of the repository deploys correctly,
end to end, in a throwaway temp directory:

  1. a fresh Python 3.12 venv is created and the repo installs into it
     (regression guard: editable packaging works on a clean checkout);
  2. a temp ``config.yaml`` is generated (fresh random keys, temp db/data dir,
     a free port) — the repo's ``config.yaml`` and ``data_dir`` are NEVER
     touched;
  3. the app starts WITHOUT migrations and reports a degraded ``/healthz``
     (503 ``{"status":"degraded","schema":"missing"}``) — the startup
     preflight guard (fixup 0.3) is exercised;
  4. ``alembic upgrade head`` applies the schema and a restart reports a
     healthy ``/healthz`` (200 ``{"status":"ok"}``);
  5. the first-run bootstrap is driven over real HTTP: the
     ``/api/auth/first-run`` status, the ``/setup`` wizard (form +
     double-submit CSRF), admin creation, the signed-in root page, and a
     login/logout cycle;
  6. the SQLite file is inspected directly: WAL journal mode,
     ``alembic_version`` stamped to head, the created user row, and an
     argon2id password hash;
  7. the environment is reported: HEAD commit, Python, SQLite, sqlite-vec.

Modes
-----
--clean   Full end-to-end in a throwaway temp directory (default).
          Never touches the repo's config.yaml or data_dir.
--live    Check a running server (skip install/migrate/start). Requires
          --base-url.

Exit code 0: all checks passed. Non-zero: a check failed (the failing check
is named in the output).

Usage (from the repo root):
    python scripts/deploy_smoke.py --clean
    python scripts/deploy_smoke.py --live --base-url http://192.168.1.50:8000
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Cookie names — mirror app/core/csrf.py (CSRF_COOKIE) and app/core/auth.py
# (SESSION_COOKIE). Hardcoded so this script stays import-free and runs under
# any interpreter: it builds its own Python 3.12 venv for the app rather than
# importing the app from the interpreter that launches the script.
CSRF_COOKIE = "lb_csrf"
SESSION_COOKIE = "lb_session"

# Throwaway bootstrap credentials for the temp instance (never written to the
# repo). The password exceeds the app's MIN_PASSWORD_LENGTH.
_SMOKE_USERNAME = "admin"
_SMOKE_PASSWORD = "correct-horse-battery-staple"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Bind port 0 to get an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """GET a URL; return (status_code, body_text, content_type).

    A connection that cannot be established (server not up yet) yields code 0.
    HTTP 4xx/5xx are returned as their status code, not raised.
    """
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
        code, _body, _ctype = _http_get(url, timeout=3)
        if code == 200:
            return True
        time.sleep(1.0)
    return False


def _wait_for_response(base_url: str, timeout: float = 30.0) -> tuple[int, str]:
    """Poll /healthz until the server answers with ANY HTTP status, returning
    (code, body). The degraded app answers 503 before it is ever healthy, so a
    200-only wait would time out. Returns (0, "") if it never answers."""
    deadline = time.monotonic() + timeout
    url = f"{base_url}/healthz"
    while time.monotonic() < deadline:
        code, body, _ctype = _http_get(url, timeout=3)
        if code != 0:
            return code, body
        time.sleep(0.5)
    return 0, ""


# ---------------------------------------------------------------------------
# Check bookkeeping (shared by --clean and --live)
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


# ---------------------------------------------------------------------------
# venv / install (step 1)
# ---------------------------------------------------------------------------


def _venv_python(venv_dir: Path) -> Path:
    """The venv's interpreter path (Windows: Scripts, POSIX: bin)."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _create_venv(venv_dir: Path) -> Path | None:
    """Create a Python 3.12 venv (uv preferred, ``python3.12 -m venv`` as a
    fallback). Returns the venv's interpreter path, or None on failure.

    uv is preferred because it can fetch a matching CPython build itself, so
    the guard holds even on a box where 3.12 is not pre-installed.
    """
    uv = shutil.which("uv")
    if uv:
        r = subprocess.run(
            [uv, "venv", "--python", "3.12", str(venv_dir)],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0 and _venv_python(venv_dir).exists():
            return _venv_python(venv_dir)
        print(f"    [warn] uv venv failed: {(r.stderr or r.stdout)[:200]}")
    py312 = shutil.which("python3.12")
    if py312:
        r = subprocess.run(
            [py312, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0 and _venv_python(venv_dir).exists():
            return _venv_python(venv_dir)
    return None


def _install_editable(venv_python: Path) -> tuple[bool, str]:
    """Install the repo (editable) into the venv. Returns (ok, detail)."""
    uv = shutil.which("uv")
    if uv:
        r = subprocess.run(
            [uv, "pip", "install", "--python", str(venv_python), "-e", str(REPO_ROOT)],
            capture_output=True, text=True, timeout=900,
        )
        return r.returncode == 0, (r.stderr or r.stdout)[-300:]
    r = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-e", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900,
    )
    return r.returncode == 0, (r.stderr or r.stdout)[-300:]


# ---------------------------------------------------------------------------
# App process lifecycle (steps 3-5)
# ---------------------------------------------------------------------------


def _start_app(venv_python: Path, config_yaml: Path) -> subprocess.Popen:
    """Start the app in the venv against the temp config (cwd = repo root)."""
    env = os.environ.copy()
    return subprocess.Popen(
        [str(venv_python), "-m", "app.main", "--config", str(config_yaml)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stop_app(proc: subprocess.Popen | None) -> None:
    """Terminate (then, if needed, kill) a running app process."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _remove_db(db_file: Path) -> None:
    """Remove the temp DB and any SQLite WAL sidecars (clean slate)."""
    for p in db_file.parent.glob(db_file.name + "*"):
        try:
            p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Cookie-jar HTTP client (step 5: the first-run flow)
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow redirects: the smoke test inspects 3xx responses directly
    (their Set-Cookie headers mint/rotate the session + CSRF cookies) instead
    of chasing them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


class _HttpClient:
    """A minimal cookie-jar HTTP client (urllib) with no redirect-following.

    Mirrors the browser behaviour the first-run flow exercises: cookies
    (lb_session / lb_csrf) are captured from Set-Cookie and resent on every
    request; 3xx responses are returned to the caller so the test can assert
    on the redirect status and the cookies it set (read back from the jar).
    """

    def __init__(self) -> None:
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            _NoRedirect(),
        )

    def cookie(self, name: str) -> str | None:
        for c in self._jar:
            if c.name == name:
                return c.value
        return None

    def _do_open(self, req: urllib.request.Request, timeout: float) -> tuple[int, str, dict]:
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if e.fp else ""
            return e.code, body, dict(e.headers)
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e), {}

    def get(self, url: str, timeout: float = 10.0) -> tuple[int, str, dict]:
        return self._do_open(urllib.request.Request(url, method="GET"), timeout)

    def post_form(
        self, url: str, fields: dict[str, str], timeout: float = 10.0
    ) -> tuple[int, str, dict]:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        return self._do_open(req, timeout)

    def post_json(
        self,
        url: str,
        payload: dict,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, str, dict]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return self._do_open(req, timeout)


def _json_field(body: str, key: str):
    """Pull a field out of a JSON body; None if the body is not JSON."""
    try:
        return json.loads(body).get(key)
    except (json.JSONDecodeError, ValueError):
        return None


def run_first_run_flow(base_url: str) -> None:
    """Drive the first-run bootstrap over real HTTP (step 5)."""
    print(f"\n  First-run bootstrap flow against {base_url}:")
    client = _HttpClient()

    # 1. Fresh instance: setup is still needed.
    code, body, _ = client.get(f"{base_url}/api/auth/first-run")
    needed = _json_field(body, "setup_needed")
    _check(
        "GET /api/auth/first-run -> 200 {setup_needed: true}",
        code == 200 and needed is True,
        f"got {code}, body={body[:120]!r}",
    )

    # 2. The /setup wizard renders and mints the double-submit CSRF cookie.
    code, _body, _ = client.get(f"{base_url}/setup")
    _check(
        "GET /setup -> 200 (mints lb_csrf)",
        code == 200 and client.cookie(CSRF_COOKIE) is not None,
        f"got {code}, csrf={'present' if client.cookie(CSRF_COOKIE) else 'MISSING'}",
    )

    # 3. Submit the wizard (form + csrf_token) -> 303 to / + the session cookie.
    code, _body, _ = client.post_form(
        f"{base_url}/setup",
        {
            "username": _SMOKE_USERNAME,
            "password": _SMOKE_PASSWORD,
            "lang": "en",
            "csrf_token": client.cookie(CSRF_COOKIE) or "",
        },
    )
    _check(
        "POST /setup (form) -> 303 / + session cookie",
        code == 303 and client.cookie(SESSION_COOKIE) is not None,
        f"got {code}, session={'present' if client.cookie(SESSION_COOKIE) else 'MISSING'}",
    )

    # 4. Signed-in root renders the shell (200).
    code, _body, _ = client.get(f"{base_url}/")
    _check("GET / (signed in) -> 200", code == 200, f"got {code}")

    # 5. Login (JSON + X-CSRF-Token) -> 200, role admin.
    code, body, _ = client.post_json(
        f"{base_url}/api/auth/login",
        {"username": _SMOKE_USERNAME, "password": _SMOKE_PASSWORD},
        headers={"X-CSRF-Token": client.cookie(CSRF_COOKIE) or ""},
    )
    role = _json_field(body, "role")
    _check(
        "POST /api/auth/login -> 200 {role: admin}",
        code == 200 and role == "admin",
        f"got {code}, body={body[:120]!r}",
    )

    # 6. Logout (JSON + X-CSRF-Token) -> 200, logged out.
    code, body, _ = client.post_json(
        f"{base_url}/api/auth/logout",
        {},
        headers={"X-CSRF-Token": client.cookie(CSRF_COOKIE) or ""},
    )
    ok = _json_field(body, "ok")
    _check(
        "POST /api/auth/logout -> 200 {ok: logged_out}",
        code == 200 and ok == "logged_out",
        f"got {code}, body={body[:120]!r}",
    )

    # 7. Bootstrap is closed: a user now exists.
    code, body, _ = client.get(f"{base_url}/api/auth/first-run")
    needed2 = _json_field(body, "setup_needed")
    _check(
        "GET /api/auth/first-run -> 200 {setup_needed: false}",
        code == 200 and needed2 is False,
        f"got {code}, body={body[:120]!r}",
    )


# ---------------------------------------------------------------------------
# SQLite inspection (step 6)
# ---------------------------------------------------------------------------


def run_sqlite_asserts(db_file: Path) -> None:
    """Inspect the temp SQLite file directly (step 6)."""
    print("\n  SQLite file inspection:")
    if not db_file.is_file():
        _check("sqlite file present", False, f"expected {db_file}")
        return
    _check("sqlite file present", True)

    conn = sqlite3.connect(str(db_file))
    try:
        cur = conn.cursor()

        # WAL journal mode (persistent once the app set it).
        row = cur.execute("PRAGMA journal_mode").fetchone()
        mode = row[0] if row else None
        _check("journal_mode = wal", (mode or "").lower() == "wal", f"got {mode!r}")

        # alembic_version stamped (present + a revision recorded).
        try:
            rev_row = cur.execute("SELECT version_num FROM alembic_version").fetchone()
            rev = rev_row[0] if rev_row else None
        except sqlite3.OperationalError:
            rev = None
        _check(
            "alembic_version stamped to a revision",
            rev is not None,
            f"got {rev!r} (head 0002)",
        )

        # The created user row.
        try:
            user = cur.execute(
                "SELECT username, role, active, password_hash FROM users WHERE username = ?",
                (_SMOKE_USERNAME,),
            ).fetchone()
        except sqlite3.OperationalError:
            user = None
        if user is None:
            _check("users row created", False, f"no row for {_SMOKE_USERNAME!r}")
        else:
            _username, _role, _active, _phash = user
            _check(
                "users row: role=admin, active",
                _role == "admin" and bool(_active),
                f"role={_role!r}, active={_active!r}",
            )
            _check(
                "password_hash is argon2id",
                isinstance(_phash, str) and _phash.startswith("$argon2id$"),
                f"got {str(_phash)[:24]!r}",
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Environment report (step 7)
# ---------------------------------------------------------------------------


def report_versions(venv_python: Path) -> None:
    """Print the environment the deployment ran on (step 7).

    Python and SQLite versions are read from the FRESH venv (where the app
    actually ran), not from the interpreter that launched this script."""
    print("\n  Environment:")

    def _venv_stdout(code: str) -> str:
        r = subprocess.run(
            [str(venv_python), "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        return (r.stdout or r.stderr).strip()

    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        head = r.stdout.strip() if r.returncode == 0 else "(not a git checkout)"
    except (OSError, subprocess.TimeoutExpired):
        head = "(git unavailable)"

    print(f"    HEAD commit : {head}")
    print(
        "    Python      : "
        f"{_venv_stdout('import sys; print(sys.version.split()[0])') or 'unknown'} (fresh venv)"
    )
    print(
        "    SQLite      : "
        f"{_venv_stdout('import sqlite3; print(sqlite3.sqlite_version)') or 'unknown'}"
    )
    print(
        "    sqlite-vec  : "
        + (
            _venv_stdout(
                "import importlib.util as u; s=u.find_spec('sqlite_vec'); "
                "print('installed' if s else 'not installed (Phase 3)')"
            )
            or "unknown"
        )
    )


# ---------------------------------------------------------------------------
# --live endpoint checks
# ---------------------------------------------------------------------------


def run_live_checks(base_url: str) -> None:
    """--live: confirm a running server's public surface is healthy."""
    print(f"\n  Endpoint checks against {base_url}:")

    # /healthz -> 200 ok
    code, body, _ = _http_get(f"{base_url}/healthz")
    _check(
        "GET /healthz -> 200 {status: ok}",
        code == 200 and _json_field(body, "status") == "ok",
        f"got {code}, body={body[:120]!r}",
    )

    # / -> a running server answers the root (200 served, 303 on redirect).
    code, _body, ctype = _http_get(f"{base_url}/")
    _check(
        "GET / -> 200/303 (root served)",
        code in (200, 303),
        f"got {code}, ctype={ctype!r}",
    )

    # /api/auth/first-run -> 200 (public; proves the API + DB layer is alive).
    code, body, _ = _http_get(f"{base_url}/api/auth/first-run")
    _check(
        "GET /api/auth/first-run -> 200 (API layer alive)",
        code == 200,
        f"got {code}, body={body[:120]!r}",
    )


# ---------------------------------------------------------------------------
# --clean mode
# ---------------------------------------------------------------------------


def run_clean() -> int:
    """Full end-to-end smoke test in a throwaway temp directory."""
    global _checks_passed, _checks_failed
    print("=" * 60)
    print(" LocalBachelor deployment smoke test (--clean)")
    print("=" * 60)

    # Safety: never touch the repo's config.yaml or data_dir.
    if (REPO_ROOT / "config.yaml").is_file():
        print("\n  Note: repo config.yaml exists — smoke test uses a temp copy.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="lb_smoke_"))
    print(f"\n  Temp dir: {tmp_dir}")

    venv_dir = tmp_dir / "venv"
    venv_python: Path | None = None
    app_proc: subprocess.Popen | None = None

    try:
        # --- Step 1: fresh venv + editable install (packaging guard) ---
        print("\n[1/7] Creating fresh Python 3.12 venv + installing the repo ...")
        venv_python = _create_venv(venv_dir)
        if venv_python is None:
            _check("create Python 3.12 venv", False, "uv / python3.12 unavailable")
            return 1
        _check("create Python 3.12 venv", True, str(venv_python))
        ok, detail = _install_editable(venv_python)
        _check("install -e . into fresh venv", ok, detail)
        if not ok:
            return 1

        # --- Step 2: temp config.yaml + alembic.ini ---
        print("\n[2/7] Writing temp config.yaml + alembic.ini ...")
        secret_key = secrets.token_hex(32)
        settings_key = secrets.token_hex(32)
        _check(
            "bootstrap keys >= 32 chars",
            len(secret_key) >= 32 and len(settings_key) >= 32,
            f"secret_key={len(secret_key)}B, settings_key={len(settings_key)}B",
        )
        port = _find_free_port()
        config_yaml = tmp_dir / "config.yaml"
        config_yaml.write_text(
            f'secret_key: "{secret_key}"\n'
            f'settings_key: "{settings_key}"\n'
            f'db_path: "local-bachelor.db"\n'
            f'data_dir: "{tmp_dir.as_posix()}"\n'
            f'host: "127.0.0.1"\n'
            f"port: {port}\n",
            encoding="utf-8",
        )
        _check("temp config.yaml written", config_yaml.is_file())

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

        db_file = tmp_dir / "local-bachelor.db"
        base_url = f"http://127.0.0.1:{port}"

        # --- Step 3: start WITHOUT migrations -> degraded /healthz (503) ---
        print("\n[3/7] Starting app WITHOUT migrations (preflight guard) ...")
        _remove_db(db_file)
        app_proc = _start_app(venv_python, config_yaml)
        code, body = _wait_for_response(base_url, timeout=30)
        _check(
            "unmigrated /healthz -> 503 degraded (missing)",
            code == 503
            and _json_field(body, "status") == "degraded"
            and _json_field(body, "schema") == "missing",
            f"got {code}, body={body[:120]!r}",
        )
        _stop_app(app_proc)
        app_proc = None

        # --- Step 4: alembic upgrade head -> restart -> healthy /healthz (200) ---
        print("\n[4/7] Running 'alembic upgrade head' + restarting ...")
        _remove_db(db_file)
        alembic_result = subprocess.run(
            [str(venv_python), "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        _check(
            "alembic upgrade head",
            alembic_result.returncode == 0,
            (alembic_result.stderr or alembic_result.stdout)[:300],
        )
        if alembic_result.returncode != 0:
            return 1
        _check("database file created", db_file.is_file(), f"expected {db_file}")

        app_proc = _start_app(venv_python, config_yaml)
        if not _wait_for_health(base_url, timeout=30):
            _stop_app(app_proc)
            app_proc = None
            _check("app restarted healthy", False, "healthz never returned 200")
            return 1
        code, body, _ = _http_get(f"{base_url}/healthz")
        _check(
            "migrated /healthz -> 200 ok",
            code == 200 and _json_field(body, "status") == "ok",
            f"got {code}, body={body[:120]!r}",
        )

        # --- Step 5: first-run bootstrap flow (real HTTP) ---
        print("\n[5/7] Driving the first-run bootstrap over HTTP ...")
        run_first_run_flow(base_url)
        _stop_app(app_proc)
        app_proc = None

        # --- Step 6: SQLite file inspection ---
        print("\n[6/7] Inspecting the SQLite file ...")
        run_sqlite_asserts(db_file)

        # --- Step 7: environment report ---
        print("\n[7/7] Environment report ...")
        report_versions(venv_python)

    except subprocess.TimeoutExpired:
        print("\n  [FAIL] Timeout waiting for a subprocess")
        _checks_failed += 1
    except Exception as e:  # noqa: BLE001 - report any unexpected failure
        print(f"\n  [FAIL] Unexpected error: {e}")
        _checks_failed += 1
    finally:
        if app_proc is not None and app_proc.poll() is None:
            print("\n  Stopping app ...")
            _stop_app(app_proc)
        print(f"\n  Removing temp dir: {tmp_dir}")
        shutil.rmtree(tmp_dir, ignore_errors=True)

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
    print(" LocalBachelor deployment smoke test (--live)")
    print(f" Target: {base_url}")
    print("=" * 60)

    if not _wait_for_health(base_url, timeout=10):
        print("  [FAIL] Server not reachable (healthz did not respond)")
        print(" SMOKE TEST FAILED")
        return 1
    _check("server reachable", True)

    run_live_checks(base_url)

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
