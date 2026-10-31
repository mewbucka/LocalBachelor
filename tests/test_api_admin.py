"""Admin user-management API + page tests (roadmap 1.5, architecture 11/12).

Administrative scope only: an active admin can list all accounts, create
users, deactivate/reactivate them, and reset their passwords. Anonymous ->
401, signed-in non-admin -> 403 (both localized). Every mutating endpoint is
dual-path (form-encoded browser path with CSRF + 303 redirect; JSON API
path with structured statuses). The last-active-admin safety guard returns
409 Conflict on the JSON path and re-renders /admin with a localized banner
on the form path.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository

_CSRF_FIELD = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _csrf_from(html: str) -> str:
    # Every form on a page carries the same double-submit value (the lb_csrf
    # cookie, task 1.6), so the first one found works for every form.
    match = _CSRF_FIELD.search(html)
    assert match is not None, "the page has no csrf_token hidden field"
    return match.group(1)


@pytest.fixture()
def app(config_file):
    """One admin + one user (the single-admin instance)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session, None)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def two_admins_app(config_file):
    """Two active admins + one user (to exercise the last-admin guard)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session, None)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("admin2", "admin2-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def uk_admin_app(config_file):
    """A uk-language admin (to verify the page renders in the user's locale)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session, None)
    repo.create("admin", "admin-pass", role="admin", lang="uk")
    repo.create("alice", "alice-pass", role="user")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def two_admin_client(two_admins_app):
    with TestClient(two_admins_app) as test_client:
        yield test_client


@pytest.fixture()
def uk_client(uk_admin_app):
    with TestClient(uk_admin_app) as test_client:
        yield test_client


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _id_of(client, username):
    listing = client.get("/api/admin/users").json()
    return next(u["id"] for u in listing if u["username"] == username)


# -- authorization ----------------------------------------------------------


def test_anonymous_list_users_401(client):
    response = client.get("/api/admin/users")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_non_admin_list_users_403(client):
    _login(client, "alice", "alice-pass")
    response = client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert response.json()["detail"] == "You do not have permission to view this page"


def test_anonymous_page_401(client):
    assert client.get("/admin").status_code == 401


def test_non_admin_page_403(client):
    _login(client, "alice", "alice-pass")
    response = client.get("/admin")
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_non_admin_cannot_create_403(client):
    _login(client, "alice", "alice-pass")
    response = client.post("/api/admin/users", json={"username": "eve", "password": "eve-pass-1"})
    assert response.status_code == 403


# -- list -------------------------------------------------------------------


def test_admin_list_users_shape_and_all_accounts(client):
    _login(client, "admin", "admin-pass")
    response = client.get("/api/admin/users")
    assert response.status_code == 200
    body = response.json()
    assert {u["username"] for u in body} == {"admin", "alice"}
    entry = next(u for u in body if u["username"] == "alice")
    assert entry["role"] == "user"
    assert entry["lang"] == "en"
    assert entry["active"] is True
    assert "created_at" in entry


# -- create (JSON path) -----------------------------------------------------


def test_admin_create_user_json_201(client):
    _login(client, "admin", "admin-pass")
    response = client.post(
        "/api/admin/users",
        json={"username": "carol", "password": "carol-pass", "role": "user", "lang": "uk"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "carol"
    assert body["role"] == "user"
    assert body["lang"] == "uk"
    assert body["active"] is True
    # The new account can actually sign in.
    with TestClient(client.app) as fresh:
        assert (
            fresh.post("/api/auth/login", json={"username": "carol", "password": "carol-pass"})
            .status_code
            == 200
        )


def test_admin_create_user_admin_role(client):
    _login(client, "admin", "admin-pass")
    response = client.post(
        "/api/admin/users",
        json={"username": "boss", "password": "boss-pass-1", "role": "admin"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_admin_create_user_duplicate_409(client):
    _login(client, "admin", "admin-pass")
    response = client.post(
        "/api/admin/users",
        json={"username": "alice", "password": "other-pass-1", "role": "user"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "conflict"
    assert response.json()["detail"] == "Username is already taken"


def test_admin_create_user_short_password_422(client):
    _login(client, "admin", "admin-pass")
    response = client.post("/api/admin/users", json={"username": "dave", "password": "short"})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


# -- create (form path) -----------------------------------------------------


def test_admin_create_user_form_success_redirects(client):
    _login(client, "admin", "admin-pass")
    token = _csrf_from(client.get("/admin").text)
    response = client.post(
        "/api/admin/users",
        data={
            "csrf_token": token,
            "username": "carol",
            "password": "carol-pass",
            "role": "user",
            "lang": "en",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    # The account now exists.
    assert "carol" in {u["username"] for u in client.get("/api/admin/users").json()}


def test_admin_create_user_form_missing_csrf_403(client):
    _login(client, "admin", "admin-pass")
    response = client.post(
        "/api/admin/users",
        data={"username": "carol", "password": "carol-pass"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_admin_create_user_form_tampered_role_falls_back_to_user(client):
    _login(client, "admin", "admin-pass")
    token = _csrf_from(client.get("/admin").text)
    response = client.post(
        "/api/admin/users",
        data={
            "csrf_token": token,
            "username": "eve",
            "password": "eve-pass-1",
            "role": "superuser",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = client.get("/api/admin/users").json()
    assert next(u for u in listing if u["username"] == "eve")["role"] == "user"


# -- deactivate / reactivate (JSON path) ------------------------------------


def test_admin_deactivate_user_json(client):
    _login(client, "admin", "admin-pass")
    alice_id = _id_of(client, "alice")
    response = client.patch(f"/api/admin/users/{alice_id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_admin_reactivate_user_json(client):
    _login(client, "admin", "admin-pass")
    alice_id = _id_of(client, "alice")
    assert client.patch(f"/api/admin/users/{alice_id}", json={"active": False}).status_code == 200
    response = client.patch(f"/api/admin/users/{alice_id}", json={"active": True})
    assert response.status_code == 200
    assert response.json()["active"] is True


def test_admin_deactivate_user_blocks_login(app, client):
    _login(client, "admin", "admin-pass")
    alice_id = _id_of(client, "alice")
    assert client.patch(f"/api/admin/users/{alice_id}", json={"active": False}).status_code == 200
    with TestClient(app) as fresh:
        assert (
            fresh.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
            .status_code
            == 401
        )


def test_admin_update_missing_target_404(client):
    _login(client, "admin", "admin-pass")
    response = client.patch("/api/admin/users/999999", json={"active": False})
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_admin_update_empty_body_422(client):
    _login(client, "admin", "admin-pass")
    alice_id = _id_of(client, "alice")
    response = client.patch(f"/api/admin/users/{alice_id}", json={})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


# -- reset password (JSON path) ---------------------------------------------


def test_admin_reset_password_json(app, client):
    _login(client, "admin", "admin-pass")
    alice_id = _id_of(client, "alice")
    response = client.patch(f"/api/admin/users/{alice_id}", json={"password": "brand-new-pass"})
    assert response.status_code == 200
    with TestClient(app) as fresh:
        # The old password no longer works; the new one does.
        assert (
            fresh.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
            .status_code
            == 401
        )
        assert (
            fresh.post("/api/auth/login", json={"username": "alice", "password": "brand-new-pass"})
            .status_code
            == 200
        )


# -- last-active-admin guard ------------------------------------------------


def test_admin_deactivate_last_admin_409(client):
    _login(client, "admin", "admin-pass")
    admin_id = _id_of(client, "admin")
    response = client.patch(f"/api/admin/users/{admin_id}", json={"active": False})
    assert response.status_code == 409
    assert response.json()["error"] == "conflict"
    assert response.json()["detail"] == "Cannot deactivate the last active administrator"


def test_admin_deactivate_ok_with_two_admins(two_admin_client):
    _login(two_admin_client, "admin", "admin-pass")
    admin2_id = _id_of(two_admin_client, "admin2")
    response = two_admin_client.patch(f"/api/admin/users/{admin2_id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False


# -- deactivate / reset (form path) -----------------------------------------


def test_admin_deactivate_form(two_admin_client):
    _login(two_admin_client, "admin", "admin-pass")
    token = _csrf_from(two_admin_client.get("/admin").text)
    alice_id = _id_of(two_admin_client, "alice")
    response = two_admin_client.post(
        f"/api/admin/users/{alice_id}",
        data={"csrf_token": token, "active": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = two_admin_client.get("/api/admin/users").json()
    assert next(u for u in listing if u["username"] == "alice")["active"] is False


def test_admin_deactivate_last_admin_form_renders_localized_error(client):
    _login(client, "admin", "admin-pass")
    token = _csrf_from(client.get("/admin").text)
    admin_id = _id_of(client, "admin")
    response = client.post(
        f"/api/admin/users/{admin_id}",
        data={"csrf_token": token, "active": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert "Cannot deactivate the last active administrator" in response.text


def test_admin_reset_password_form(client, app):
    _login(client, "admin", "admin-pass")
    token = _csrf_from(client.get("/admin").text)
    alice_id = _id_of(client, "alice")
    response = client.post(
        f"/api/admin/users/{alice_id}",
        data={"csrf_token": token, "password": "brand-new-pass"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with TestClient(app) as fresh:
        assert (
            fresh.post("/api/auth/login", json={"username": "alice", "password": "brand-new-pass"})
            .status_code
            == 200
        )


# -- page rendering, locale, sidebar ----------------------------------------


def test_admin_page_renders_en_table(client):
    _login(client, "admin", "admin-pass")
    html = client.get("/admin").text
    assert "User management" in html
    assert "alice" in html
    assert 'class="lb-admin-table"' in html
    assert 'name="csrf_token"' in html


def test_admin_page_renders_uk_strings(uk_client):
    _login(uk_client, "admin", "admin-pass")
    html = uk_client.get("/admin").text
    assert "Керування користувачами" in html
    assert "Скинути пароль" in html
    assert "Деактивувати" in html


def test_admin_link_visible_to_admin(client):
    _login(client, "admin", "admin-pass")
    assert 'href="/admin"' in client.get("/").text


def test_admin_link_hidden_to_user(client):
    _login(client, "alice", "alice-pass")
    assert 'href="/admin"' not in client.get("/").text
