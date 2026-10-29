"""Phase 1 security baseline tests (roadmap 1.6, architecture section 13).

End-to-end through the running app (the single enforcement point is the
CSRF middleware in ``app/main.py``):

- the double-submit ``lb_csrf`` cookie: bootstrapped on the first render of a
  form page, flagged ``SameSite=Lax`` and JS-readable (no ``httponly``),
  stable across requests (no re-mint), and ROTATED when a session is
  established;
- the middleware's 403 matrix for the HTMX JSON endpoints
  (``PUT /api/settings``, ``PATCH /api/users/lang``): anonymous -> 401 (the
  route's own check, which fires before any CSRF check since an anonymous
  caller cannot mutate state), signed-in with a missing/mismatched
  ``X-CSRF-Token`` -> structured 403 (+ the ``X-Request-ID`` echo), signed-in
  with a matching token -> the normal response;
- the pre-setup ``/setup`` form gate (403 without the double-submit token);
- the conditional ``Secure`` flag (absent over plain HTTP, present behind a
  TLS-terminating proxy via ``X-Forwarded-Proto``);
- the LAN opt-in: a non-loopback bind sets ``app.state.lan_exposed``, renders
  the ``lb-lan-warning`` banner, and logs a startup warning (loopback does
  none of this).
"""

import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.csrf import CSRF_COOKIE
from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository

# A double-submit value that can never match a real cookie.
_FAKE_TOKEN = "attacker-forged-token"


def _write_config(tmp_path: Path, host: str) -> Path:
    values = {
        "secret_key": "a" * 48,
        "settings_key": "b" * 48,
        "db_path": "local-bachelor.db",
        "data_dir": str(tmp_path),
        "host": host,
        "port": 8123,
    }
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(f"{key}: {json.dumps(value)}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return path


def _build_app(config: Path, *, seed: bool = True) -> tuple[FastAPI, list[logging.LogRecord]]:
    """Create the app, returning (app, the ``app.main`` log records).

    The LAN startup warning fires INSIDE ``create_app``, before pytest's
    root-level caplog window opens (and ``setup_logging`` replaces the root
    handlers anyway), so a handler is attached to the ``app.main`` logger
    directly to capture it.
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    app_logger = logging.getLogger("app.main")
    handler = _Capture()
    app_logger.addHandler(handler)
    try:
        application = create_app(config)
    finally:
        app_logger.removeHandler(handler)

    Base.metadata.create_all(application.state.engine)
    if seed:
        session = application.state.session_factory()
        repo = UserRepository(session)
        repo.create("admin", "admin-pass", role="admin")
        repo.create("alice", "alice-pass", role="user")
        session.commit()
        session.close()
    return application, records


@pytest.fixture()
def seeded_app(config_file) -> FastAPI:
    application, _ = _build_app(config_file, seed=True)
    return application


@pytest.fixture()
def fresh_app(config_file) -> FastAPI:
    application, _ = _build_app(config_file, seed=False)
    return application


@pytest.fixture()
def client(seeded_app):
    with TestClient(seeded_app) as test_client:
        yield test_client


@pytest.fixture()
def fresh_client(fresh_app):
    with TestClient(fresh_app) as test_client:
        yield test_client


def _lan_records(tmp_path: Path) -> list[logging.LogRecord]:
    _, records = _build_app(_write_config(tmp_path, "0.0.0.0"), seed=False)
    return records


def _lan_warning(records: list[logging.LogRecord]) -> bool:
    return any(
        record.levelno == logging.WARNING and "local network" in record.getMessage()
        for record in records
    )


# -- lb_csrf cookie: bootstrap, flags, stability, rotation --------------------


def test_csrf_cookie_bootstrapped_with_expected_flags(client):
    # A fresh visitor renders the login form; the response mints lb_csrf.
    response = client.get("/login")
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{CSRF_COOKIE}=" in set_cookie
    lower = set_cookie.lower()
    assert "samesite=lax" in lower
    # JS-readable: the page's JS reads it to populate X-CSRF-Token.
    assert "httponly" not in lower
    # NOT Secure over plain HTTP (the default local bind).
    assert "secure" not in lower


def test_csrf_cookie_stable_not_resent_when_present(client):
    client.get("/login")  # mint lb_csrf
    token = client.cookies[CSRF_COOKIE]
    # A returning request reuses the cookie: no re-mint, no re-issue.
    response = client.get("/login")
    assert f"{CSRF_COOKIE}=" not in response.headers.get("set-cookie", "")
    assert client.cookies[CSRF_COOKIE] == token


def test_csrf_cookie_rotates_on_session_establishment(client):
    client.get("/login")  # mint token A
    before = client.cookies[CSRF_COOKIE]
    response = client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    assert response.status_code == 200
    after = client.cookies[CSRF_COOKIE]
    # A successful login rotates the token: a cross-site page holding the
    # old value is left with a stale one.
    assert before and after and after != before


def test_csrf_cookie_secure_behind_tls_proxy(client):
    # A fresh client whose first request arrives behind a TLS-terminating
    # proxy (X-Forwarded-Proto: https) gets a Secure-flagged cookie.
    response = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{CSRF_COOKIE}=" in set_cookie
    assert "secure" in set_cookie.lower()


# -- CSRF middleware 403 matrix: HTMX JSON endpoints ---------------------------


def test_put_settings_anonymous_is_401_not_csrf(client):
    # Anonymous: the route's own 401 fires before any CSRF check (no session,
    # so no state can be mutated) — not a 403.
    response = client.put("/api/settings", json={"theme": "dark"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in"


def test_put_settings_signed_in_no_token_is_403(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put("/api/settings", json={"theme": "dark"})
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert response.json()["detail"] == "Session expired, please try again"


def test_put_settings_signed_in_mismatched_token_is_403(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings", json={"theme": "dark"}, headers={"X-CSRF-Token": _FAKE_TOKEN}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_put_settings_403_echoes_request_id(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put("/api/settings", json={"theme": "dark"})
    assert response.status_code == 403
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert response.json()["correlation_id"] == request_id


def test_put_settings_signed_in_matching_token_ok(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    assert response.json() == {"theme": "dark"}


def test_patch_lang_anonymous_is_401_not_csrf(client):
    response = client.patch("/api/users/lang", json={"lang": "uk"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in"


def test_patch_lang_signed_in_no_token_is_403(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.patch("/api/users/lang", json={"lang": "uk"})
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_patch_lang_signed_in_matching_token_ok(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.patch(
        "/api/users/lang",
        json={"lang": "uk"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    assert response.json()["lang"] == "uk"


# -- pre-setup /setup form gate ------------------------------------------------


def test_setup_form_without_token_is_403(fresh_client):
    # Pre-setup the /setup form is CSRF-enforced; no token -> 403.
    response = fresh_client.post(
        "/setup", data={"username": "boss", "password": "long-enough-1"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_setup_form_with_matching_token_succeeds(fresh_client):
    fresh_client.get("/setup")  # mint lb_csrf
    response = fresh_client.post(
        "/setup",
        data={
            "username": "boss",
            "password": "long-enough-1",
            "csrf_token": fresh_client.cookies[CSRF_COOKIE],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# -- LAN opt-in (section 13) ---------------------------------------------------


def test_lan_flag_and_banner_on_non_loopback(tmp_path):
    application, _ = _build_app(_write_config(tmp_path, "0.0.0.0"), seed=False)
    assert application.state.lan_exposed is True
    with TestClient(application) as test_client:
        html = test_client.get("/login").text
    assert 'class="lb-lan-warning"' in html
    assert "local network" in html


def test_no_lan_banner_on_loopback(config_file):
    application, _ = _build_app(config_file, seed=False)  # host=127.0.0.1
    assert application.state.lan_exposed is False
    with TestClient(application) as test_client:
        html = test_client.get("/login").text
    assert "lb-lan-warning" not in html


def test_lan_startup_warning_logged(tmp_path):
    assert _lan_warning(_lan_records(tmp_path)) is True


def test_loopback_startup_does_not_warn(config_file):
    _, records = _build_app(config_file, seed=False)
    assert _lan_warning(records) is False
