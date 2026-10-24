"""Session token tests (roadmap 1.1, architecture section 13).

Compact signed tokens: round-trip, tamper rejection, expiry, wrong-key
rejection, and the cookie flags (httpOnly + SameSite=Lax).
"""

import base64
import json

from fastapi import Response

from app.core.auth import SESSION_COOKIE, SessionManager


def _manager() -> SessionManager:
    return SessionManager("k" * 48)


def _decode_payload(token: str) -> dict:
    payload_b64 = token.partition(".")[0]
    padding = (-len(payload_b64)) % 4
    return json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * padding))


def _reencode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_issue_validate_roundtrip():
    manager = _manager()
    assert manager.validate(manager.issue(42)) == 42


def test_validate_rejects_tampered_payload():
    manager = _manager()
    token = manager.issue(42)
    _, _, signature = token.partition(".")
    payload = _decode_payload(token)
    payload["uid"] = 43  # re-encode without re-signing
    forged = _reencode(payload) + "." + signature
    assert manager.validate(forged) is None


def test_validate_rejects_tampered_signature():
    manager = _manager()
    token = manager.issue(42)
    payload_b64, _, sig_b64 = token.partition(".")
    padding = (-len(sig_b64)) % 4
    sig = bytearray(base64.urlsafe_b64decode(sig_b64 + "=" * padding))
    sig[0] ^= 0xFF
    forged = payload_b64 + "." + base64.urlsafe_b64encode(bytes(sig)).rstrip(b"=").decode()
    assert manager.validate(forged) is None


def test_validate_rejects_token_signed_with_other_key():
    other = SessionManager("b" * 48)
    assert _manager().validate(other.issue(7)) is None


def test_validate_rejects_expired_token():
    manager = SessionManager("k" * 48, ttl_seconds=-10)  # already expired
    assert manager.validate(manager.issue(9)) is None


def test_validate_rejects_garbage():
    manager = _manager()
    assert manager.validate(None) is None
    assert manager.validate("") is None
    assert manager.validate("not-a-token") is None
    assert manager.validate("aaa.bbb") is None


def test_set_cookie_flags():
    manager = _manager()
    response = Response()
    manager.set_cookie(response, 5)
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_clear_cookie_expires_it():
    manager = _manager()
    response = Response()
    manager.set_cookie(response, 5)
    manager.clear_cookie(response)
    cookies = response.headers.getlist("set-cookie")
    assert any(SESSION_COOKIE in c and "max-age=0" in c.lower() for c in cookies)
