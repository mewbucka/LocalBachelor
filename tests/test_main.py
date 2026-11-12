"""App factory + startup preflight tests (roadmap 0.3 / fixup 0.3).

Covers: the migrated app's /healthz (200 ok), the degraded app's 503
responses (missing / outdated schema), the structured error shapes
(404 / 422 / 500), app state wiring, WAL mode, and the session factory.
"""

import json

import pytest
from fastapi import Query
from fastapi.testclient import TestClient

from app.core.auth import SessionManager
from app.core.preflight import SchemaStatus
from app.main import create_app
from app.models import Base
from tests.conftest import stamp_alembic_head


@pytest.fixture()
def app(config_file):
    """Migrated app: tables created + alembic_version stamped to head."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    stamp_alembic_head(application.state.engine)
    return application


@pytest.fixture()
def unmigrated_app(config_file):
    """Unmigrated app: no tables, no alembic_version (fresh DB)."""
    return create_app(config_file)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def unmigrated_client(unmigrated_app):
    with TestClient(unmigrated_app) as test_client:
        yield test_client


# -- /healthz -------------------------------------------------------------------


def test_healthz_migrated_returns_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_degraded_on_unmigrated(unmigrated_client):
    response = unmigrated_client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "schema": "missing"}


# -- schema guard: API routes ----------------------------------------------------


def test_api_route_degraded_on_unmigrated(unmigrated_client):
    response = unmigrated_client.get("/api/auth/me")
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "service_unavailable"
    assert body["schema"] == "missing"
    assert "correlation_id" in body
    assert response.headers["X-Request-ID"] == body["correlation_id"]


# -- schema guard: browser routes ------------------------------------------------


def test_browser_route_degraded_on_unmigrated(unmigrated_client):
    response = unmigrated_client.get("/login")
    assert response.status_code == 503
    assert "text/html" in response.headers["content-type"]
    # The localized message names the fix command.
    assert "alembic upgrade head" in response.text


# -- schema guard: outdated -------------------------------------------------------


def test_outdated_schema_degraded(config_file):
    """A DB stamped with a non-head revision is flagged outdated."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    # Stamp with the PARENT revision (0001) instead of head (0002).
    from sqlalchemy import text

    with application.state.engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001')"))

    with TestClient(application) as test_client:
        response = test_client.get("/healthz")
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "schema": "outdated"}


# -- schema state after lifespan --------------------------------------------------


def test_schema_state_after_lifespan(client, app):
    """After the TestClient context enters (lifespan runs), the schema
    state is stored on app.state."""
    assert app.state.schema_status == SchemaStatus.OK
    assert app.state.schema_head == "0002"
    assert app.state.schema_current == "0002"


# -- error shapes (migrated app) --------------------------------------------------


def test_404_json_with_incoming_correlation_id(client):
    response = client.get("/nope", headers={"X-Request-ID": "req-123"})
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found"
    assert body["correlation_id"] == "req-123"
    assert response.headers["X-Request-ID"] == "req-123"


def test_404_generates_correlation_id_when_header_absent(client):
    response = client.get("/nope")
    body = response.json()
    assert body["error"] == "not_found"
    assert body["correlation_id"]
    assert response.headers["X-Request-ID"] == body["correlation_id"]


def test_422_validation_error_shape(client, app):
    @app.get("/_test_validation")
    def _test_validation(x: int = Query(...)):
        return {"x": x}

    response = client.get("/_test_validation")
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert isinstance(body["detail"], list)
    assert body["detail"][0]["loc"][-1] == "x"
    assert body["correlation_id"]


def test_500_unhandled_exception_shape(app):
    @app.get("/_boom")
    def _boom():
        raise ValueError("kaput")

    # Starlette's ServerErrorMiddleware always re-raises the exception AFTER
    # the 500 response has been sent (so real servers can log it); the
    # TestClient re-raises it into the test unless told otherwise.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/_boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert body["correlation_id"]
    assert "kaput" not in json.dumps(body)  # exception detail must not leak


# -- app state + engine ------------------------------------------------------------


def test_app_state_wired(app):
    assert app.state.settings is not None
    assert app.state.engine is not None
    assert app.state.session_factory is not None
    assert isinstance(app.state.session_store, SessionManager)
    assert app.state.templates is not None  # task 1.3: shared i18n template env


def test_engine_connection_is_in_wal_mode(app):
    with app.state.engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_session_factory_returns_bound_session(app):
    session = app.state.session_factory()
    try:
        assert session.bind is app.state.engine
    finally:
        session.close()
