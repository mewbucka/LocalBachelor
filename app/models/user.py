"""User model (architecture section 3, users table).

Task 1.1 scope. The other section 3 tables (projects, sources, chunks,
index_builds, jobs, conversations, messages, notes, settings) are added
by their own roadmap tasks (projects/sources/chunks/index_builds/jobs in
3.1), so they are intentionally NOT defined here yet.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now_iso


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id
    role: Mapped[str] = mapped_column(String(16), server_default="user")  # admin / user
    lang: Mapped[str] = mapped_column(String(8), server_default="en")  # en / uk
    active: Mapped[bool] = mapped_column(Boolean, server_default="1")
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso)

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} username={self.username!r} "
            f"role={self.role!r} lang={self.lang!r} active={self.active}>"
        )
