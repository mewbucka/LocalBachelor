"""i18n engine tests (roadmap 1.3, architecture section 14).

Covers: en/uk catalog parity, ``translate`` (missing key / unsupported
locale raise), per-request locale resolution (users.lang → lb_lang cookie
→ Accept-Language uk* → default en), the Jinja ``_()`` global and the
embedded per-page catalog, localized error details (401 / 404 / 405 / 500
/ 409), the /setup page in the resolved locale, and both language
switchers (anonymous ``POST /lang``, authenticated
``PATCH /api/users/lang``).
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.core.csrf import CSRF_COOKIE
from app.i18n import LANG_COOKIE, load_catalog, translate
from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository

PASSWORD = "correct-horse-battery"


def _csrf(client) -> str:
    """The ``lb_csrf`` double-submit token (task 1.6) the client holds."""
    return client.cookies[CSRF_COOKIE]


def _register_probes(application: FastAPI) -> None:
    """Test-only routes: render the i18n globals / base.html directly."""

    @application.get("/_i18n_probe")
    def i18n_probe(request: Request) -> HTMLResponse:
        template = application.state.templates.env.from_string(
            '<html lang="{{ jinja_locale() }}">{{ _("setup.title") }}|{{ _("app.name") }}</html>'
        )
        return HTMLResponse(template.render(request=request))

    @application.get("/_base_probe")
    def base_probe(request: Request) -> HTMLResponse:
        return application.state.templates.TemplateResponse(request=request, name="base.html")

    @application.get("/_boom")
    def boom():
        raise ValueError("kaput")


@pytest.fixture()
def app(config_file) -> FastAPI:
    """Seeded instance: admin, alice (lang=en), petr (lang=uk)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    repo.create("petr", "petr-pass", role="user", lang="uk")
    session.commit()
    session.close()
    _register_probes(application)
    return application


@pytest.fixture()
def fresh_app(config_file) -> FastAPI:
    """Fresh instance (no users) for the /setup wizard + switcher tests."""
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


# -- catalogs + translate ----------------------------------------------------


def test_catalogs_have_identical_key_sets():
    en = load_catalog("en")
    uk = load_catalog("uk")
    assert en and set(en) == set(uk)
    assert all(isinstance(value, str) and value for value in en.values())


def test_translate_returns_catalog_value():
    assert translate("setup.title", "en") == "LocalBachelor setup"
    assert translate("setup.title", "uk") == "Налаштування LocalBachelor"


def test_translate_missing_key_raises():
    with pytest.raises(KeyError):
        translate("no.such.key", "en")


def test_translate_unsupported_locale_raises():
    with pytest.raises(KeyError):
        translate("app.name", "fr")


# -- locale resolution (users.lang → lb_lang cookie → Accept-Language → en) --


def test_anonymous_defaults_to_en(client):
    response = client.get("/_i18n_probe")
    assert 'lang="en"' in response.text
    assert "LocalBachelor setup" in response.text


def test_accept_language_uk_wins(client):
    response = client.get("/_i18n_probe", headers={"Accept-Language": "uk"})
    assert 'lang="uk"' in response.text
    assert "Налаштування LocalBachelor" in response.text


def test_accept_language_uk_region_tag_wins(client):
    response = client.get("/_i18n_probe", headers={"Accept-Language": "uk-UA,en;q=0.5"})
    assert 'lang="uk"' in response.text


def test_accept_language_ua_never_maps_to_uk(client):
    response = client.get("/_i18n_probe", headers={"Accept-Language": "ua"})
    assert 'lang="en"' in response.text


def test_accept_language_rejected_uk_ignored(client):
    response = client.get("/_i18n_probe", headers={"Accept-Language": "uk;q=0,en;q=1"})
    assert 'lang="en"' in response.text


def test_lb_lang_cookie_wins_over_accept_language(client):
    client.cookies.set(LANG_COOKIE, "uk")
    response = client.get("/_i18n_probe", headers={"Accept-Language": "en"})
    assert 'lang="uk"' in response.text


def test_invalid_lb_lang_cookie_ignored(client):
    client.cookies.set(LANG_COOKIE, "fr")
    response = client.get("/_i18n_probe")
    assert 'lang="en"' in response.text


def test_users_lang_overrides_cookie_and_accept_language(client):
    client.post("/api/auth/login", json={"username": "petr", "password": "petr-pass"})
    client.cookies.set(LANG_COOKIE, "en")
    response = client.get("/_i18n_probe", headers={"Accept-Language": "en"})
    assert 'lang="uk"' in response.text


def test_deactivated_user_lang_falls_back_to_cookie(client, app):
    client.post("/api/auth/login", json={"username": "petr", "password": "petr-pass"})
    session = app.state.session_factory()
    try:
        repo = UserRepository(session)
        petr = repo.get_by_username("petr")
        admin = repo.get_by_username("admin")
        repo.set_active(petr.id, False, owner_id=admin.id)
        session.commit()
    finally:
        session.close()
    client.cookies.set(LANG_COOKIE, "en")
    response = client.get("/_i18n_probe")
    assert 'lang="en"' in response.text


# -- /setup page in the resolved locale ---------------------------------------


def test_setup_page_defaults_to_english(fresh_client):
    response = fresh_client.get("/setup")
    assert response.status_code == 200
    html = response.text
    assert 'action="/setup"' in html
    assert "LocalBachelor setup" in html
    assert "Create the administrator account" in html
    # Single-locale rendering: the uk strings are absent (they are not even
    # in the embedded en catalog).
    assert "Ім'я користувача" not in html
    assert "Налаштування" not in html


def test_setup_page_in_uk_via_accept_language(fresh_client):
    html = fresh_client.get("/setup", headers={"Accept-Language": "uk"}).text
    assert "Налаштування LocalBachelor" in html
    assert "Створіть обліковий запис адміністратора" in html
    assert "Create the administrator account" not in html


def test_setup_page_embeds_catalog_for_js(fresh_client):
    html = fresh_client.get("/setup", headers={"Accept-Language": "uk"}).text
    assert 'id="i18n-catalog"' in html
    assert '"setup.title": "Налаштування LocalBachelor"' in html
    assert "window._" in html


def test_setup_error_rendered_localized(fresh_client):
    fresh_client.get("/setup")  # mint the lb_csrf double-submit cookie
    response = fresh_client.post(
        "/setup",
        data={
            "username": "boss",
            "password": "short",
            "lang": "en",
            "csrf_token": _csrf(fresh_client),
        },
        headers={"Accept-Language": "uk"},
    )
    assert response.status_code == 400
    assert '<p class="error">Пароль має містити щонайменше 8 символів</p>' in response.text
    assert "Password must be at least 8 characters" not in response.text


# -- language switchers --------------------------------------------------------


def test_anonymous_header_shows_lang_form(fresh_client):
    html = fresh_client.get("/setup").text
    assert 'action="/lang"' in html
    assert "/api/users/lang" not in html


def test_authenticated_header_uses_lang_api(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    html = client.get("/_base_probe").text
    assert "/api/users/lang" in html
    assert 'action="/lang"' not in html


def test_post_lang_sets_cookie_and_redirects_back(fresh_client):
    response = fresh_client.post(
        "/lang",
        data={"lang": "uk"},
        headers={"Referer": "http://testserver/setup"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"
    set_cookie = response.headers.get("set-cookie", "")
    assert "lb_lang=uk" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    # The cookie now drives the locale on the next request.
    assert 'lang="uk"' in fresh_client.get("/_i18n_probe").text


def test_post_lang_without_referer_redirects_to_root(fresh_client):
    response = fresh_client.post("/lang", data={"lang": "uk"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_post_lang_cross_origin_referer_ignored(fresh_client):
    response = fresh_client.post(
        "/lang",
        data={"lang": "uk"},
        headers={"Referer": "http://evil.example/setup"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_post_lang_tampered_value_falls_back_to_default(fresh_client):
    response = fresh_client.post("/lang", data={"lang": "fr"}, follow_redirects=False)
    assert "lb_lang=en" in response.headers.get("set-cookie", "")
    assert 'lang="en"' in fresh_client.get("/_i18n_probe").text


def test_post_lang_syncs_users_lang_when_signed_in(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    client.post("/lang", data={"lang": "uk"})
    session = app.state.session_factory()
    try:
        assert UserRepository(session).get_by_username("alice").lang == "uk"
    finally:
        session.close()


def test_patch_lang_requires_session(client):
    response = client.patch("/api/users/lang", json={"lang": "uk"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in"


def test_patch_lang_requires_session_localized(client):
    response = client.patch(
        "/api/users/lang", json={"lang": "uk"}, headers={"Accept-Language": "uk"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Ви не увійшли в систему"


def test_patch_lang_updates_preference_and_locale(client, app):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.patch(
        "/api/users/lang",
        json={"lang": "uk"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 200
    assert response.json()["lang"] == "uk"
    session = app.state.session_factory()
    try:
        assert UserRepository(session).get_by_username("alice").lang == "uk"
    finally:
        session.close()
    # users.lang now overrides the cookie on every page (architecture §14).
    client.cookies.set(LANG_COOKIE, "en")
    response = client.get("/_i18n_probe", headers={"Accept-Language": "en"})
    assert 'lang="uk"' in response.text


def test_patch_lang_invalid_rejected(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    response = client.patch(
        "/api/users/lang",
        json={"lang": "fr"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 422


# -- localized error details ----------------------------------------------------


def test_login_401_localized(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "nope"},
        headers={"Accept-Language": "uk"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Невірні дані входу"


def test_first_run_409_localized(client):
    response = client.post(
        "/api/auth/first-run",
        json={"username": "other", "password": PASSWORD},
        headers={"Accept-Language": "uk"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Налаштування вже завершено"


def test_404_localized(client):
    response = client.get("/nope", headers={"Accept-Language": "uk"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Не знайдено"


def test_405_localized(client):
    response = client.get("/lang", headers={"Accept-Language": "uk"})
    assert response.status_code == 405
    assert response.json()["detail"] == "Метод не дозволено"


def test_500_localized(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/_boom", headers={"Accept-Language": "uk"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Внутрішня помилка сервера"
