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
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.lang import router as lang_router
from app.api.pages import router as pages_router
from app.api.settings import router as settings_router
from app.api.setup import router as setup_router
from app.api.theme import router as theme_router
from app.api.users import router as users_router
from app.config import load_settings
from app.core.auth import SessionManager, resolve_session_user_id
from app.core.csrf import CsrfManager
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.core.theme import resolve_theme
from app.db.engine import create_session_factory, init_engine
from app.i18n import create_templates, resolve_locale

logger = logging.getLogger(__name__)


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    """Build the FastAPI app from bootstrap config.

    Wires: structured logging, config, SQLite engine (WAL), the session
    stack (task 1.1: SessionManager + session middleware + login/logout),
    the first-run wizard (task 1.2: /setup page + /api/auth/first-run),
    the i18n engine (task 1.3: en/uk catalogs + locale middleware +
    language switchers), the three-pane shell (task 1.4: light/dark theme
    middleware + /static + the login/home pages + the minimal settings and
    theme endpoints), /healthz, JSON error handlers.
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
    # Task 1.4: the stateless signed CSRF store for the HTML forms that POST
    # to the JSON auth endpoints (login/logout); the full CSRF middleware is
    # task 1.6. Shares config.yaml's secret_key (roadmap rule 7).
    app.state.csrf_store = CsrfManager(settings.secret_key)
    # Task 1.3: the shared template env with the i18n globals (_(),
    # i18n_catalog_json(), jinja_locale(), csrf_token()); every HTML route
    # renders through it (architecture §14).
    app.state.templates = create_templates()

    # Task 1.3 + 1.4: resolve the request locale (users.lang → lb_lang
    # cookie → Accept-Language uk* → en) and the theme (per-user settings →
    # admin default → lb_theme cookie → light). Both are registered BEFORE
    # the session middleware below, which makes the session middleware the
    # OUTERMOST one (Starlette wraps user middlewares so the LAST registered
    # runs first) — by the time the locale/theme are resolved,
    # request.state.session_user_id is set (both need it to decide whether
    # to consult the database).
    @app.middleware("http")
    async def i18n_locale_middleware(request: Request, call_next):
        db = session_factory()
        try:
            request.state.locale = resolve_locale(request, db)
        finally:
            db.close()
        return await call_next(request)

    @app.middleware("http")
    async def theme_middleware(request: Request, call_next):
        db = session_factory()
        try:
            request.state.theme = resolve_theme(request, db)
        finally:
            db.close()
        return await call_next(request)

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
    # Task 1.3: language preferences (per-user + anonymous cookie) and the
    # header switchers.
    app.include_router(users_router)
    app.include_router(lang_router)
    # Task 1.4: the three-pane shell pages (/ and /login), the minimal
    # settings (PUT /api/settings) and theme (POST /theme) endpoints, and
    # the single static stylesheet.
    app.include_router(pages_router)
    app.include_router(settings_router)
    app.include_router(theme_router)
    # Task 1.5: the admin user-management page (/admin) and the admin-only
    # /api/admin/users endpoints (create / deactivate / reset password).
    app.include_router(admin_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

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
