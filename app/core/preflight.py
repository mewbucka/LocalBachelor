"""Startup schema preflight (fixup 0.3).

At application startup the lifespan hook checks the database schema
against the latest alembic revision.  If the schema is missing (no
``alembic_version`` table) or outdated (recorded revision ≠ head), the
app enters **degraded mode**: ``/healthz`` returns 503, API routes
return 503 JSON, and browser routes return 503 HTML with a localized
message naming ``alembic upgrade head``.

The preflight is **read-only**: it never creates tables or runs
migrations.  The operator must run ``alembic upgrade head`` manually.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: The operator command to bring the schema current.
MIGRATE_CMD = "alembic upgrade head"


class SchemaStatus(StrEnum):
    """Outcome of the startup schema preflight."""

    OK = "ok"
    MISSING = "missing"
    OUTDATED = "outdated"


@dataclass(frozen=True)
class PreflightResult:
    status: SchemaStatus
    head: str | None
    current: str | None


def alembic_ini_path() -> Path:
    """Repo-root ``alembic.ini`` (this file lives at ``app/core/preflight.py``,
    so ``parents[2]`` is the repository root)."""
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def migration_head(ini: Path | None = None) -> str | None:
    """The head alembic revision from the script directory.

    Returns ``None`` when the ini file is missing or the script directory
    has no single head (branching / empty).  This function only reads the
    script files — it does **not** connect to any database.
    """
    ini_path = ini or alembic_ini_path()
    if not ini_path.is_file():
        return None
    try:
        config = Config(str(ini_path))
        script_dir = ScriptDirectory.from_config(config)
        heads = script_dir.get_heads()
    except Exception:
        return None
    if len(heads) != 1:
        return None
    return heads[0]


def recorded_revision(engine: Engine) -> str | None:
    """The revision recorded in ``alembic_version``, or ``None`` if the
    table does not exist or is empty."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
            if row is None:
                return None
            return str(row[0])
    except Exception:
        return None


def run_preflight(engine: Engine, ini: Path | None = None) -> PreflightResult:
    """Check the DB schema against the alembic head.

    Returns a :class:`PreflightResult` with:

    - ``OK`` — head is known and current == head.
    - ``MISSING`` — no alembic_version table (or no recorded revision).
    - ``OUTDATED`` — head is known, a revision is recorded, and they
      differ.

    When the head cannot be determined (no ini, no single head) the
    preflight is a no-op and returns ``OK`` (best-effort; the app still
    boots, just without schema verification).
    """
    head = migration_head(ini)
    current = recorded_revision(engine)

    if head is None:
        logger.warning(
            "schema preflight: cannot determine alembic head "
            "(ini missing or multiple heads); skipping check"
        )
        return PreflightResult(status=SchemaStatus.OK, head=None, current=current)

    if current is None:
        return PreflightResult(status=SchemaStatus.MISSING, head=head, current=None)

    if current != head:
        return PreflightResult(status=SchemaStatus.OUTDATED, head=head, current=current)

    return PreflightResult(status=SchemaStatus.OK, head=head, current=current)
