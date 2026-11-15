"""Settings endpoint (architecture sections 3, 6, 12; roadmap 2.1).

Generalized from the task-1.4 theme-only subset to the full typed settings
surface. Every key is declared in ``app/core/settings_registry.py`` (the single
source of defaults / types / bounds / scope); this module only wires the
registry to HTTP.

- ``GET /api/settings`` — every registered key with its EFFECTIVE value (own
  -> admin-default -> code-default) and a ``source`` badge; secret keys are
  masked to ``***`` (an unset secret reads as ``null``). Session-only (401 for
  anonymous callers).
- ``PUT /api/settings`` — partial merge of the provided keys. Personal keys
  write to the caller's own namespace; admin-default-only keys write to the
  shared admin-default namespace and are admins-only (a non-admin gets 403).
  Every key is registry-validated: an unknown key, a bad type, an out-of-bounds
  value, or a chunk overlap that does not stay below the target is a localized
  422. The response echoes only the keys the caller sent, secrets masked. The
  task-1.4 theme behavior (``{"theme": "dark"}`` -> the new value, persisted to
  the caller's own row) is preserved.

CSRF (architecture section 13): PUT is a mutating JSON route and is protected
by the double-submit middleware in ``app/main.py`` (it is NOT on the exemption
list). Both routes respect the schema preflight degraded mode: the outermost
guard in ``app/main.py`` returns 503 before migrations when the DB schema is
degraded, so they never run against an unknown schema.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.settings_registry import (
    MASKED,
    SETTING_SPECS,
    SettingValidationError,
    check_cross_fields,
    resolve_effective_value,
    validate_payload,
)
from app.db.session import get_db
from app.i18n import request_locale, translate
from app.models import User
from app.repositories.settings import SettingsRepository

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Registry validation code -> the localized 422 catalog key.
_ERROR_KEYS: dict[str, str] = {
    "unknown_key": "settings.unknown_key",
    "invalid_value": "settings.invalid_value",
    "cross_field": "settings.invalid_cross_field",
}


def _effective_entries(
    user: User, db: Session, *, mask_secrets: bool
) -> dict[str, dict[str, object]]:
    """Every registered key with its effective value and source badge.

    ``own`` is the caller's personal row (consulted for personal keys only; an
    admin-default-only key never reads a personal row). The shared admin-default
    row is the fallback. ``mask_secrets`` replaces a set secret's value with
    :data:`MASKED` (an unset secret reads as ``None``). Shared by
    ``GET /api/settings`` and the ``/settings`` page so both surfaces show
    exactly the same values.
    """
    own_repo = SettingsRepository(db, user.id, actor=user)
    admin_repo = SettingsRepository(db, None, actor=user)
    entries: dict[str, dict[str, object]] = {}
    for key, spec in SETTING_SPECS.items():
        own = own_repo.get(key) if spec.scope == "personal" else None
        value, source = resolve_effective_value(spec, own, admin_repo.get(key))
        if mask_secrets and spec.secret:
            value = MASKED if value not in (None, "") else None
        entries[key] = {"value": value, "source": source}
    return entries


def _check_chunk_cross_field(
    user: User, db: Session, validated: dict[str, object]
) -> None:
    """Re-check overlap<target using effective values.

    A partial payload may touch only one of the two chunk keys, so the other is
    resolved to its current effective value (both are admin-default-only, so
    both resolve over the shared admin-default row, then the code default)
    before the relationship is checked.
    """
    admin_repo = SettingsRepository(db, None, actor=user)
    target = validated.get("chunk_target_tokens")
    if target is None:
        target, _ = resolve_effective_value(
            SETTING_SPECS["chunk_target_tokens"],
            None,
            admin_repo.get("chunk_target_tokens"),
        )
    overlap = validated.get("chunk_overlap_tokens")
    if overlap is None:
        overlap, _ = resolve_effective_value(
            SETTING_SPECS["chunk_overlap_tokens"],
            None,
            admin_repo.get("chunk_overlap_tokens"),
        )
    check_cross_fields(target, overlap)


@router.get("")
def get_settings(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
) -> dict[str, dict[str, object]]:
    """Every registered key with its effective value and source badge."""
    locale = request_locale(request)
    if user is None:
        raise HTTPException(401, detail=translate("auth.not_signed_in", locale))
    return _effective_entries(user, db, mask_secrets=True)


@router.put("")
def update_settings(
    body: dict[str, object],
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
) -> dict[str, object]:
    """Merge the provided keys into the caller's rows (registry-validated)."""
    locale = request_locale(request)
    if user is None:
        raise HTTPException(401, detail=translate("auth.not_signed_in", locale))
    if not body:
        raise HTTPException(422, detail=translate("settings.no_settings", locale))

    # Registry validation: unknown keys / bad type / out of bounds, then the
    # chunk overlap<target cross-field rule against the effective values.
    try:
        validated = validate_payload(body)
        if "chunk_target_tokens" in validated or "chunk_overlap_tokens" in validated:
            _check_chunk_cross_field(user, db, validated)
    except SettingValidationError as exc:
        raise HTTPException(422, detail=translate(_ERROR_KEYS[exc.code], locale)) from None

    # Scope enforcement: admin-default-only keys are writable by admins only.
    for key in validated:
        if SETTING_SPECS[key].scope == "admin_default_only" and user.role != "admin":
            raise HTTPException(403, detail=translate("settings.forbidden", locale))

    # Write each key into its namespace; echo only the keys the caller sent,
    # secrets masked (a set secret reads back as ***; an unset one as null).
    response: dict[str, object] = {}
    for key, value in validated.items():
        spec = SETTING_SPECS[key]
        owner_id = None if spec.scope == "admin_default_only" else user.id
        if not SettingsRepository(db, owner_id, actor=user).set(key, value):
            raise HTTPException(500, detail=translate("error.internal", locale))
        if spec.secret:
            response[key] = MASKED if value not in (None, "") else None
        else:
            response[key] = value
    return response
