"""Bootstrap configuration.

config.yaml is the ONLY configuration file on disk and holds ONLY the
bootstrap values (roadmap rule 7): secret_key, settings_key, db_path,
data_dir, host, port. Every other setting lives in the DB settings store
and is edited via the UI.

Path semantics:
- data_dir: absolute, or relative to the process working directory.
- db_path: absolute, or relative to data_dir.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)

#: Recommended length for bootstrap keys (advisory, not a hard minimum).
MIN_RECOMMENDED_KEY_LENGTH = 32

_BOOTSTRAP_KEYS = ("secret_key", "settings_key", "db_path", "data_dir", "host", "port")


class ConfigError(RuntimeError):
    """config.yaml is missing, unreadable, or invalid."""


class Settings(BaseSettings):
    """Bootstrap settings. config.yaml is the only source (no env/dotenv)."""

    secret_key: str = Field(min_length=1)
    settings_key: str = Field(min_length=1)
    db_path: str = Field(min_length=1)
    data_dir: str = Field(min_length=1)
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)

    model_config = SettingsConfigDict(extra="forbid")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Bootstrap keys come ONLY from config.yaml (roadmap rule 7);
        # env / dotenv / secret-file sources are deliberately disabled.
        return (init_settings,)

    @property
    def resolved_data_dir(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else Path.cwd() / path

    @property
    def resolved_db_path(self) -> Path:
        path = Path(self.db_path)
        if path.is_absolute():
            return path
        return self.resolved_data_dir / path

    @model_validator(mode="after")
    def _check_key_lengths(self) -> Settings:
        for name in ("secret_key", "settings_key"):
            if len(getattr(self, name)) < MIN_RECOMMENDED_KEY_LENGTH:
                logger.warning(
                    "%s is shorter than the recommended %d characters",
                    name,
                    MIN_RECOMMENDED_KEY_LENGTH,
                )
        return self

    @model_validator(mode="after")
    def _check_paths(self) -> Settings:
        _check_location(self.resolved_data_dir, "data_dir", must_be_dir=True)
        _check_location(self.resolved_db_path, "db_path", must_be_dir=False)
        return self


def _nearest_existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            raise ConfigError(f"no usable location found for: {path}")
        current = current.parent
    return current


def _check_location(path: Path, label: str, *, must_be_dir: bool) -> None:
    """A location is valid when it already has the right type (if it
    exists) and its nearest existing ancestor is a writable directory, so
    any missing intermediate directories can be created at startup."""
    if path.exists():
        if must_be_dir and not path.is_dir():
            raise ConfigError(f"{label} exists but is not a directory: {path}")
        if not must_be_dir and not path.is_file():
            raise ConfigError(f"{label} exists but is not a file: {path}")
        # Nothing is missing below an existing path, so the anchor is the
        # path itself (a directory) or its parent (for a file, which
        # necessarily exists as a directory): restarts re-validate cleanly.
        anchor = path if must_be_dir else path.parent
    else:
        anchor = _nearest_existing_ancestor(path)
    if not anchor.is_dir():
        raise ConfigError(f"{label} cannot be created: {anchor} is not a directory")
    if not os.access(anchor, os.W_OK):
        raise ConfigError(f"{label} is not writable: {anchor}")


def load_settings(config_path: str | Path) -> Settings:
    """Load and validate the bootstrap config; raise ConfigError clearly."""
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path} — copy config.example.yaml to "
            "config.yaml and fill in real values"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        hint = (
            " — hint: inside double quotes, backslashes are escape characters; "
            "use forward slashes or single quotes for Windows paths"
            if "\\" in raw
            else ""
        )
        raise ConfigError(f"invalid YAML in {path}: {exc}{hint}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    unknown = sorted(set(data) - set(_BOOTSTRAP_KEYS))
    if unknown:
        raise ConfigError(
            f"unknown key(s) in {path}: {', '.join(unknown)} — config.yaml holds "
            f"ONLY bootstrap keys: {', '.join(_BOOTSTRAP_KEYS)} (roadmap rule 7)"
        )
    try:
        return Settings(**data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}:\n{exc}") from exc
