"""argon2id password hashing (architecture §13 security model).

Locked decision: argon2id via argon2-cffi. The variant is set explicitly
(Type.ID) so a future library default change cannot silently weaken it.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from argon2.low_level import Type

_hasher = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Check a candidate password against a stored hash (constant-time)."""
    try:
        return _hasher.verify(stored_hash, password)
    except Argon2Error:
        return False
