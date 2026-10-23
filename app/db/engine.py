"""SQLAlchemy engine setup: SQLite in WAL mode.

Single DB file under data_dir (architecture sections 3 and 15). The
in-process worker thread (Phase 3) shares this engine, so connections are
created cross-thread.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

BUSY_TIMEOUT_MS = 5000


def init_engine(settings: Settings) -> Engine:
    """Create the engine, ensure the data directories exist, set WAL.

    PRAGMAs are applied to every new connection via the `connect` event:
    journal_mode=WAL persists in the file, busy_timeout and foreign_keys
    are per-connection.
    """
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{settings.resolved_db_path.as_posix()}",
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

    # Force the first connection so WAL mode is set at startup.
    with engine.connect():
        pass

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
