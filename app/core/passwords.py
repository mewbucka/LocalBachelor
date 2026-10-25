"""argon2id password hashing (architecture §13 security model).

Locked decision: argon2id via argon2-cffi. The variant is set explicitly
(Type.ID) so a future library default change cannot silently weaken it.

Password policy (roadmap 1.2): minimum 8 characters. ``check_password_policy``
is the single enforcement point; the JSON API mirrors the bound on the
pydantic field (``MIN_PASSWORD_LENGTH``) and the first-run wizard page
calls it directly, so both reject short passwords with a clear error.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from argon2.low_level import Type

_hasher = PasswordHasher(type=Type.ID)

#: Password policy: minimum length (roadmap 1.2).
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Check a candidate password against a stored hash (constant-time)."""
    try:
        return _hasher.verify(stored_hash, password)
    except Argon2Error:
        return False


def check_password_policy(password: str) -> None:
    """Raise ValueError when the password violates the policy (min length)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
