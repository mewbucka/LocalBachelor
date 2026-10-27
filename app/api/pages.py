"""App pages (roadmap 1.4): the three-pane shell routes.

``GET /`` — signed in: the bare shell with localized empty states (the
projects UI lands in Phase 3); anonymous: 303 to ``/login``, or to
``/setup`` while the instance has no users — the wizard is the only
entry point before setup (roadmap 1.2), and a login form could never
succeed there.

``GET /login`` — the localized login form inside the shell. The form
posts to ``POST /api/auth/login`` (``app/api/auth.py``) with a CSRF
token: a missing/invalid token is rejected with a 403 BEFORE any
credential check, bad credentials re-render the page with the localized
error, and success 303-redirects to ``/`` with the session cookie set on
the returned redirect. A signed-in visitor is redirected to ``/``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import resolve_session_user_id
from app.db.session import get_db
from app.models import User
from app.repositories.users import UserRepository

router = APIRouter(tags=["pages"])


def _active_user_id(request: Request, db: Session) -> int | None:
    """The session user id when the account is still active, else None.

    Mirrors get_current_user's fail-closed rule: a valid token for a
    deactivated account is treated as anonymous.
    """
    uid = getattr(request.state, "session_user_id", None)
    if uid is None:
        uid = resolve_session_user_id(request)
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not user.active:
        return None
    return user.id


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)) -> Response:
    """The three-pane shell; anonymous visitors go to /login (or /setup)."""
    uid = _active_user_id(request, db)
    if uid is None:
        if UserRepository(db).count() == 0:
            # Pre-setup instances have no accounts at all: the wizard is
            # the only entry point (roadmap 1.2).
            return RedirectResponse("/setup", status_code=303)
        return RedirectResponse("/login", status_code=303)
    user = db.get(User, uid)
    # The sidebar "Admin" link is shown only to active admins (task 1.5).
    is_admin = user is not None and user.role == "admin"
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request, name="home.html", context={"is_admin": is_admin}
    )


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)) -> Response:
    """The login form (inside the shell); a signed-in visitor goes to /."""
    if _active_user_id(request, db) is not None:
        return RedirectResponse("/", status_code=303)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request=request, name="login.html", context={})
