"""SettingsRepository: owner-scoped settings access (architecture §3, §11).

Per-user settings with admin-default fallback (architecture §3):
``owner_id`` is the owning user's id, or NULL for the ADMIN DEFAULT row.
Isolation (architecture §11): a user may read the admin-default row only
as a fallback and may write ONLY their own row (settings are personal,
mirroring UserRepository.set_lang's no-admin-bypass rule); the
admin-default row is writable by an active admin only. Values are typed
JSON (architecture §3), parsed on read and serialized on write.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting, User
from app.models.base import utc_now_iso


class SettingsRepository:
    """Owner-scoped settings reads/writes; values are typed JSON."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, owner_id: int | None, key: str) -> object | None:
        """The parsed JSON value of one (owner, key) row, else None.

        A corrupted (non-JSON) row is treated as absent rather than
        failing the request.
        """
        row = self._session.scalar(
            select(Setting).where(Setting.owner_id == owner_id, Setting.key == key)
        )
        if row is None:
            return None
        try:
            return json.loads(row.value)
        except ValueError:
            return None

    def get_for_user(self, user_id: int, key: str) -> object | None:
        """Per-user value with admin-default fallback (architecture §3).

        The user's own row wins; otherwise the admin-default row
        (owner_id NULL); otherwise None.
        """
        value = self.get(user_id, key)
        if value is not None:
            return value
        return self.get(None, key)

    def set(self, owner_id: int | None, key: str, value: object, actor: User | None) -> bool:
        """Upsert one (owner, key) row. Owner-scoped (architecture §11).

        A non-admin actor may only write their OWN row (owner_id ==
        actor.id) — no admin bypass for per-user settings; the
        admin-default row (owner_id None) is writable by an active admin
        only. Returns False (no change) when the actor may not write the
        row.
        """
        if owner_id is None:
            if actor is None or not actor.active or actor.role != "admin":
                return False
        else:
            if actor is None or actor.id != owner_id:
                return False
        row = self._session.scalar(
            select(Setting).where(Setting.owner_id == owner_id, Setting.key == key)
        )
        if row is None:
            row = Setting(owner_id=owner_id, key=key)
            self._session.add(row)
        row.value = json.dumps(value)
        row.updated_at = utc_now_iso()
        self._session.flush()
        return True
