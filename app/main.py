"""LocalBachelor app factory (roadmap task 0.3).

Run:
    python -m app.main --config config.yaml
or:
    uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.lang import router as lang_router
from app.api.pages import router as pages_router
from app.api.settings import router as settings_router
from app.api.settings_page import router as settings_page_router
from app.api.setup import router as setup_router
from app.api.theme import router as theme_router
from app.api.users import router as users_router
from app.config import load_settings
from app.core.auth import SessionManager, resolve_session_user_id
from app.core.csrf import CSRF_FORM_FIELD, CSRF_HEADER, CsrfManager, is_form_encoded
from app.core.errors import CORRELATION_HEADER, correlation_id_for, register_error_handlers
from app.core.logging import setup_logging
from app.core.preflight import MIGRATE_CMD, SchemaStatus, run_preflight
from app.core.theme import resolve_theme
from app.db.engine import create_session_factory, init_engine
from app.i18n import create_templates, request_locale, resolve_locale, translate
from app.repositories.users import UserRepository

logger = logging.getLogger(__name__)

#: Hosts that keep the instance on the local loopback interface. Any other
#: host is a LAN opt-in (section 13, task 1.6) that widens the attack
#: surface to the local network and warrants the startup warning + banner.
#: The default bind is 127.0.0.1, so the flag is off out of the box.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: Mutating HTTP methods the CSRF double-submit check enforces on.
#: Deny-by-default (fixup 1.6): a mutating request is protected the moment
#: its route lands; the only way to remove that protection is an explicit,
#: justified entry on the exemption list below — never an omission from a
#: positive protected list. See §17 item 9 and the coverage test in
#: tests/test_security_baseline.py.
_CSRF_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: (method, path) -> justification for entries exempt from CSRF enforcement.
#: Starts empty: a public GET such as /healthz needs no entry (it is not a
#: mutating method, so it is never enforced). Add an entry ONLY with a
#: non-empty justification of why the endpoint is safe to leave unprotected;
#: the coverage test in tests/test_security_baseline.py fails the gate if a
#: mutating route is exempt without a justification, or if a future mutating
#: route lands uncovered. A dict (not a set) so every exemption carries its
#: justification as the value.
_CSRF_EXEMPT: dict[tuple[str, str], str] = {}


def _csrf_exempt(method: str, path: str) -> bool:
    """True if (method, path) is on the explicit CSRF exemption list."""
    return (method, path) in _CSRF_EXEMPT


def _no_users(app: FastAPI) -> bool:
    """True while the instance has no accounts (first-run / setup pending)."""
    db = app.state.session_factory()
    try:
        return UserRepository(db, None).count() == 0
    finally:
        db.close()


def _form_field(form, key: str) -> str:
    """A form field as a string; a file upload or a missing field -> ""."""
    value = form.get(key)
    return value if isinstance(value, str) else ""


def _csrf_forbidden(request: Request) -> JSONResponse:
    """A failed double-submit check -> a structured 403 JSON response.

    A user middleware cannot ``raise HTTPException``: FastAPI's exception
    handlers live inside ExceptionMiddleware, which sits INNER to user
    middlewares, so the raise would escape as a 500. Return the structured
    response directly, mirroring the shape the error handlers produce
    (error / detail / correlation_id + the X-Request-ID echo), with the
    detail localized to the request's resolved locale.
    """
    cid = correlation_id_for(request)
    logger.info(
        "csrf check failed",
        extra={"correlation_id": cid, "path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=403,
        content={
            "error": "forbidden",
            "detail": translate("auth.csrf_invalid", request_locale(request)),
            "correlation_id": cid,
        },
        headers={CORRELATION_HEADER: cid},
    )


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    """Build the FastAPI app from bootstrap config.

    Wires: structured logging, config, SQLite engine (WAL), the session
    stack (task 1.1: SessionManager + session middleware + login/logout),
    the first-run wizard (task 1.2: /setup page + /api/auth/first-run),
    the i18n engine (task 1.3: en/uk catalogs + locale middleware +
    language switchers), the three-pane shell (task 1.4: light/dark theme
    middleware + /static + the login/home pages + the minimal settings and
    theme endpoints), the Phase 1 security baseline (task 1.6: conditional
    Secure cookies + the CSRF double-submit middleware + the LAN opt-in
    warning), /healthz, JSON error handlers.
    """
    setup_logging()

    settings = load_settings(config_path)
    logger.info("configuration loaded", extra={"config_path": str(config_path)})

    engine = init_engine(settings)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup: schema preflight. Shutdown: dispose engine."""
        result = run_preflight(engine)
        app.state.schema_status = result.status
        app.state.schema_head = result.head
        app.state.schema_current = result.current
        if result.status is not SchemaStatus.OK:
            logger.error(
                "schema preflight failed: %s (head=%s, current=%s) — run `%s`",
                result.status.value,
                result.head,
                result.current,
                MIGRATE_CMD,
            )
        yield
        engine.dispose()

    app = FastAPI(title="LocalBachelor", debug=False, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    # Task 1.1: the real session stack. Compact signed tokens (no
    # server-side session table); the signing key is config.yaml's
    # secret_key (roadmap rule 7).
    app.state.session_store = SessionManager(settings.secret_key)
    # Task 1.6: the double-submit CSRF cookie store (app/core/csrf.py).
    # Stateless — no secret_key (the token is an unsigned random value that
    # lives in the client's cookie); the middleware below enforces it.
    app.state.csrf_store = CsrfManager()
    # Task 1.3: the shared template env with the i18n globals (_(),
    # i18n_catalog_json(), jinja_locale(), csrf_token()); every HTML route
    # renders through it (architecture §14).
    app.state.templates = create_templates()
    # Task 1.6: the LAN opt-in flag (section 13). The default bind is
    # 127.0.0.1 (loopback only); any other host exposes the instance on the
    # local network, which we surface as a startup warning and a UI banner
    # so the operator is aware of the widened attack surface.
    lan_exposed = settings.host not in _LOOPBACK_HOSTS
    app.state.lan_exposed = lan_exposed
    if lan_exposed:
        logger.warning(
            "instance is reachable from the local network (LAN opt-in, "
            "architecture section 13)",
            extra={"host": settings.host, "port": settings.port},
        )

    # Middleware order. Starlette wraps user middlewares so the LAST
    # registered is OUTERMOST (runs first on the request path, last on the
    # response path). Registered in the order csrf, i18n, theme, session,
    # the request path is:
    #
    #   session -> theme -> i18n -> csrf -> routes
    #
    # - session runs first and sets request.state.session_user_id; theme and
    #   i18n (registered next) read it to decide whether to consult the DB,
    #   and i18n sets request.state.locale.
    # - csrf is registered FIRST, so it is INNERMOST: it runs after
    #   session/theme/i18n on the request path (so session_user_id and
    #   locale are already set — it needs both for a localized 403) and
    #   before them on the response path (so the cookie it sets is the
    #   first Set-Cookie on the wire).
    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        manager: CsrfManager = request.app.state.csrf_store
        path = request.url.path
        method = request.method

        # Deny-by-default (fixup 1.6): enforce the double-submit check on
        # every mutating method (POST/PUT/PATCH/DELETE) except a (method,
        # path) on the explicit exemption list. A new mutating route is
        # protected the moment it lands; only a deliberate, justified
        # exemption removes it (the coverage test in
        # tests/test_security_baseline.py fails the gate if a mutating route
        # is left uncovered). The token is accepted from the X-CSRF-Token
        # header (JSON) or the csrf_token form field (form POSTs).
        if method in _CSRF_MUTATING_METHODS and not _csrf_exempt(method, path):
            if is_form_encoded(request):
                # Form POSTs are enforced for ANY caller (a cross-site page
                # can auto-submit a form-encoded body, so this covers
                # anonymous login/first-run/setup and signed-in logout/admin
                # alike). /setup is enforced only while pre-setup — a
                # post-setup POST is a no-op 303 redirect that mutates
                # nothing, so it needs no token.
                if path != "/setup" or _no_users(request.app):
                    # Cache the raw body BEFORE parsing it. Starlette's
                    # BaseHTTPMiddleware only forwards a request body to the
                    # downstream app when body() was called on the dispatch
                    # request; request.form() reads via stream(), which leaves
                    # the body uncached and would starve the downstream route's
                    # own Form(...) fields (see _CachedRequest.wrapped_receive).
                    await request.body()
                    form = await request.form()
                    form_token = _form_field(form, CSRF_FORM_FIELD)
                    if not manager.validate_double_submit(request, form_token):
                        return _csrf_forbidden(request)
            else:
                # JSON (HTMX/fetch) mutating calls: enforce the X-CSRF-Token
                # header, but ONLY for a signed-in caller — an anonymous
                # caller is rejected by the route's own 401 before any state
                # is mutated, so it is not enforced here (401-first is
                # preserved).
                signed_in = getattr(request.state, "session_user_id", None) is not None
                if signed_in:
                    header_token = request.headers.get(CSRF_HEADER)
                    if not manager.validate_double_submit(request, header_token):
                        return _csrf_forbidden(request)

        response = await call_next(request)

        # Persist a (re)minted token: the jinja csrf_token() global stashes
        # one when the cookie is new (first render), and a login/first-run/
        # setup success stashes a ROTATED token. Only set the cookie when a
        # token was stashed, so a returning client's stable cookie is left
        # untouched (no needless re-issue, no multi-tab race).
        token = getattr(request.state, "csrf_cookie_token", None)
        if token is not None:
            manager.set_cookie(response, request, token)
        return response

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

    @app.middleware("http")
    async def schema_guard_middleware(request: Request, call_next):
        """Outermost middleware: short-circuit all traffic when the schema
        is degraded (missing / outdated) so inner middlewares (session,
        theme, i18n) never open DB sessions.

        - ``/healthz`` → 503 ``{"status":"degraded","schema":"…"}``
        - ``/api/*`` → 503 structured JSON (error / detail / schema /
          correlation_id + X-Request-ID header)
        - browser routes → 503 minimal localized HTML naming the fix

        Fail-closed (b2): if ``app.state.schema_status`` is absent — the
        preflight never populated it (a guard hit with no lifespan) — the
        guard treats the schema as MISSING (degraded), never OK, so a
        degraded DB cannot serve traffic on a healthy-by-default assumption.
        """
        status: SchemaStatus = getattr(
            request.app.state, "schema_status", SchemaStatus.MISSING
        )
        if status is SchemaStatus.OK:
            return await call_next(request)

        schema = status.value  # "missing" | "outdated"
        cid = correlation_id_for(request)
        # DB-free locale: the guard runs before session middleware, so
        # request.state.session_user_id is unset; resolve_locale with
        # db=None uses the anonymous chain (cookie → Accept-Language → en).
        locale = resolve_locale(request, None)

        if request.url.path == "/healthz":
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "schema": schema},
            )

        if request.url.path.startswith("/api/"):
            detail = translate("error.schema_degraded", locale)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "detail": detail,
                    "schema": schema,
                    "correlation_id": cid,
                },
                headers={CORRELATION_HEADER: cid},
            )

        # Browser routes: minimal localized HTML.
        title = escape(translate("error.schema_degraded_title", locale))
        message = escape(translate("error.schema_degraded", locale))
        html = (
            f'<!doctype html>\n<html lang="{locale}">\n'
            f'<head><meta charset="utf-8"><title>{title}</title></head>\n'
            f"<body>\n<h1>{title}</h1>\n<p>{message}</p>\n"
            f"</body>\n</html>"
        )
        return HTMLResponse(status_code=503, content=html, headers={CORRELATION_HEADER: cid})

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
    # Task 2.1: the /settings page (read-only settings skeleton over the typed
    # registry in app/core/settings_registry.py).
    app.include_router(settings_page_router)
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
