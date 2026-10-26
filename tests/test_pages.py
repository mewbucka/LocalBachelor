"""Page route tests (roadmap 1.4): the three-pane shell routes.

Covers the routing logic of ``GET /`` (signed in → the shell; anonymous on a
set-up instance → /login; anonymous on a fresh instance → /setup) and
``GET /login`` (anonymous → the localized form; signed in → /), plus the
shell's server-rendered structure (three panes, ``data-theme``) and the
localized empty states.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository


@pytest.fixture()
def app(config_file) -> FastAPI:
    """Seeded (set-up) instance: admin, alice."""
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
def fresh_app(config_file) -> FastAPI:
    """Fresh instance (no users)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def fresh_client(fresh_app):
    with TestClient(fresh_app) as test_client:
        yield test_client


# -- GET / ---------------------------------------------------------------------


def test_root_signed_in_renders_shell(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert 'class="lb-shell"' in html
    assert 'class="lb-sidebar"' in html
    assert 'class="lb-main"' in html
    assert 'class="lb-context"' in html
    assert "data-theme=" in html
    # The login form is not part of the app shell.
    assert 'action="/api/auth/login"' not in html


def test_root_anonymous_on_setup_instance_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_anonymous_on_fresh_instance_redirects_to_setup(fresh_client):
    response = fresh_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_home_page_shows_localized_empty_states(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    html = client.get("/").text
    assert "Welcome" in html
    assert "Create a project to get started" in html
    assert "No active conversation" in html


# -- GET /login ------------------------------------------------------------------


def test_login_page_anonymous_renders_form(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert 'action="/api/auth/login"' in html
    assert 'name="csrf_token"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert "Sign in" in html


def test_login_page_signed_in_redirects_to_root(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
