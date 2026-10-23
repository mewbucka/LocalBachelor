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

from app.config import load_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.db.engine import create_session_factory, init_engine

logger = logging.getLogger(__name__)


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    """Build the FastAPI app from bootstrap config.

    Wires: structured logging, config, SQLite engine (WAL), session
    middleware hook (real stack in Phase 1), /healthz, JSON error handlers.
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
    # Session middleware hook: the real session-cookie + argon2id stack
    # lands in Phase 1 (tasks 1.1-1.2). The placeholder keeps the wiring
    # point in place so Phase 1 replaces one function, not the stack.
    app.state.session_store = None

    @app.middleware("http")
    async def session_middleware(request: Request, call_next):
        request.state.user = None  # set by the Phase 1 session middleware
        return await call_next(request)

    register_error_handlers(app)

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
