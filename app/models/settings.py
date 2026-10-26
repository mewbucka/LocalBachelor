"""Setting model (architecture section 3, settings table).

Key/value store for settings with per-user + admin-default rows:
``owner_id`` is the owning user's id, or NULL for the ADMIN DEFAULT row
(architecture §3). Values are typed JSON stored as text. Task 1.4 lands
the table plus the single ``theme`` key (light/dark) the theme switcher
writes; the full typed settings store (defaults, masked provider keys,
GET, the settings UI) is tasks 2.1/2.2/2.6.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now_iso


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)  # typed JSON (architecture §3)
    updated_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso)

    __table_args__ = (UniqueConstraint("owner_id", "key"),)

    def __repr__(self) -> str:
        return f"<Setting owner_id={self.owner_id} key={self.key!r}>"
