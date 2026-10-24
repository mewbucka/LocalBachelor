"""Session tokens + current-user dependency (architecture section 13, roadmap 1.1).

Session tokens are COMPACT SIGNED TOKENS, not opaque server-side rows:

    base64url(payload) . base64url(HMAC-SHA256(payload, secret_key))

where ``payload = {"uid": <int>, "iat": <epoch s>, "exp": <epoch s>}``.
The server verifies the HMAC (timing-safe) and the expiry and keeps no
server-side session state — consistent with the single-DB, no-external-
service topology (architecture section 15). ``secret_key`` (config.yaml,
roadmap rule 7) is the signing key.

Cookie flags: the session cookie is set ``httpOnly`` + ``SameSite=Lax``
here, per the Phase 1 security baseline (section 13). The ``Secure`` flag,
session-fixation handling, and the CSRF double-submit middleware are
task 1.6.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User

SESSION_COOKIE = "lb_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _b64url_encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64url_decode(data: bytes) -> bytes:
    padding = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + b"=" * padding)


class SessionManager:
    """Issues and verifies compact signed session tokens."""

    def __init__(self, secret_key: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._key = secret_key.encode("utf-8")
        self._ttl = ttl_seconds

    def issue(self, user_id: int) -> str:
        """Mint a signed token for the given user id."""
        now = int(time.time())
        payload = json.dumps(
            {"uid": user_id, "iat": now, "exp": now + self._ttl},
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return (_b64url_encode(payload) + b"." + _b64url_encode(signature)).decode("ascii")

    def validate(self, token: str | None) -> int | None:
        """Return the user id for a valid, unexpired token; else None.

        Fails closed: any malformed, tampered, or expired token yields
        None rather than an exception.
        """
        if not token or "." not in token:
            return None
        payload_b64, _, signature_b64 = token.partition(".")
        try:
            payload = _b64url_decode(payload_b64.encode("ascii"))
            provided = _b64url_decode(signature_b64.encode("ascii"))
        except (ValueError, TypeError):
            return None
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, provided):
            return None
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return None
        uid = data.get("uid")
        exp = data.get("exp")
        if not isinstance(uid, int) or not isinstance(exp, int):
            return None
        if exp < time.time():
            return None
        return uid

    def set_cookie(self, response: Response, user_id: int) -> None:
        """Attach the session cookie (httpOnly + SameSite=Lax, section 13)."""
        response.set_cookie(
            SESSION_COOKIE,
            self.issue(user_id),
            max_age=self._ttl,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def clear_cookie(self, response: Response) -> None:
        """Expire the session cookie on logout."""
        response.delete_cookie(SESSION_COOKIE, path="/")


def resolve_session_user_id(request: Request) -> int | None:
    """Verify the session cookie and return the user id (or None).

    Called once per request by the session middleware, which stores the
    result on ``request.state`` for route dependencies to read.
    """
    manager: SessionManager = request.app.state.session_store
    return manager.validate(request.cookies.get(SESSION_COOKIE))


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """FastAPI dependency: the authenticated, active user (or None).

    Reads the user id the session middleware resolved from the cookie,
    then loads the account. Returns None (fail-closed) when the token is
    missing/invalid/expired or the account has been deactivated; routes
    that REQUIRE authentication translate None into a 401.
    """
    user_id = getattr(request.state, "session_user_id", None)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or not user.active:
        return None
    return user
