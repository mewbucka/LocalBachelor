import json
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    return write_config(
        tmp_path,
        {
            "secret_key": "a" * 48,
            "settings_key": "b" * 48,
            "db_path": "local-bachelor.db",
            "data_dir": str(tmp_path),
            "host": "127.0.0.1",
            "port": 8123,
        },
    )


def write_config(tmp_path: Path, values: dict[str, object]) -> Path:
    lines = [f"{key}: {json.dumps(value)}" for key, value in values.items()]
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def base_values(tmp_path: Path) -> dict[str, object]:
    return {
        "secret_key": "a" * 48,
        "settings_key": "b" * 48,
        "db_path": "local-bachelor.db",
        "data_dir": str(tmp_path),
        "host": "127.0.0.1",
        "port": 8123,
    }


@pytest.fixture()
def db_session(config_file):
    """A session on a temp DB with all tables created (fresh per test).

    Uses ``create_all`` (not the alembic migration) so repository tests are
    fast and independent of migration state; the migration itself is
    verified separately by the task 1.1 migration run.
    """
    from app.config import load_settings
    from app.db.engine import create_session_factory, init_engine
    from app.models import Base

    settings = load_settings(config_file)
    engine = init_engine(settings)
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def stamp_alembic_head(engine: Engine) -> None:
    """Create the ``alembic_version`` table and stamp it with the head
    revision so the startup schema preflight passes.

    HTTP-level test fixtures use ``create_all`` (not the real alembic
    migration) to build the schema; without this stamp the preflight
    would see no recorded revision and flag the DB as degraded.  The
    real alembic migration is NOT run here because ``env.py`` resolves
    the DB URL from the repo-root ``config.yaml`` (which would target
    the real database, not the test's temp one).
    """
    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    config = Config(str(ini_path))
    script_dir = ScriptDirectory.from_config(config)
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected a single alembic head, got {heads}"
    head = heads[0]

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": head},
        )
