"""Settings endpoint (architecture section 12) — the minimal task-1.4 subset.

``PUT /api/settings`` merges the provided keys into the CALLER's own
settings rows (per-user, architecture §3) and returns the updated values.
In Phase 1 the only accepted key is ``theme`` (light/dark, roadmap 1.4):
the signed-in header theme switcher calls it and reloads. Unknown keys
are rejected with 422 (the request model forbids extras — no silent
ignore), as is an empty body. Session-only: 401 with the localized
"not signed in" detail for anonymous callers.

The full settings surface (GET, typed defaults, masked provider keys,
Test buttons, the settings UI) is tasks 2.1/2.2/2.6.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.theme import THEME_SETTING_KEY
from app.db.session import get_db
from app.i18n import request_locale, translate
from app.models import User
from app.repositories.settings import SettingsRepository

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsRequest(BaseModel):
    """The task-1.4 subset: the theme key only.

    ``extra="forbid"`` rejects unknown keys with 422 instead of silently
    dropping them (roadmap rule 8's loud-failure posture).
    """

    model_config = ConfigDict(extra="forbid")

    theme: Literal["light", "dark"] | None = None


@router.put("")
def update_settings(
    body: SettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
) -> dict[str, object]:
    """Merge the provided settings into the caller's own rows (owner-scoped)."""
    locale = request_locale(request)
    if user is None:
        raise HTTPException(status_code=401, detail=translate("auth.not_signed_in", locale))
    if body.theme is None:
        raise HTTPException(status_code=422, detail=translate("settings.no_settings", locale))
    if not SettingsRepository(db, user.id, actor=user).set(THEME_SETTING_KEY, body.theme):
        # The caller's own row is always writable by an active caller; a
        # False here means the account vanished or was deactivated mid-
        # request.
        raise HTTPException(status_code=500, detail=translate("error.internal", locale))
    return {"theme": body.theme}
