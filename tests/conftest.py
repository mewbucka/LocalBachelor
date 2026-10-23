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
