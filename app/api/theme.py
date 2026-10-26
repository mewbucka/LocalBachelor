"""Theme switcher endpoint (roadmap 1.4) — the ANONYMOUS toggle.

``POST /theme`` is the anonymous header switcher: the form in
``templates/base.html`` posts the chosen theme (light/dark), this
endpoint stores it in the ``lb_theme`` cookie and 303-redirects back to
the page the switcher was clicked on (same-origin Referer, else the app
root) — exactly the ``/lang`` pattern from task 1.3.

If a session IS present the preference is additionally written to the
user's own settings row so the two sources of truth stay consistent
(mirrors ``/lang`` syncing users.lang; the per-user row overrides the
cookie in resolution, so the switch takes effect immediately either way).
Signed-in users toggle via ``PUT /api/settings`` from the header instead;
the two paths agree on the outcome. Resolution itself lives in
``app/core/theme.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import resolve_session_user_id
from app.core.theme import (
    DEFAULT_THEME,
    SUPPORTED_THEMES,
    THEME_COOKIE,
    THEME_COOKIE_TTL_SECONDS,
    THEME_SETTING_KEY,
)
from app.db.session import get_db
from app.i18n import redirect_back_target
from app.models import User
from app.repositories.settings import SettingsRepository

router = APIRouter(tags=["theme"])


@router.post("/theme")
def switch_theme(
    request: Request,
    db: Session = Depends(get_db),
    theme: str = Form("light"),
) -> RedirectResponse:
    """Store the theme preference in the cookie and redirect back."""
    if theme not in SUPPORTED_THEMES:
        # The form offers only light/dark; a tampered value falls back to
        # the default theme instead of failing the request.
        theme = DEFAULT_THEME
    # The cookie is set on the RedirectResponse itself: FastAPI discards
    # the injected response object when a route returns a Response object.
    response = RedirectResponse(redirect_back_target(request), status_code=303)
    response.set_cookie(
        THEME_COOKIE,
        theme,
        max_age=THEME_COOKIE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    uid = getattr(request.state, "session_user_id", None)
    if uid is None:
        uid = resolve_session_user_id(request)
    if uid is not None:
        user = db.get(User, uid)
        if user is not None and user.active:
            SettingsRepository(db).set(uid, THEME_SETTING_KEY, theme, actor=user)
    return response
