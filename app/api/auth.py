"""Auth endpoints: login / logout / first-run (architecture section 12).

Session-cookie auth. ``POST /api/auth/login`` verifies the password with
argon2id and, on success, sets a compact signed session cookie;
``POST /api/auth/logout`` clears it. Both are public (no prior session).

Task 1.4: login and logout are DUAL-PATH. A JSON ``Content-Type`` — or a
request with NO content-type (the existing no-body logout test) — is the
API path: token-less, returning JSON (the shape a client or the test suite
uses). A form-ENCODED ``Content-Type`` is the browser path: it requires the
stateless signed CSRF token (task 1.4; the full CSRF middleware is 1.6) and
answers with HTML — re-rendering the page on failure, or 303-redirecting on
success.

First-run (roadmap 1.2): ``GET /api/auth/first-run`` reports whether the
instance still needs setup (users table empty); ``POST`` creates the FIRST
account (role=admin, active, ``users.lang``) — pre-setup ONLY, see the
endpoint docstring. The HTML twin (the ``/setup`` wizard page) lives in
``app/api/setup.py`` and shares the same pre-setup-only rule.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.auth import SessionManager
from app.core.csrf import CsrfManager
from app.core.passwords import MIN_PASSWORD_LENGTH
from app.db.session import get_db
from app.i18n import request_locale, translate
from app.repositories.users import UserRepository

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


def _session_manager(request: Request) -> SessionManager:
    return request.app.state.session_store


def _csrf_store(request: Request) -> CsrfManager:
    return request.app.state.csrf_store


def _is_form(request: Request) -> bool:
    """True when the request is a form-encoded browser submission.

    The dual-path dispatch (task 1.4): a form-encoded body carries the CSRF
    hidden field and gets the HTML treatment; anything else — JSON, or a
    request with no content-type at all — is the token-less API path.
    """
    ctype = request.headers.get("content-type", "").lower()
    return ctype.startswith("application/x-www-form-urlencoded") or ctype.startswith(
        "multipart/form-data"
    )


def _form_str(form: FormData, key: str) -> str:
    """Return a form field as a string; a file upload or a missing field → "".

    ``FormData.get`` is typed ``UploadFile | str | None``; the login/logout
    form paths only ever read text fields, so anything non-string is treated
    as absent.
    """
    value = form.get(key)
    return value if isinstance(value, str) else ""


def _render_login(
    request: Request,
    *,
    username: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the login form in the request's locale (mirrors setup._render).

    With ``error`` the form re-renders showing the localized
    ``auth.invalid_credentials`` message and the attempted username.
    """
    context: dict[str, object] = {"username": username}
    if error is not None:
        context["error"] = error
    templates: Jinja2Templates = request.app.state.templates
    response = templates.TemplateResponse(request=request, name="login.html", context=context)
    response.status_code = status_code
    return response


@router.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
    manager: SessionManager = Depends(_session_manager),
    csrf: CsrfManager = Depends(_csrf_store),
) -> Response:
    """Verify credentials; on success set the session cookie.

    JSON path: validate the ``LoginRequest`` body (422 on a malformed
    body), then 401 JSON for bad credentials or the user object + session
    cookie on success. Form path: verify the CSRF token first (403 on a
    missing/invalid token), then re-render the login page on bad credentials
    (401, localized, fresh token) or 303-redirect to ``/`` on success.
    """
    if _is_form(request):
        form = await request.form()
        if not csrf.validate(_form_str(form, "csrf_token")):
            raise HTTPException(
                status_code=403,
                detail=translate("auth.csrf_invalid", request_locale(request)),
            )
        username = _form_str(form, "username")
        password = _form_str(form, "password")
        user = UserRepository(db).authenticate(username, password)
        if user is None:
            return _render_login(
                request, username=username, error="invalid_credentials", status_code=401
            )
        redirect = RedirectResponse("/", status_code=303)
        manager.set_cookie(redirect, user.id)
        return redirect

    try:
        body = LoginRequest.model_validate_json(await request.body())
    except PydanticValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    user = UserRepository(db).authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=translate("auth.invalid_credentials", request_locale(request)),
        )
    response = JSONResponse(
        status_code=200,
        content={"id": user.id, "username": user.username, "role": user.role, "lang": user.lang},
    )
    manager.set_cookie(response, user.id)
    return response


@router.post("/logout")
async def logout(
    request: Request,
    manager: SessionManager = Depends(_session_manager),
    csrf: CsrfManager = Depends(_csrf_store),
) -> Response:
    """Clear the session cookie (idempotent; public).

    JSON path (a client call, or a no-body POST): clear the cookie and
    return JSON. Form path (the header logout button): verify the CSRF token
    first (403 on a missing/invalid token, and the cookie is NOT cleared on
    failure), then clear the cookie and 303-redirect to ``/login``.
    """
    if _is_form(request):
        form = await request.form()
        if not csrf.validate(_form_str(form, "csrf_token")):
            raise HTTPException(
                status_code=403,
                detail=translate("auth.csrf_invalid", request_locale(request)),
            )
        redirect = RedirectResponse("/login", status_code=303)
        manager.clear_cookie(redirect)
        return redirect

    response = JSONResponse(status_code=200, content={"ok": "logged_out"})
    manager.clear_cookie(response)
    return response


class FirstRunRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    lang: Literal["en", "uk"] = "en"  # default locale en (architecture §14)


@router.get("/first-run")
def first_run_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    """Report whether the instance still needs first-run setup.

    ``setup_needed`` is true while the users table is empty. Public: no
    prior session exists before setup.
    """
    return {"setup_needed": UserRepository(db).count() == 0}


@router.post("/first-run")
def first_run(
    body: FirstRunRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    manager: SessionManager = Depends(_session_manager),
) -> dict[str, object]:
    """Create the FIRST account (role=admin, active, users.lang=lang).

    Pre-setup ONLY (documented choice): once ANY user exists the bootstrap
    is closed and this endpoint answers 409. There is no re-openable setup
    path — accounts are deactivated, never deleted (UserRepository
    .set_active is the users-table "delete"), so under normal operation the
    table cannot become empty again, and a re-openable bootstrap would be
    an unauthenticated privilege-escalation hole. On success a session
    cookie is issued (same SessionManager as login) so the caller is signed
    in immediately; 200 (not 201) mirrors the sibling login endpoint.
    """
    repo = UserRepository(db)
    if repo.count() > 0:
        raise HTTPException(
            status_code=409, detail=translate("auth.setup_completed", request_locale(request))
        )
    try:
        user = repo.create(body.username, body.password, role="admin", lang=body.lang)
    except ValueError:
        # role and lang are already validated by the request model, so the
        # only ValueError ``create`` can raise here is "username already
        # taken" — a concurrent first-run POST won the race.
        raise HTTPException(
            status_code=409, detail=translate("auth.setup_completed", request_locale(request))
        ) from None
    manager.set_cookie(response, user.id)
    return {"id": user.id, "username": user.username, "role": user.role, "lang": user.lang}
