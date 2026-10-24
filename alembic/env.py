"""Alembic migration environment (roadmap task 1.1).

The database URL is resolved from the app's config.yaml (the single
source of truth, roadmap rule 7) when it is present, so `alembic upgrade
head` targets exactly the database the app uses. When no config.yaml is
reachable (e.g. a bare run), it falls back to the placeholder
`sqlalchemy.url` in alembic.ini.

The engine is built via app.db.engine.build_engine so migrations run
against the same WAL-configured SQLite database the app uses.
"""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool

from app.config import load_settings
from app.db.engine import build_engine
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """Resolve the SQLite URL from config.yaml, else the ini placeholder."""
    section = config.get_section("app")
    app_config = section.get("app_config") if section else None
    if app_config:
        path = Path(app_config)
        if not path.is_absolute():
            # Relative to the directory of the alembic.ini that declares it.
            base = Path(config.config_file_name).resolve().parent
            path = base / app_config
        if path.is_file():
            settings = load_settings(path)
            return f"sqlite:///{settings.resolved_db_path.as_posix()}"
    return str(config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    """Offline mode: emit SQL to stdout (no live DB connection)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: connect and run the migrations."""
    engine = build_engine(_resolve_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            poolclass=pool.NullPool,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
