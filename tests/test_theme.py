"""Theme resolution + toggle tests (roadmap 1.4, architecture section 3).

Covers the per-request theme resolution (a signed-in user's own settings row
→ the admin-default row → the ``lb_theme`` cookie → the ``light`` default;
the settings table is consulted only when a session is present) and the
anonymous ``POST /theme`` switcher (stores the preference in the cookie and
303-redirects back; a tampered value falls back to the default; a session
syncs the user's own settings row). The resolved theme is server-rendered on
``<html data-theme="...">`` by the theme middleware — no flash-of-wrong-theme
script (the signed-in ``PUT /api/settings`` toggle is in test_api_settings.py).
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.core.theme import THEME_COOKIE
from app.main import create_app
from app.models import Base
from app.repositories.settings import SettingsRepository
from app.repositories.users import UserRepository


def _register_probes(application: FastAPI) -> None:
    """Test-only route: render the resolved theme off request.state."""

    @application.get("/_theme_probe")
    def theme_probe(request: Request) -> HTMLResponse:
        template = application.state.templates.env.from_string(
            '<html data-theme="{{ request.state.theme }}"></html>'
        )
        return HTMLResponse(template.render(request=request))


@pytest.fixture()
def app(config_file) -> FastAPI:
    """Seeded instance: admin, alice (no settings), petr (lang=uk)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session, None)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    repo.create("petr", "petr-pass", role="user", lang="uk")
    session.commit()
    session.close()
    _register_probes(application)
    return application


@pytest.fixture()
def fresh_app(config_file) -> FastAPI:
    """Fresh instance (no users, no settings rows)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    _register_probes(application)
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def fresh_client(fresh_app):
    with TestClient(fresh_app) as test_client:
        yield test_client


def _set_own_theme(app, username, theme) -> None:
    """Write a user's own settings row directly (isolates resolution)."""
    session = app.state.session_factory()
    try:
        user = UserRepository(session, None).get_by_username(username)
        SettingsRepository(session, user.id, actor=user).set("theme", theme)
        session.commit()
    finally:
        session.close()


def _set_admin_default_theme(app, username, theme) -> None:
    """Write the admin-default settings row (owner_id NULL) as an admin."""
    session = app.state.session_factory()
    try:
        admin = UserRepository(session, None).get_by_username(username)
        assert admin.role == "admin"
        SettingsRepository(session, None, actor=admin).set("theme", theme)
        session.commit()
    finally:
        session.close()


def _deactivate(app, username) -> None:
    session = app.state.session_factory()
    try:
        repo = UserRepository(session, None)
        user = repo.get_by_username(username)
        admin = repo.get_by_username("admin")
        UserRepository(session, admin.id).set_active_admin(user.id, False)
        session.commit()
    finally:
        session.close()


# -- resolution (per-user row → admin default → cookie → light) ---------------


def test_anonymous_defaults_to_light(client):
    assert 'data-theme="light"' in client.get("/_theme_probe").text


def test_anonymous_lb_theme_cookie_wins(client):
    client.cookies.set(THEME_COOKIE, "dark")
    assert 'data-theme="dark"' in client.get("/_theme_probe").text


def test_anonymous_invalid_lb_theme_cookie_ignored(client):
    client.cookies.set(THEME_COOKIE, "neon")
    assert 'data-theme="light"' in client.get("/_theme_probe").text


def test_signed_in_own_settings_row_wins(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    _set_own_theme(app, "alice", "dark")
    assert 'data-theme="dark"' in client.get("/_theme_probe").text


def test_signed_in_admin_default_fallback(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    _set_admin_default_theme(app, "admin", "dark")
    assert 'data-theme="dark"' in client.get("/_theme_probe").text


def test_signed_in_own_row_beats_admin_default(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    _set_own_theme(app, "alice", "light")
    _set_admin_default_theme(app, "admin", "dark")
    assert 'data-theme="light"' in client.get("/_theme_probe").text


def test_signed_in_settings_beats_cookie(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    _set_own_theme(app, "alice", "dark")
    client.cookies.set(THEME_COOKIE, "light")
    assert 'data-theme="dark"' in client.get("/_theme_probe").text


def test_deactivated_user_theme_falls_back_to_cookie(client, app):
    client.post("/api/auth/login", json={"username": "petr", "password": "petr-pass"})
    _deactivate(app, "petr")
    client.cookies.set(THEME_COOKIE, "dark")
    assert 'data-theme="dark"' in client.get("/_theme_probe").text


# -- anonymous toggle (POST /theme) -------------------------------------------


def test_post_theme_anonymous_sets_cookie_and_redirects_back(fresh_client):
    response = fresh_client.post(
        "/theme",
        data={"theme": "dark"},
        headers={"Referer": "http://testserver/setup"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{THEME_COOKIE}=dark" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    # The cookie now drives the theme on the next request.
    assert 'data-theme="dark"' in fresh_client.get("/_theme_probe").text


def test_post_theme_without_referer_redirects_to_root(fresh_client):
    response = fresh_client.post("/theme", data={"theme": "dark"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_post_theme_cross_origin_referer_ignored(fresh_client):
    response = fresh_client.post(
        "/theme",
        data={"theme": "dark"},
        headers={"Referer": "http://evil.example/setup"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_post_theme_tampered_value_falls_back_to_default(fresh_client):
    response = fresh_client.post("/theme", data={"theme": "neon"}, follow_redirects=False)
    assert f"{THEME_COOKIE}=light" in response.headers.get("set-cookie", "")
    assert 'data-theme="light"' in fresh_client.get("/_theme_probe").text


def test_post_theme_anonymous_does_not_create_settings_row(fresh_app, fresh_client):
    fresh_client.post("/theme", data={"theme": "dark"})
    session = fresh_app.state.session_factory()
    try:
        # Anonymous never writes a settings row (there is no user to attach
        # it to); the preference lives only in the cookie.
        assert SettingsRepository(session, None).get("theme") is None
    finally:
        session.close()


def test_post_theme_signed_in_syncs_settings_row(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    client.post("/theme", data={"theme": "dark"})
    session = app.state.session_factory()
    try:
        alice = UserRepository(session, None).get_by_username("alice")
        assert SettingsRepository(session, alice.id, actor=alice).get("theme") == "dark"
    finally:
        session.close()
