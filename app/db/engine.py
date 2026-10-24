"""SQLAlchemy engine setup: SQLite in WAL mode.

Single DB file under data_dir (architecture sections 3 and 15). The
in-process worker thread (Phase 3) shares this engine, so connections are
created cross-thread.

``build_engine(url)`` is the single place where the SQLite PRAGMAs are
applied; both the app (``init_engine``) and the alembic ``env.py`` use it
so migrations run against the same WAL-configured database.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

BUSY_TIMEOUT_MS = 5000


def build_engine(url: str) -> Engine:
    """Create a SQLite engine with WAL + busy_timeout + foreign_keys.

    PRAGMAs are applied to every new connection via the ``connect`` event:
    journal_mode=WAL persists in the file, busy_timeout and foreign_keys
    are per-connection. The first connection is forced so WAL mode is set
    immediately, before any schema work.
    """
    engine = create_engine(
        url,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect():
        pass

    return engine


def init_engine(settings: Settings) -> Engine:
    """Create the engine, ensure the data directories exist, set WAL."""
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    return build_engine(f"sqlite:///{settings.resolved_db_path.as_posix()}")


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
