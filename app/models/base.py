"""Declarative base + shared model helpers (architecture section 3).

All timestamps are ISO-8601 UTC strings ("2026-10-24T02:00:00Z") per
section 3. The vec0 / FTS5 virtual tables (sqlite-vec + FTS5) are NOT
ORM models: they live in the same DB file, keyed by chunks.id, and are
created in Phase 3 alongside the vector store.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a Z suffix (section 3)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Base(DeclarativeBase):
    pass
