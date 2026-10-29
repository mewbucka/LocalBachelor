"""First-run wizard tests (roadmap 1.2, architecture section 12).

Fresh-DB flow: GET /api/auth/first-run reports setup_needed; POST creates
the first account (role=admin, active, users.lang) with an argon2id hash,
issues the session cookie, and closes the endpoint (second POST → 409).
The /setup HTML wizard mirrors the same flow: the form renders in the
resolved locale (task 1.3, default en; uk variants are covered in
tests/test_i18n.py), POST creates the admin and 303-redirects to / with
the session cookie, short passwords are rejected with a localized error,
and the page redirects to / once an account exists.
"""

import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient

from app.core.auth import SESSION_COOKIE, get_current_user
from app.core.csrf import CSRF_COOKIE
from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository

PASSWORD = "correct-horse-battery"


def _csrf(client) -> str:
    """The ``lb_csrf`` double-submit token (task 1.6) the client holds."""
    return client.cookies[CSRF_COOKIE]


@pytest.fixture()
def app(config_file):
    application = create_app(config_file)
    # Fresh instance: tables created, NO users seeded (the wizard bootstraps
    # the first account; seeding would defeat the pre-setup state).
    Base.metadata.create_all(application.state.engine)

    @application.get("/_me")
    def me(user=Depends(get_current_user)):
        if user is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return {"username": user.username, "role": user.role, "lang": user.lang}

    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _user_count(app) -> int:
    session = app.state.session_factory()
    try:
        return UserRepository(session).count()
    finally:
        session.close()


# -- JSON API: /api/auth/first-run -----------------------------------------


def test_first_run_get_fresh_reports_setup_needed(client):
    response = client.get("/api/auth/first-run")
    assert response.status_code == 200
    assert response.json() == {"setup_needed": True}


def test_first_run_post_creates_admin_with_lang(client, app):
    response = client.post(
        "/api/auth/first-run",
        json={"username": "boss", "password": PASSWORD, "lang": "uk"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "boss"
    assert body["role"] == "admin"
    assert body["lang"] == "uk"
    session = app.state.session_factory()
    try:
        user = UserRepository(session).get_by_username("boss")
        assert user is not None
        assert user.role == "admin"
        assert user.lang == "uk"
        assert user.active is True
        assert user.password_hash.startswith("$argon2id$")
    finally:
        session.close()


def test_first_run_post_sets_working_session_cookie(client):
    response = client.post(
        "/api/auth/first-run",
        json={"username": "boss", "password": PASSWORD, "lang": "uk"},
    )
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    # The issued cookie resolves to the new admin on an authenticated route.
    me = client.get("/_me")
    assert me.status_code == 200
    assert me.json() == {"username": "boss", "role": "admin", "lang": "uk"}


def test_login_accepts_first_run_credentials(client):
    assert (
        client.post(
            "/api/auth/first-run",
            json={"username": "boss", "password": PASSWORD},
        ).status_code
        == 200
    )
    response = client.post(
        "/api/auth/login", json={"username": "boss", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_second_first_run_post_409_and_get_flips(client):
    first = client.post(
        "/api/auth/first-run", json={"username": "boss", "password": PASSWORD}
    )
    assert first.status_code == 200
    second = client.post(
        "/api/auth/first-run",
        json={"username": "other", "password": PASSWORD, "lang": "uk"},
    )
    assert second.status_code == 409
    assert second.json()["error"] == "conflict"
    assert client.get("/api/auth/first-run").json() == {"setup_needed": False}


def test_short_password_rejected_and_setup_stays_open(client, app):
    response = client.post(
        "/api/auth/first-run",
        json={"username": "boss", "password": "short", "lang": "en"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert any("password" in str(item.get("loc")) for item in body["detail"])
    assert _user_count(app) == 0
    assert client.get("/api/auth/first-run").json() == {"setup_needed": True}


# -- HTML wizard: /setup ----------------------------------------------------


def test_setup_page_renders_in_default_locale(client):
    response = client.get("/setup")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert 'action="/setup"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert 'name="lang"' in html
    # Task 1.3: single-locale rendering (default en); the uk strings are
    # absent — including from the embedded en catalog.
    assert "Username" in html
    assert "Ім'я користувача" not in html
    assert "Українська" not in html


def test_setup_page_post_creates_admin_and_redirects(client, app):
    client.get("/setup")  # mint the lb_csrf double-submit cookie
    response = client.post(
        "/setup",
        data={"username": "boss", "password": PASSWORD, "lang": "uk", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in response.headers.get("set-cookie", "")
    session = app.state.session_factory()
    try:
        user = UserRepository(session).get_by_username("boss")
        assert user is not None and user.role == "admin" and user.lang == "uk"
    finally:
        session.close()
    me = client.get("/_me")
    assert me.status_code == 200
    assert me.json() == {"username": "boss", "role": "admin", "lang": "uk"}


def test_setup_page_post_short_password_rejected(client, app):
    client.get("/setup")  # mint the lb_csrf double-submit cookie
    response = client.post(
        "/setup",
        data={"username": "boss", "password": "short", "lang": "en", "csrf_token": _csrf(client)},
    )
    assert response.status_code == 400
    assert '<p class="error">Password must be at least 8 characters</p>' in response.text
    assert _user_count(app) == 0


def test_setup_page_redirects_once_setup_done(client, app):
    client.get("/setup")  # mint the lb_csrf double-submit cookie
    first = client.post(
        "/setup",
        data={"username": "boss", "password": PASSWORD, "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert first.status_code == 303
    get_after = client.get("/setup", follow_redirects=False)
    assert get_after.status_code == 303
    assert get_after.headers["location"] == "/"
    # Post-setup the bootstrap is closed: a re-POST is a no-op 303 redirect
    # the middleware does NOT CSRF-enforce (/setup is gated on no users), so
    # it needs no token.
    post_again = client.post(
        "/setup",
        data={"username": "other", "password": PASSWORD},
        follow_redirects=False,
    )
    assert post_again.status_code == 303
    assert post_again.headers["location"] == "/"
    assert _user_count(app) == 1


# -- public surface ------------------------------------------------------------


def test_healthz_stays_public_on_fresh_instance(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_setup_when_fresh(client):
    # Task 1.4: the root route now exists. On a fresh (no-users) instance an
    # anonymous visitor is sent to the /setup wizard — the only entry point
    # before setup, since a login form could never succeed there.
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"
