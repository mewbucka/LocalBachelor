"""Login / logout endpoint tests (roadmap 1.1, architecture section 12).

Covers the session-cookie lifecycle end to end: login sets the cookie
(httpOnly + SameSite=Lax), bad credentials yield the structured 401,
logout expires the cookie, and the session cookie resolves to the current
user for authenticated routes.
"""

import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient

from app.core.auth import SESSION_COOKIE, get_current_user
from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository


@pytest.fixture()
def app(config_file):
    application = create_app(config_file)
    # create_app does not create tables (alembic owns migrations); the test
    # creates them and seeds users directly.
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


def test_login_success_sets_session_cookie(client):
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["role"] == "user"
    assert body["lang"] == "en"
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_login_wrong_password_401(client):
    response = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_login_unknown_user_401(client):
    response = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert response.status_code == 401


def test_login_inactive_user_401(client, app):
    session = app.state.session_factory()
    repo = UserRepository(session)
    alice = repo.get_by_username("alice")
    admin = repo.get_by_username("admin")
    repo.set_active(alice.id, False, owner_id=admin.id)
    session.commit()
    session.close()
    response = client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    assert response.status_code == 401


def test_logout_expires_cookie(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in set_cookie and "max-age=0" in set_cookie.lower()


def test_session_cookie_resolves_current_user(client, app):
    @app.get("/_me")
    def me(user=Depends(get_current_user)):
        if user is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return {"username": user.username}

    # No session yet: the dependency fails closed -> 401.
    assert client.get("/_me").status_code == 401
    # After login the cookie resolves to the user.
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.get("/_me")
    assert response.status_code == 200
    assert response.json() == {"username": "alice"}
    # After logout the session is gone again.
    client.post("/api/auth/logout")
    assert client.get("/_me").status_code == 401
