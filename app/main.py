"""LocalBachelor app factory (roadmap task 0.3).

Run:
    python -m app.main --config config.yaml
or:
    uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request

from app.api.auth import router as auth_router
from app.api.setup import router as setup_router
from app.config import load_settings
from app.core.auth import SessionManager, resolve_session_user_id
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.db.engine import create_session_factory, init_engine

logger = logging.getLogger(__name__)


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    """Build the FastAPI app from bootstrap config.

    Wires: structured logging, config, SQLite engine (WAL), the session
    stack (task 1.1: SessionManager + session middleware + login/logout),
    the first-run wizard (task 1.2: /setup page + /api/auth/first-run),
    /healthz, JSON error handlers.
    """
    setup_logging()

    settings = load_settings(config_path)
    logger.info("configuration loaded", extra={"config_path": str(config_path)})

    engine = init_engine(settings)
    session_factory = create_session_factory(engine)

    app = FastAPI(title="LocalBachelor", debug=False)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    # Task 1.1: the real session stack. Compact signed tokens (no
    # server-side session table); the signing key is config.yaml's
    # secret_key (roadmap rule 7).
    app.state.session_store = SessionManager(settings.secret_key)

    @app.middleware("http")
    async def session_middleware(request: Request, call_next):
        # Resolve the session cookie once per request; route dependencies
        # (get_current_user) read the result from request.state.
        request.state.session_user_id = resolve_session_user_id(request)
        return await call_next(request)

    register_error_handlers(app)
    app.include_router(auth_router)
    # Task 1.2: the HTML wizard page. Public while setup_needed; once any
    # user exists it 303-redirects to / (see app/api/setup.py).
    app.include_router(setup_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe (public, no auth)."""
        return {"status": "ok"}

    logger.info("app ready", extra={"host": settings.host, "port": settings.port})
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LocalBachelor server")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()
    app = create_app(args.config)
    settings = app.state.settings
    # log_config=None: keep the JSON logging setup from create_app() instead
    # of letting uvicorn re-install its own plain-text logging config.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
