"""Language switcher endpoint (roadmap 1.3, architecture §14).

``POST /lang`` is the ANONYMOUS switcher: the header form in
``templates/base.html`` posts the chosen code (``en``/``uk``), this
endpoint stores it in the ``lb_lang`` cookie and 303-redirects back to
the page the switcher was clicked on (same-origin Referer, else the app
root).

If a session IS present the preference is additionally written to
``users.lang`` so the two sources of truth stay consistent (and because
``users.lang`` overrides the cookie on every later request, the switch
takes effect immediately either way). The authenticated UI uses
``PATCH /api/users/lang`` instead (owner-scoped, session user only); the
two paths agree on the outcome.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import resolve_session_user_id
from app.db.session import get_db
from app.i18n import (
    DEFAULT_LOCALE,
    LANG_COOKIE,
    LANG_COOKIE_TTL_SECONDS,
    SUPPORTED_LOCALES,
    redirect_back_target,
)
from app.models import User
from app.repositories.users import UserRepository

router = APIRouter(tags=["lang"])


@router.post("/lang")
def switch_lang(
    request: Request,
    db: Session = Depends(get_db),
    lang: str = Form("en"),
) -> RedirectResponse:
    """Store the language preference and redirect back to the caller page."""
    if lang not in SUPPORTED_LOCALES:
        # The form offers only en/uk; a tampered value falls back to the
        # default locale (architecture §14) instead of failing the request.
        lang = DEFAULT_LOCALE
    # The cookie is set on the RedirectResponse itself: FastAPI discards
    # the injected response object when a route returns a Response object.
    response = RedirectResponse(redirect_back_target(request), status_code=303)
    response.set_cookie(
        LANG_COOKIE,
        lang,
        max_age=LANG_COOKIE_TTL_SECONDS,
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
            UserRepository(db).set_lang(user.id, lang, owner_id=user.id)
    return response
