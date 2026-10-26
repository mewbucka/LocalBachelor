"""Stateless signed CSRF tokens (roadmap 1.4; the full CSRF middleware
for mutating API/HTMX requests is task 1.6).

Task 1.4 ships the token for the two HTML forms that POST to the JSON
auth endpoints: the login form (POST /api/auth/login) and the header
logout form (POST /api/auth/logout). The tokens are COMPACT SIGNED
TOKENS, the same construction as the session tokens in
``app/core/auth.py``:

    base64url(payload) . base64url(HMAC-SHA256(payload, secret_key))

where ``payload = {"nonce": <hex>, "iat": <epoch s>, "exp": <epoch s>}``.
Validation is a timing-safe HMAC check plus an expiry check; nothing is
stored server-side. The token travels in a hidden form field (never a
cookie), so a cross-site page cannot read or mint it; ``secret_key``
(config.yaml, roadmap rule 7) is the signing key. Until the task-1.6
middleware lands, the auth endpoints enforce the token only on
form-encoded submissions (the HTML forms); plain JSON API calls stay
token-less.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from jinja2 import pass_context

#: A form token is valid for 15 minutes: long enough to fill a form,
#: short enough to bound the replay window.
CSRF_TTL_SECONDS = 15 * 60


def _b64url_encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64url_decode(data: bytes) -> bytes:
    padding = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + b"=" * padding)


class CsrfManager:
    """Issues and verifies stateless signed CSRF tokens."""

    def __init__(self, secret_key: str, ttl_seconds: int = CSRF_TTL_SECONDS) -> None:
        self._key = secret_key.encode("utf-8")
        self._ttl = ttl_seconds

    def issue(self) -> str:
        """Mint a fresh token (random nonce, iat/exp, HMAC-signed)."""
        now = int(time.time())
        payload = json.dumps(
            {"nonce": secrets.token_hex(16), "iat": now, "exp": now + self._ttl},
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return (_b64url_encode(payload) + b"." + _b64url_encode(signature)).decode("ascii")

    def validate(self, token: str | None) -> bool:
        """True when ``token`` is a valid, unexpired issued token.

        Fails closed: anything malformed, tampered, or expired is False.
        """
        if not token or "." not in token:
            return False
        payload_b64, _, signature_b64 = token.partition(".")
        try:
            payload = _b64url_decode(payload_b64.encode("ascii"))
            provided = _b64url_decode(signature_b64.encode("ascii"))
        except (ValueError, TypeError):
            return False
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, provided):
            return False
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(data, dict) or "exp" not in data:
            return False
        exp = data["exp"]
        if not isinstance(exp, int) or exp < time.time():
            return False
        return True


@pass_context
def jinja_csrf_token(context) -> str:
    """Jinja global ``csrf_token()``: a fresh signed token per render.

    The hidden fields in templates/base.html (header logout form) and
    templates/login.html read it. Every page render carries its own
    token, and all tokens stay valid until they expire (stateless), so a
    token from a previously loaded page still works within the TTL.
    """
    request = context.get("request")
    if request is None:
        return ""
    manager: CsrfManager = request.app.state.csrf_store
    return manager.issue()
