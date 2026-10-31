"""SettingsRepository: owner-scoped settings access (architecture §3, §11, task 1.7).

Per-user settings with admin-default fallback (architecture §3):
``owner_id`` is the owning user's id, or NULL for the ADMIN DEFAULT row.
Isolation (architecture §11, task 1.7) — the constructor binds the
repository to a tenant:

- ``owner_id`` set (a personal namespace): the actor MUST be the owner
  (and active). Even an admin cannot bind another user's personal
  settings — settings are personal, mirroring UserRepository.set_lang's
  no-admin-bypass rule.
- ``owner_id`` None (the admin-default namespace): readable by all (it is
  the §3 fallback); writable by an active admin only.

A user reads the admin-default row only as a fallback (``get_effective``).
Values are typed JSON (architecture §3), parsed on read and serialized on
write.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting, User
from app.models.base import utc_now_iso
from app.repositories.base import TenantScopedRepository


class SettingsRepository(TenantScopedRepository):
    """Owner-scoped settings reads/writes; values are typed JSON."""

    model = Setting
    tenant_column = Setting.owner_id  # None = the admin-default namespace

    def __init__(self, session: Session, owner_id: int | None, actor: User | None = None) -> None:
        super().__init__(session, owner_id)
        # The bound actor, kept for the admin-default write check in ``set``
        # (call sites bind it here, then call the methods without repeating
        # it).
        self._actor = actor
        if owner_id is not None:
            # A personal namespace binds to its owner only: this is what
            # stops an admin (or anyone) from reading or writing another
            # user's personal settings (architecture §11).
            if actor is None or actor.id != owner_id or not actor.active:
                raise PermissionError(
                    "settings are personal: a personal namespace can only "
                    "be bound by its owner"
                )

    def get(self, key: str) -> object | None:
        """The parsed JSON value of the bound tenant's (owner, key) row.

        On an unbound repository the bound namespace is the admin default
        (owner_id NULL). A corrupted (non-JSON) row is treated as absent
        rather than failing the request.
        """
        row = self.session.scalar(self._scoped(select(Setting).where(Setting.key == key)))
        if row is None:
            return None
        try:
            return json.loads(row.value)
        except ValueError:
            return None

    def get_effective(self, key: str) -> object | None:
        """Per-user value with admin-default fallback (architecture §3).

        The bound user's own row wins; otherwise the admin-default row
        (owner_id NULL); otherwise None. The admin-default row is shared
        configuration (not another user's data), so this fallback read is
        the one cross-namespace read this repository performs.
        """
        value = self.get(key)
        if value is not None:
            return value
        row = self.session.scalar(
            select(Setting).where(Setting.owner_id.is_(None), Setting.key == key)
        )
        if row is None:
            return None
        try:
            return json.loads(row.value)
        except ValueError:
            return None

    def set(self, key: str, value: object) -> bool:
        """Upsert one row in the bound tenant's namespace (architecture §11).

        A personal namespace is bound by its owner only (enforced in the
        constructor) — no admin bypass; the admin-default namespace
        (owner_id None) is writable by an active admin only (the actor
        bound in the constructor). Returns False (no change) when the
        actor may not write the row.
        """
        if self._owner_id is None:
            actor = self._actor
            if actor is None or not actor.active or actor.role != "admin":
                return False
        row = self.session.scalar(self._scoped(select(Setting).where(Setting.key == key)))
        if row is None:
            row = Setting(owner_id=self._owner_id, key=key)
            self.session.add(row)
        row.value = json.dumps(value)
        row.updated_at = utc_now_iso()
        self.session.flush()
        return True
