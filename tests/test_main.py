import json

import pytest
from fastapi import Query
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def app(config_file):
    return create_app(config_file)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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


def test_app_state_wired(app):
    assert app.state.settings is not None
    assert app.state.engine is not None
    assert app.state.session_factory is not None
    assert app.state.session_store is None  # configured in Phase 1


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
