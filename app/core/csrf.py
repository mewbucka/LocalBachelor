"""Stateless double-submit CSRF cookie (roadmap 1.6, section 13).

The token is a stable random value, NOT signed: the ``lb_csrf`` cookie
holds a ``secrets.token_hex(32)`` value that is issued once (when the
cookie is absent) and REUSED on every later request. It is rotated only
when a session is ESTABLISHED (login / first-run / setup success) by
stashing a fresh value on ``request.state.csrf_cookie_token``; the CSRF
middleware in ``app/main.py`` then persists that value into the response,
so a cross-site page that captured the old token is left holding a stale
one.

The double-submit check — the single enforcement point, in the middleware
— compares the cookie against the token the client presents: the
``csrf_token`` form field for browser form posts, or the ``X-CSRF-Token``
header for JSON/HTMX calls. A cross-site attacker cannot read the
``lb_csrf`` cookie (same-origin only; ``SameSite=Lax`` also blocks it on
cross-site form POSTs), so they cannot forge a matching value, and
``hmac.compare_digest`` makes the comparison timing-safe. Nothing is
stored server-side, so no ``secret_key`` is needed and the token survives
reboots (it lives in the client's cookie).
"""

from __future__ import annotations

import hmac
import secrets

from jinja2 import pass_context
from starlette.requests import Request
from starlette.responses import Response

from app.core.auth import is_secure_request

#: Cookie carrying the CSRF token. Read by the page's JS (to populate the
#: X-CSRF-Token header) and compared server-side against the presented
#: token. ``httponly=False`` is deliberate — see ``set_cookie``.
CSRF_COOKIE = "lb_csrf"
#: Header a JSON/HTMX client sends the token in (double-submit).
CSRF_HEADER = "X-CSRF-Token"
#: Hidden form field a browser form posts the token in (double-submit).
CSRF_FORM_FIELD = "csrf_token"
#: The token is stable for the browser's lifetime of the cookie; a 30-day
#: max_age keeps the cookie from becoming a permanent artifact.
CSRF_COOKIE_TTL_SECONDS = 30 * 24 * 60 * 60


def is_form_encoded(request: Request) -> bool:
    """True for a form-encoded (browser) submission; else the JSON API path.

    A request with no content-type at all is NOT form-encoded (it is the
    token-less API path). Cross-site attacks can only POST a form-encoded
    body, so the double-submit check gates on exactly this.
    """
    ctype = request.headers.get("content-type", "").lower()
    return ctype.startswith("application/x-www-form-urlencoded") or ctype.startswith(
        "multipart/form-data"
    )


class CsrfManager:
    """Issues, reads, and validates the double-submit CSRF cookie."""

    def new_token(self) -> str:
        """Mint a fresh random token (used for rotation on session setup)."""
        return secrets.token_hex(32)

    def cookie_token(self, request: Request) -> str:
        """Return the request's CSRF token, stashing one if the cookie is new.

        Reuses the existing ``lb_csrf`` cookie so a token is stable across
        requests (no multi-tab race). When the cookie is absent it mints a
        token and stashes it on ``request.state.csrf_cookie_token`` for the
        middleware to persist on the response. If the cookie is absent but a
        token was already stashed earlier in THIS request (a page renders
        more than one ``csrf_token()``), that stashed value is reused so
        every form field and the response cookie all agree on one token.
        """
        token = request.cookies.get(CSRF_COOKIE)
        if token:
            return token
        stashed = getattr(request.state, "csrf_cookie_token", None)
        if stashed:
            return stashed
        token = self.new_token()
        request.state.csrf_cookie_token = token
        return token

    def set_cookie(self, response: Response, request: Request, token: str) -> None:
        """Set the ``lb_csrf`` cookie (JS-readable, ``Secure`` when over TLS).

        ``httponly=False`` on purpose: the page's JS reads the cookie to
        populate the ``X-CSRF-Token`` header. The value is only ever
        validated server-side against a presented copy (double-submit), and
        an attacker on another origin cannot read the cookie either, so JS
        visibility does not weaken it (section 13, task 1.6).
        """
        response.set_cookie(
            CSRF_COOKIE,
            token,
            max_age=CSRF_COOKIE_TTL_SECONDS,
            httponly=False,
            samesite="lax",
            secure=is_secure_request(request),
            path="/",
        )

    def validate_double_submit(self, request: Request, provided: str | None) -> bool:
        """True when the presented token matches the cookie (timing-safe).

        Fails closed: a missing cookie or a missing/blank presented token is
        False. The comparison uses ``hmac.compare_digest``.
        """
        expected = request.cookies.get(CSRF_COOKIE)
        if not expected or not provided:
            return False
        return hmac.compare_digest(expected, provided)


@pass_context
def jinja_csrf_token(context) -> str:
    """Jinja global ``csrf_token()``: the request's double-submit token.

    Templates (base.html logout form, login.html, setup.html, admin.html)
    read it into a hidden ``csrf_token`` field. It returns the STABLE cookie
    token (``cookie_token``) rather than a fresh value, so a token from a
    previously loaded page stays valid and the hidden field matches the
    cookie the double-submit check compares against.
    """
    request = context.get("request")
    if request is None:
        return ""
    manager: CsrfManager = request.app.state.csrf_store
    return manager.cookie_token(request)
