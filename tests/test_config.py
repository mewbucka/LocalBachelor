import logging

import pytest

from app.config import ConfigError, load_settings

from .conftest import write_config


def test_load_valid_settings(config_file):
    settings = load_settings(config_file)
    assert settings.secret_key == "a" * 48
    assert settings.settings_key == "b" * 48
    assert settings.db_path == "local-bachelor.db"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8123


def test_relative_db_path_resolves_against_data_dir(tmp_path, monkeypatch, base_values):
    monkeypatch.chdir(tmp_path)
    base_values["data_dir"] = "data_dir"  # does not exist yet: must still validate
    base_values["db_path"] = "local-bachelor.db"
    settings = load_settings(write_config(tmp_path, base_values))
    assert settings.resolved_data_dir == tmp_path / "data_dir"
    assert settings.resolved_db_path == tmp_path / "data_dir" / "local-bachelor.db"


def test_absolute_db_path_resolves_verbatim(tmp_path, base_values):
    base_values["db_path"] = str(tmp_path / "elsewhere.db")
    settings = load_settings(write_config(tmp_path, base_values))
    assert settings.resolved_db_path == tmp_path / "elsewhere.db"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml")


def test_empty_file_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)


def test_non_mapping_file_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML mapping"):
        load_settings(path)


def test_unknown_key_rejected(tmp_path, base_values):
    base_values["chat_provider"] = "openai"  # belongs in DB settings, not config.yaml
    with pytest.raises(ConfigError, match="unknown key"):
        load_settings(write_config(tmp_path, base_values))


def test_empty_secret_key_rejected(tmp_path, base_values):
    base_values["secret_key"] = ""
    with pytest.raises(ConfigError):
        load_settings(write_config(tmp_path, base_values))


def test_short_key_loads_but_warns(tmp_path, base_values, caplog):
    base_values["secret_key"] = "short"
    with caplog.at_level(logging.WARNING, logger="app.config"):
        settings = load_settings(write_config(tmp_path, base_values))
    assert settings.secret_key == "short"
    assert any("secret_key" in r.message and "recommended" in r.message for r in caplog.records)


def test_bad_port_rejected(tmp_path, base_values):
    base_values["port"] = 0
    with pytest.raises(ConfigError):
        load_settings(write_config(tmp_path, base_values))
    base_values["port"] = 70000
    with pytest.raises(ConfigError):
        load_settings(write_config(tmp_path, base_values))


def test_double_quoted_windows_path_error_is_actionable(tmp_path):
    # Literal backslashes inside a double-quoted YAML scalar are invalid
    # escapes; load_settings must surface an actionable ConfigError.
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                'secret_key: "' + "a" * 48 + '"',
                'settings_key: "' + "b" * 48 + '"',
                'db_path: "local-bachelor.db"',
                'data_dir: "C:\\Users\\me\\lb"',
                'host: "127.0.0.1"',
                "port: 8123",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="invalid YAML") as excinfo:
        load_settings(path)
    assert "forward slashes" in str(excinfo.value)


def test_data_dir_parent_is_file_rejected(tmp_path, base_values):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    base_values["data_dir"] = str(blocker / "sub")
    with pytest.raises(ConfigError, match="data_dir"):
        load_settings(write_config(tmp_path, base_values))


def test_db_path_exists_but_is_directory_rejected(tmp_path, base_values):
    blocker = tmp_path / "local-bachelor.db"
    blocker.mkdir()
    with pytest.raises(ConfigError, match="db_path"):
        load_settings(write_config(tmp_path, base_values))


def test_existing_db_file_revalidated_on_restart(tmp_path, base_values):
    # Second start: the DB file already exists; validation must accept it
    # (the anchor for a file is its parent directory, not the file itself).
    (tmp_path / "local-bachelor.db").touch()
    settings = load_settings(write_config(tmp_path, base_values))
    assert settings.resolved_db_path == tmp_path / "local-bachelor.db"


def test_existing_data_dir_revalidated_on_restart(tmp_path, base_values):
    (tmp_path / "data").mkdir()
    base_values["data_dir"] = str(tmp_path / "data")
    settings = load_settings(write_config(tmp_path, base_values))
    assert settings.resolved_db_path == tmp_path / "data" / "local-bachelor.db"
