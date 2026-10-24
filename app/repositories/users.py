"""UserRepository: tenant-scoped user access (architecture sections 3, 11).

The users table is the identity layer, so its isolation rules differ from
content repositories:

- A plain user can only ever touch their OWN row (read / change password).
- Admin scope is administrative (section 11): create users, deactivate /
  reactivate users, reset any user's password, and list all accounts.
- Admins do NOT bypass tenant isolation to read other users' DATA
  (projects, sources, chunks, notes, jobs, settings) — that stays owner-
  scoped in the respective repositories (Phase 3+).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.passwords import hash_password, verify_password
from app.models import User

_VALID_ROLES = ("admin", "user")
_VALID_LANGS = ("en", "uk")


class UserRepository:
    """User reads/writes scoped to the acting user (or admin scope)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- authentication -------------------------------------------------

    def authenticate(self, username: str, password: str) -> User | None:
        """Return the active user matching (username, password), else None.

        Inactive accounts are rejected so deactivation immediately ends
        access. A wrong password and an unknown user are indistinguishable
        (both yield None) to avoid username enumeration.
        """
        user = self._session.scalar(select(User).where(User.username == username))
        if user is None or not user.active:
            return None
        if not verify_password(user.password_hash, password):
            return None
        return user

    # -- admin user management (first-run bootstrap + admin panel) ------

    def create(
        self,
        username: str,
        password: str,
        *,
        role: str = "user",
        lang: str = "en",
        owner_id: int | None = None,
    ) -> User:
        """Create a user.

        ``owner_id=None`` is the first-run bootstrap (no acting user yet,
        task 1.2). ``owner_id`` set to an admin id is the admin panel
        creating an account (task 1.5); a non-admin ``owner_id`` is
        rejected with PermissionError.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"invalid role: {role!r}")
        if lang not in _VALID_LANGS:
            raise ValueError(f"invalid lang: {lang!r}")
        if owner_id is not None:
            actor = self._session.get(User, owner_id)
            if actor is None or not actor.active or actor.role != "admin":
                raise PermissionError("only an active admin can create users")
        if self.get_by_username(username) is not None:
            raise ValueError(f"username already taken: {username!r}")
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            lang=lang,
            active=True,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def get_by_username(self, username: str) -> User | None:
        return self._session.scalar(select(User).where(User.username == username))

    def get_by_id(self, user_id: int, owner_id: int) -> User | None:
        """Return one account row only if the requester may see it.

        A user sees their own row. An active admin sees any account row
        (administrative scope, section 11). A plain user reading anyone
        else's row — including an admin's — gets None, never the row.
        """
        row = self._session.get(User, user_id)
        if row is None:
            return None
        if row.id == owner_id:
            return row
        actor = self._session.get(User, owner_id)
        if actor is not None and actor.active and actor.role == "admin":
            return row
        return None

    def list_all(self, owner_id: int) -> list[User]:
        """Admin-only listing of every account (the admin panel view).

        A non-admin caller gets an empty list, not the other tenants'
        accounts.
        """
        actor = self._session.get(User, owner_id)
        if actor is None or not actor.active or actor.role != "admin":
            return []
        return list(self._session.scalars(select(User).order_by(User.id)))

    def update_password(self, user_id: int, new_password: str, owner_id: int) -> bool:
        """Change a password: self-service, or an admin resetting it.

        Returns False (no change) when the target is missing or the
        requester is neither the owner nor an active admin.
        """
        row = self._session.get(User, user_id)
        if row is None:
            return False
        if row.id != owner_id:
            actor = self._session.get(User, owner_id)
            if actor is None or not actor.active or actor.role != "admin":
                return False
        row.password_hash = hash_password(new_password)
        self._session.flush()
        return True

    def set_active(self, user_id: int, active: bool, owner_id: int) -> bool:
        """Activate / deactivate an account. Administrative only (section 11).

        Deactivation is the users-table "delete" path: the row and all of
        that user's data stay intact, but ``authenticate`` rejects the
        account immediately. Only an active admin may change the flag (a
        user cannot self-deactivate).
        """
        row = self._session.get(User, user_id)
        if row is None:
            return False
        actor = self._session.get(User, owner_id)
        if actor is None or not actor.active or actor.role != "admin":
            return False
        row.active = active
        self._session.flush()
        return True
