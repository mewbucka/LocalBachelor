"""Request-scoped DB session (FastAPI dependency).

Yields one session per request: commits on success, rolls back on error,
and always closes. ``expire_on_commit=False`` (set by
``create_session_factory``) keeps ORM attributes readable after commit,
which matters because the session is closed before the response body is
serialized.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
