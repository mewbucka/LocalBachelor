import json
from pathlib import Path

import pytest


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
