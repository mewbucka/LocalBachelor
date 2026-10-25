"""First-run wizard page (roadmap 1.2, architecture section 12).

``GET /setup`` renders the minimal setup form (username, password,
language) while the instance has no users; ``POST /setup`` creates the
first (admin) account, issues the session cookie via SessionManager, and
303-redirects to ``/`` — which may still 404 until the three-pane layout
lands in task 1.4.

Once ANY user exists the wizard is closed, mirroring the JSON twin
``/api/auth/first-run`` (which answers 409): ``GET /setup`` and
``POST /setup`` both 303-redirect to ``/`` and no account is created.
There is no re-openable setup path — see the first-run endpoint
docstring in ``app/api/auth.py``.

Strings are hardcoded bilingually (en/uk) in ``app/templates/setup.html``
— the i18n engine lands in task 1.3 and retrofits this template.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import SessionManager
from app.core.passwords import check_password_policy
from app.db.session import get_db
from app.repositories.users import UserRepository

router = APIRouter(tags=["setup"])

_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

# Bilingual error strings (en, uk) hardcoded until the task 1.3 i18n engine.
_ERRORS: dict[str, tuple[str, str]] = {
    "username": (
        "Username is required (1–64 characters)",
        "Вкажіть ім'я користувача (1–64 символи)",
    ),
    "password_too_short": (
        "Password must be at least 8 characters",
        "Пароль має містити щонайменше 8 символів",
    ),
    "username_taken": ("Username is already taken", "Таке ім'я користувача вже зайняте"),
}

_VALID_LANGS = ("en", "uk")


def _session_manager(request: Request) -> SessionManager:
    return request.app.state.session_store


def _render(
    request: Request,
    *,
    username: str = "",
    lang: str = "en",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the wizard form; with ``error``, re-render it showing the
    bilingual error message (and the given status code, 400 for rejected
    submissions)."""
    context: dict[str, object] = {"username": username, "lang": lang}
    if error is not None:
        context["error"] = _ERRORS[error]
    response = _templates.TemplateResponse(
        request=request, name="setup.html", context=context
    )
    response.status_code = status_code
    return response


@router.get("/setup")
def setup_page(request: Request, db: Session = Depends(get_db)) -> Response:
    """Wizard form while the instance is unset up.

    Once any user exists the page 303-redirects to ``/``: the wizard has
    no post-setup purpose (the JSON twin answers 409; the HTML twin has
    no error page for "already set up", so it goes to the app root).
    """
    if UserRepository(db).count() > 0:
        return RedirectResponse("/", status_code=303)
    return _render(request)


@router.post("/setup")
def setup_submit(
    request: Request,
    db: Session = Depends(get_db),
    manager: SessionManager = Depends(_session_manager),
    username: str = Form(""),
    password: str = Form(""),
    lang: str = Form("en"),
) -> Response:
    """Create the first (admin) account from the wizard form.

    Validation problems re-render the form with a bilingual error (400).
    Success issues the session cookie and 303-redirects to ``/`` (POST →
    redirect, so the browser follows with a GET; ``/`` may still 404 until
    task 1.4). The cookie is set on the RedirectResponse itself — FastAPI
    discards the implicit response when a route returns a Response object.
    """
    repo = UserRepository(db)
    if repo.count() > 0:
        # Pre-setup only (same rule as the JSON endpoint's 409): send the
        # browser to the app root; no account is created.
        return RedirectResponse("/", status_code=303)
    username = username.strip()
    if not 1 <= len(username) <= 64:
        return _render(request, username=username, lang=lang, error="username", status_code=400)
    try:
        check_password_policy(password)
    except ValueError:
        return _render(
            request, username=username, lang=lang, error="password_too_short", status_code=400
        )
    if lang not in _VALID_LANGS:
        # The select offers only en/uk; a tampered value falls back to the
        # default locale (architecture §14) instead of failing the setup.
        lang = "en"
    try:
        user = repo.create(username, password, role="admin", lang=lang)
    except ValueError:
        # Only "username already taken" is reachable here (role/lang are
        # validated above): a concurrent bootstrap won the race. If the
        # instance is set up now, go to the app root; otherwise show the
        # error so the user can pick another name.
        if repo.count() > 0:
            return RedirectResponse("/", status_code=303)
        return _render(
            request, username=username, lang=lang, error="username_taken", status_code=400
        )
    redirect = RedirectResponse("/", status_code=303)
    manager.set_cookie(redirect, user.id)
    return redirect
