"""Settings endpoint tests (roadmap 1.4, architecture section 12).

Covers the task-1.4 subset ``PUT /api/settings``: session-only (401 for
anonymous callers, localized), the theme merge (updates the caller's own
settings row and returns the new value), and loud rejection of an empty body
(422 ``settings.no_settings``) and of unknown keys (422, ``extra="forbid"``).
The full settings surface (GET, typed defaults, masked provider keys, the
settings UI) is tasks 2.1/2.2/2.6.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base
from app.repositories.settings import SettingsRepository
from app.repositories.users import UserRepository


@pytest.fixture()
def app(config_file) -> FastAPI:
    """Seeded instance: admin, alice."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


# -- auth gate -------------------------------------------------------------------


def test_put_settings_anonymous_401(client):
    response = client.put("/api/settings", json={"theme": "dark"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in"


def test_put_settings_anonymous_401_localized(client):
    response = client.put(
        "/api/settings", json={"theme": "dark"}, headers={"Accept-Language": "uk"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Ви не увійшли в систему"


# -- validation ------------------------------------------------------------------


def test_put_settings_empty_body_422(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put("/api/settings", json={})
    assert response.status_code == 422
    assert response.json()["detail"] == "No settings provided"


def test_put_settings_unknown_key_422(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put("/api/settings", json={"theme": "dark", "bogus": 1})
    assert response.status_code == 422


def test_put_settings_invalid_theme_422(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put("/api/settings", json={"theme": "neon"})
    assert response.status_code == 422


# -- happy path ------------------------------------------------------------------


def test_put_settings_updates_theme(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put("/api/settings", json={"theme": "dark"})
    assert response.status_code == 200
    assert response.json() == {"theme": "dark"}


def test_put_settings_persists_to_settings_row(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    client.put("/api/settings", json={"theme": "dark"})
    session = app.state.session_factory()
    try:
        alice = UserRepository(session).get_by_username("alice")
        assert SettingsRepository(session).get(alice.id, "theme") == "dark"
    finally:
        session.close()


def test_put_settings_toggles_between_themes(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    assert client.put("/api/settings", json={"theme": "dark"}).json() == {"theme": "dark"}
    assert client.put("/api/settings", json={"theme": "light"}).json() == {"theme": "light"}
    session = app.state.session_factory()
    try:
        alice = UserRepository(session).get_by_username("alice")
        assert SettingsRepository(session).get(alice.id, "theme") == "light"
    finally:
        session.close()
