"""UserRepository: tenant-scoped user access (architecture §3, §11, task 1.7).

The users table is the identity layer, so its isolation rules differ from
content repositories (the tenant is the row itself; the base
repository's ``tenant_column`` is the primary key):

- A plain user can only touch their OWN row (read, change password,
  change language): the constructor binds the acting user's id and the
  tenant-scoped methods operate on that row alone.
- Admin scope is administrative (§11): create users, deactivate /
  reactivate users, reset any user's password, and list all accounts —
  the explicit ``*_admin`` methods, each requiring the bound actor to be
  an active admin.
- Admins do NOT bypass tenant isolation to read other users' DATA
  (projects, sources, chunks, notes, jobs, settings) — that stays owner-
  scoped in the respective repositories (Phase 3+).
- First-run bootstrap (task 1.2) runs on a deliberately unbound instance
  (``owner_id=None``); ``create`` is valid only there.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.passwords import hash_password, verify_password
from app.models import User
from app.repositories.base import TenantScopedRepository, TenantScopeError

_VALID_ROLES = ("admin", "user")
_VALID_LANGS = ("en", "uk")


class AdminGuardViolation(Exception):
    """An administrative action would violate an admin-safety invariant.

    ``code`` is a stable machine-readable identifier the API layer maps to a
    localized message (e.g. ``"last_admin"`` -> ``admin.error.last_admin``).
    Raising (rather than returning False) keeps the guard distinct from the
    "no-op" outcomes so the API can answer 409 Conflict.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UserRepository(TenantScopedRepository):
    """User reads/writes scoped to the bound user (or admin scope)."""

    model = User
    tenant_column = User.id  # identity table: the tenant is the row itself

    # -- unscoped identity queries (not tenant content) -------------------

    def count(self) -> int:
        """Total number of user rows.

        The users table is the identity layer, not tenant content, so this
        is deliberately unscoped (§11's owner filters apply to the content
        repositories). First-run (task 1.2) uses it to detect an empty
        instance.
        """
        return self.session.scalar(select(func.count()).select_from(User)) or 0

    # -- authentication ---------------------------------------------------

    def authenticate(self, username: str, password: str) -> User | None:
        """Return the active user matching (username, password), else None.

        Inactive accounts are rejected so deactivation immediately ends
        access. A wrong password and an unknown user are indistinguishable
        (both yield None) to avoid username enumeration.
        """
        user = self.session.scalar(select(User).where(User.username == username))
        if user is None or not user.active:
            return None
        if not verify_password(user.password_hash, password):
            return None
        return user

    def get_by_username(self, username: str) -> User | None:
        """Public identity lookup (login, bootstrap, admin panel use).

        The username→row mapping is the identity plane, not tenant content:
        it is deliberately unscoped, like ``count``/``authenticate``.
        """
        return self.session.scalar(select(User).where(User.username == username))

    # -- tenant scope: the bound user's own row ---------------------------

    def get(self) -> User | None:
        """The bound user's own row, else None.

        The tenant is the row itself (``tenant_column`` is the primary
        key), so the scoped query returns a row only when it IS the bound
        account.
        """
        self._require_bound()
        return self.session.scalar(self._scoped(select(User)))

    def set_lang(self, lang: str) -> bool:
        """Change the bound user's own UI language (architecture §14).

        Self-service only: an account changes its OWN preference — there
        is deliberately NO admin bypass (unlike the ``*_admin`` methods),
        because a language preference is personal, not an administrative
        act. Returns False (no change) when the bound row is missing.
        """
        if lang not in _VALID_LANGS:
            raise ValueError(f"invalid lang: {lang!r}")
        row = self.get()
        if row is None:
            return False
        row.lang = lang
        self.session.flush()
        return True

    def update_password(self, new_password: str) -> bool:
        """Change the bound user's own password (self-service).

        Returns False (no change) when the bound row is missing. An admin
        resetting ANOTHER account's password uses ``update_password_admin``.
        """
        row = self.get()
        if row is None:
            return False
        row.password_hash = hash_password(new_password)
        self.session.flush()
        return True

    # -- bootstrap + admin scope (administrative, §11) ---------------------

    def create(
        self,
        username: str,
        password: str,
        *,
        role: str = "user",
        lang: str = "en",
    ) -> User:
        """Create the FIRST account (first-run bootstrap, task 1.2).

        Valid only on a deliberately unbound instance (``owner_id=None`` —
        there is no acting user yet). Once a tenant is bound, account
        creation is administrative work and goes through ``create_admin``;
        calling ``create`` on a bound instance raises ``TenantScopeError``
        (loudly, not a silent no-op).
        """
        if self._owner_id is not None:
            raise TenantScopeError(
                "account creation on a bound tenant is administrative: "
                "use create_admin"
            )
        return self._insert_user(username, password, role=role, lang=lang)

    def create_admin(
        self,
        username: str,
        password: str,
        *,
        role: str = "user",
        lang: str = "en",
    ) -> User:
        """Create an account (admin panel, task 1.5). Administrative only.

        The bound actor must be an active admin; a non-admin (or unbound)
        actor gets PermissionError.
        """
        self._require_admin()
        return self._insert_user(username, password, role=role, lang=lang)

    def get_admin(self, user_id: int) -> User | None:
        """One account row (administrative scope, §11). Admin only."""
        self._require_admin()
        return self.session.get(User, user_id)

    def list_all_admin(self) -> list[User]:
        """Admin-only listing of every account (the admin panel view).

        A non-admin (or unbound) actor gets PermissionError, never a
        partial list.
        """
        self._require_admin()
        return list(self.session.scalars(select(User).order_by(User.id)))

    def set_active_admin(self, user_id: int, active: bool) -> bool:
        """Activate / deactivate an account. Administrative only (§11).

        Deactivation is the users-table "delete" path: the row and all of
        that user's data stay intact, but ``authenticate`` rejects the
        account immediately. A user cannot self-deactivate (a plain user's
        instance gets PermissionError from the guard).

        Safety guard (task 1.5): the last remaining ACTIVE admin may never
        be deactivated — doing so would leave the instance with no one able
        to administer it (an admin who hands over would be locked out with
        no successor, and nobody could ever re-enable the account). An admin
        may deactivate THEMSELVES only when at least one other active admin
        remains. Reactivation is never blocked: restoring an account can
        never orphan the instance. Returns False (no change) when the target
        is missing; a non-admin actor raises PermissionError.
        """
        self._require_admin()
        row = self.session.get(User, user_id)
        if row is None:
            return False
        if not active and row.active and row.role == "admin" and self._active_admin_count() == 1:
            raise AdminGuardViolation("last_admin")
        row.active = active
        self.session.flush()
        return True

    def update_password_admin(self, user_id: int, new_password: str) -> bool:
        """Reset any account's password. Administrative only (§11).

        Returns False (no change) when the target row is missing; a
        non-admin (or unbound) actor raises PermissionError.
        """
        self._require_admin()
        row = self.session.get(User, user_id)
        if row is None:
            return False
        row.password_hash = hash_password(new_password)
        self.session.flush()
        return True

    # -- guards / helpers ---------------------------------------------------

    def _require_admin(self) -> None:
        """The bound actor must be an active admin, else PermissionError."""
        actor = self.session.get(User, self._owner_id) if self._owner_id is not None else None
        if actor is None or not actor.active or actor.role != "admin":
            raise PermissionError("admin scope required")

    def _insert_user(self, username: str, password: str, *, role: str, lang: str) -> User:
        if role not in _VALID_ROLES:
            raise ValueError(f"invalid role: {role!r}")
        if lang not in _VALID_LANGS:
            raise ValueError(f"invalid lang: {lang!r}")
        if self.get_by_username(username) is not None:
            raise ValueError(f"username already taken: {username!r}")
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            lang=lang,
            active=True,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def _active_admin_count(self) -> int:
        """Number of active admin accounts (the admin-safety invariant)."""
        return (
            self.session.scalar(
                select(func.count()).select_from(User).where(
                    User.role == "admin", User.active.is_(True)
                )
            )
            or 0
        )
