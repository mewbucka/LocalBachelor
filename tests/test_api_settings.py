"""Settings endpoint tests (roadmap 1.4, architecture section 12).

Covers the task-1.4 subset ``PUT /api/settings``: session-only (401 for
anonymous callers, localized), the theme merge (updates the caller's own
settings row and returns the new value), and loud rejection of an empty body
(422 ``settings.no_settings``) and of unknown keys (422, ``extra="forbid"``).
The full settings surface (GET, typed defaults, masked provider keys, the
settings UI) is tasks 2.1/2.2/2.6.

Task 1.6: a signed-in caller must also send the double-submit
``X-CSRF-Token`` header (the ``lb_csrf`` cookie value) or the middleware
403s; an anonymous caller is rejected by the route's own 401 before any CSRF
check, so those tests stay header-less.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.csrf import CSRF_COOKIE
from app.main import create_app
from app.models import Base
from app.repositories.settings import SettingsRepository
from app.repositories.users import UserRepository
from tests.conftest import stamp_alembic_head


@pytest.fixture()
def app(config_file) -> FastAPI:
    """Seeded instance: admin, alice."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    stamp_alembic_head(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session, None)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def page_app(config_file) -> FastAPI:
    """Seeded instance with an en user (alice) and a uk-locale user (petr)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    stamp_alembic_head(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session, None)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    repo.create("petr", "petr-pass", role="user", lang="uk")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def page_client(page_app):
    with TestClient(page_app) as test_client:
        yield test_client


@pytest.fixture()
def fresh_app(config_file) -> FastAPI:
    """Fresh instance (no users)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    stamp_alembic_head(application.state.engine)
    return application


@pytest.fixture()
def fresh_client(fresh_app):
    with TestClient(fresh_app) as test_client:
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
    response = client.put(
        "/api/settings",
        json={},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "No settings provided"


def test_put_settings_unknown_key_422(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"theme": "dark", "bogus": 1},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 422


def test_put_settings_invalid_theme_422(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"theme": "neon"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 422


# -- happy path ------------------------------------------------------------------


def test_put_settings_updates_theme(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    assert response.json() == {"theme": "dark"}


def test_put_settings_persists_to_settings_row(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    client.put(
        "/api/settings",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    session = app.state.session_factory()
    try:
        alice = UserRepository(session, None).get_by_username("alice")
        assert SettingsRepository(session, alice.id, actor=alice).get("theme") == "dark"
    finally:
        session.close()


def test_put_settings_toggles_between_themes(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    token = client.cookies[CSRF_COOKIE]
    dark = client.put(
        "/api/settings",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": token},
    )
    assert dark.json() == {"theme": "dark"}
    light = client.put(
        "/api/settings",
        json={"theme": "light"},
        headers={"X-CSRF-Token": token},
    )
    assert light.json() == {"theme": "light"}
    session = app.state.session_factory()
    try:
        alice = UserRepository(session, None).get_by_username("alice")
        assert SettingsRepository(session, alice.id, actor=alice).get("theme") == "light"
    finally:
        session.close()


# -- GET /api/settings (roadmap 2.1) --------------------------------------------


def test_get_settings_anonymous_401(client):
    response = client.get("/api/settings")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in"


def test_get_settings_returns_every_registered_key(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    data = client.get("/api/settings").json()
    # A fresh user has no stored values: every key resolves to the code default.
    assert set(data) == {
        "theme",
        "top_k",
        "chat_base_url",
        "chat_model_id",
        "chat_api_key",
        "embedding_base_url",
        "embedding_model_id",
        "embedding_api_key",
        "embedding_dim",
        "embedding_prefix_query",
        "embedding_prefix_passage",
        "search_provider",
        "searxng_url",
        "chunk_target_tokens",
        "chunk_overlap_tokens",
    }
    assert data["theme"] == {"value": "light", "source": "code_default"}
    assert data["top_k"] == {"value": 5, "source": "code_default"}
    # an unset secret reads as null (never a leaked or masked value)
    assert data["chat_api_key"] == {"value": None, "source": "code_default"}


def test_get_settings_reflects_own_value(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    client.put(
        "/api/settings",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    data = client.get("/api/settings").json()
    assert data["theme"] == {"value": "dark", "source": "own"}


def test_get_settings_masks_admin_default_secret(client, app):
    # An admin publishes an admin-default API key; a user's read must mask it.
    session = app.state.session_factory()
    try:
        admin = UserRepository(session, None).get_by_username("admin")
        assert admin is not None
        assert SettingsRepository(session, None, actor=admin).set("chat_api_key", "sk-supersecret")
        session.commit()
    finally:
        session.close()
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.get("/api/settings")
    data = response.json()
    assert data["chat_api_key"] == {"value": "***", "source": "admin_default"}
    assert "sk-supersecret" not in response.text


# -- PUT /api/settings (roadmap 2.1, extended) ----------------------------------


def test_put_partial_merge_leaves_other_keys(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    client.put(
        "/api/settings",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    # A later PUT touching only top_k must not clobber the stored theme.
    client.put(
        "/api/settings",
        json={"top_k": 8},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    session = app.state.session_factory()
    try:
        alice = UserRepository(session, None).get_by_username("alice")
        assert alice is not None
        repo = SettingsRepository(session, alice.id, actor=alice)
        assert repo.get("theme") == "dark"
        assert repo.get("top_k") == 8
    finally:
        session.close()


def test_put_multiple_keys_echoes_each(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"theme": "dark", "top_k": 8},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    assert response.json() == {"theme": "dark", "top_k": 8}


def test_put_echoes_secret_masked(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"chat_api_key": "sk-personal"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    assert response.json() == {"chat_api_key": "***"}


def test_put_user_cannot_write_admin_default_403(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"chunk_target_tokens": 800},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have permission to change this setting"


def test_put_admin_can_write_admin_default(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
    response = client.put(
        "/api/settings",
        json={"chunk_target_tokens": 800, "chunk_overlap_tokens": 120},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    assert response.json() == {"chunk_target_tokens": 800, "chunk_overlap_tokens": 120}


def test_put_cross_field_overlap_gte_target_422(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
    response = client.put(
        "/api/settings",
        json={"chunk_target_tokens": 100, "chunk_overlap_tokens": 100},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Chunk overlap must be smaller than the target chunk size"


def test_put_cross_field_checked_against_current_target(client):
    # Admin publishes target=200; a later PUT of only overlap=250 must fail,
    # because the current effective target (200) is below 250.
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
    token = client.cookies[CSRF_COOKIE]
    ok = client.put(
        "/api/settings",
        json={"chunk_target_tokens": 200, "chunk_overlap_tokens": 60},
        headers={"X-CSRF-Token": token},
    )
    assert ok.status_code == 200
    bad = client.put(
        "/api/settings",
        json={"chunk_overlap_tokens": 250},
        headers={"X-CSRF-Token": token},
    )
    assert bad.status_code == 422


def test_put_out_of_bounds_422(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.put(
        "/api/settings",
        json={"top_k": 99},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid value"


# -- GET /settings (page, roadmap 2.1) ------------------------------------------


def test_settings_page_anonymous_redirects_to_login(client):
    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_settings_page_anonymous_fresh_redirects_to_setup(fresh_client):
    response = fresh_client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_settings_page_signed_in_renders_sections(page_client):
    page_client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = page_client.get("/settings")
    assert response.status_code == 200
    html = response.text
    assert 'class="lb-settings"' in html
    for title in ("Chat", "Embeddings", "Search", "Ingestion", "Interface"):
        assert title in html
    assert "Retrieved passages (top-k)" in html
    assert "API key" in html
    # a fresh user resolves every key to the code default -> the "Default" badge
    assert "Default" in html
    # the read-only hint is shown
    assert "read-only" in html


def test_settings_page_renders_in_uk(page_client):
    page_client.post("/api/auth/login", json={"username": "petr", "password": "petr-pass"})
    response = page_client.get("/settings")
    assert response.status_code == 200
    html = response.text
    for title in ("Чат", "Вбудовування", "Пошук", "Інгестія", "Інтерфейс"):
        assert title in html
    assert "Отримані фрагменти (top-k)" in html
    assert "Налаштування" in html


def test_settings_page_masks_secret_value(page_client, page_app):
    # Publish an admin-default API key; the page must show the mask, not the key.
    session = page_app.state.session_factory()
    try:
        admin = UserRepository(session, None).get_by_username("admin")
        assert admin is not None
        assert SettingsRepository(session, None, actor=admin).set("chat_api_key", "sk-page-secret")
        session.commit()
    finally:
        session.close()
    page_client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    html = page_client.get("/settings").text
    assert "sk-page-secret" not in html
    assert "***" in html
