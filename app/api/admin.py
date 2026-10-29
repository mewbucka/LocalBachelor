"""Admin user management (roadmap 1.5, architecture sections 11, 12).

Administrative scope ONLY (section 11): create users, deactivate /
reactivate them, and reset their passwords. Admins do NOT bypass tenant
isolation to read other users' DATA — that stays owner-scoped (Phase 3+).

Endpoints (all admin-only; anonymous -> 401, signed-in non-admin -> 403,
both localized):

- ``GET  /admin`` — the user-management page: the account table, the create
  form, and per-row deactivate/reactivate + reset-password controls.
- ``GET    /api/admin/users`` — every account (JSON).
- ``POST   /api/admin/users`` — create an account (dual-path, below).
- ``POST/PATCH /api/admin/users/{id}`` — deactivate/reactivate and/or reset
  the password (dual-path, below).

Dual-path (mirrors ``app/api/auth.py``): a form-encoded body is the browser
path — its ``csrf_token`` is checked by the CSRF middleware (app/main.py,
not here) and the endpoint answers by 303-redirecting to ``/admin`` (or
re-rendering it with the localized error). A JSON body is the API path:
token-less (the middleware enforces the double-submit token only on the two
HTMX settings/language endpoints, not on admin CRUD), returning JSON (201
create / 200 update, 404 missing target, 409 last-admin guard, 422 malformed
body). The HTML ``POST`` reaches the ``PATCH``-contracted endpoint via
``api_route(methods=["POST", "PATCH"])`` (forms cannot natively PATCH); JSON
clients send PATCH.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.auth import get_current_user
from app.core.passwords import MIN_PASSWORD_LENGTH, check_password_policy
from app.db.session import get_db
from app.i18n import request_locale, translate
from app.models import User
from app.repositories.users import AdminGuardViolation, UserRepository

router = APIRouter(tags=["admin"])


def _is_form(request: Request) -> bool:
    """True for a form-encoded (browser) submission; else the JSON API path."""
    ctype = request.headers.get("content-type", "").lower()
    return ctype.startswith("application/x-www-form-urlencoded") or ctype.startswith(
        "multipart/form-data"
    )


def _form_str(form: FormData, key: str) -> str:
    """A form field as a string; a file upload or a missing field -> ""."""
    value = form.get(key)
    return value if isinstance(value, str) else ""


def get_admin_user(
    request: Request,
    user: User | None = Depends(get_current_user),
) -> User:
    """Dependency: the current user, requiring an active admin.

    Anonymous (no valid session) -> 401; a signed-in non-admin -> 403. Both
    are localized (roadmap rule 8: no hardcoded user-visible strings).
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=translate("auth.not_signed_in", request_locale(request)),
        )
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail=translate("auth.forbidden", request_locale(request)),
        )
    return user


def _user_dict(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "lang": user.lang,
        "active": user.active,
        "created_at": user.created_at,
    }


def _render_admin(
    request: Request,
    db: Session,
    admin: User,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the admin page (in the request's locale) with the account list.

    ``error`` is a catalog-key suffix (``username``, ``password_too_short``,
    ``username_taken``, ``last_admin``) shown as a localized banner; the page
    re-renders with it (and the given status code) on a rejected form
    submission.
    """
    users = UserRepository(db).list_all(owner_id=admin.id)
    context: dict[str, object] = {"users": users, "is_admin": True}
    if error is not None:
        context["error"] = error
    templates: Jinja2Templates = request.app.state.templates
    response = templates.TemplateResponse(request=request, name="admin.html", context=context)
    response.status_code = status_code
    return response


# -- page --------------------------------------------------------------------


@router.get("/admin")
def admin_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
) -> HTMLResponse:
    """The user-management page (admin only)."""
    return _render_admin(request, db, admin)


# -- API ---------------------------------------------------------------------


@router.get("/api/admin/users")
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
) -> list[dict[str, object]]:
    """List every account (admin only, section 11)."""
    return [_user_dict(u) for u in UserRepository(db).list_all(owner_id=admin.id)]


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role: Literal["admin", "user"] = "user"
    lang: Literal["en", "uk"] = "en"


@router.post("/api/admin/users")
async def create_user(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
) -> Response:
    """Create an account (admin only).

    JSON path: validate the ``CreateUserRequest`` body (422 malformed), then
    201 with the new account, or 409 on a duplicate username. Form path:
    verify the CSRF token first (403), re-render ``/admin`` with the
    localized error on a rejected submission, or 303-redirect on success.
    A tampered ``role``/``lang`` falls back to the safe default (never
    grants admin from a forged value).
    """
    repo = UserRepository(db)
    if _is_form(request):
        form = await request.form()
        username = _form_str(form, "username").strip()
        password = _form_str(form, "password")
        role = _form_str(form, "role") or "user"
        lang = _form_str(form, "lang") or "en"
        if not 1 <= len(username) <= 64:
            return _render_admin(request, db, admin, error="username", status_code=400)
        try:
            check_password_policy(password)
        except ValueError:
            return _render_admin(request, db, admin, error="password_too_short", status_code=400)
        if role not in ("admin", "user"):
            role = "user"
        if lang not in ("en", "uk"):
            lang = "en"
        try:
            repo.create(username, password, role=role, lang=lang, owner_id=admin.id)
        except ValueError:
            # Only "username already taken" is reachable here (role/lang are
            # validated above and the actor is a known active admin).
            return _render_admin(request, db, admin, error="username_taken", status_code=409)
        return RedirectResponse("/admin", status_code=303)

    try:
        body = CreateUserRequest.model_validate_json(await request.body())
    except PydanticValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    try:
        user = repo.create(
            body.username, body.password, role=body.role, lang=body.lang, owner_id=admin.id
        )
    except ValueError:
        # role/lang are validated by the model; only "username already taken"
        # is reachable here.
        raise HTTPException(
            status_code=409, detail=translate("setup.error.username_taken", request_locale(request))
        ) from None
    return JSONResponse(status_code=201, content=_user_dict(user))


class UpdateUserRequest(BaseModel):
    active: bool | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH)

    @model_validator(mode="after")
    def _require_a_field(self) -> UpdateUserRequest:
        if self.active is None and self.password is None:
            raise ValueError("at least one of 'active' or 'password' is required")
        return self


@router.api_route("/api/admin/users/{user_id}", methods=["POST", "PATCH"])
async def update_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
) -> Response:
    """Deactivate/reactivate and/or reset an account's password (admin only).

    JSON path (PATCH): ``{active?}`` and/or ``{password?}`` (min length
    enforced; a body with neither is 422). ``set_active`` raising the
    last-active-admin guard -> 409; a missing target -> 404; success -> 200
    with the updated account. Form path (POST, the page's controls): verify
    the CSRF token first (403); the ``active`` field toggles the flag and the
    ``password`` field resets it; a guard/short-password rejection re-renders
    ``/admin`` with the localized error, otherwise 303-redirect.
    """
    repo = UserRepository(db)
    if _is_form(request):
        form = await request.form()
        active_raw = _form_str(form, "active")
        password = _form_str(form, "password")
        if active_raw != "":
            try:
                repo.set_active(user_id, active_raw == "1", owner_id=admin.id)
            except AdminGuardViolation:
                return _render_admin(request, db, admin, error="last_admin", status_code=409)
            return RedirectResponse("/admin", status_code=303)
        if password != "":
            try:
                check_password_policy(password)
            except ValueError:
                return _render_admin(
                    request, db, admin, error="password_too_short", status_code=400
                )
            repo.update_password(user_id, password, owner_id=admin.id)
            return RedirectResponse("/admin", status_code=303)
        return RedirectResponse("/admin", status_code=303)

    try:
        body = UpdateUserRequest.model_validate_json(await request.body())
    except PydanticValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    if body.active is not None:
        try:
            changed = repo.set_active(user_id, body.active, owner_id=admin.id)
        except AdminGuardViolation:
            raise HTTPException(
                status_code=409,
                detail=translate("admin.error.last_admin", request_locale(request)),
            ) from None
        if not changed:
            raise HTTPException(404, detail=translate("error.not_found", request_locale(request)))
    if body.password is not None:
        changed = repo.update_password(user_id, body.password, owner_id=admin.id)
        if not changed:
            raise HTTPException(404, detail=translate("error.not_found", request_locale(request)))
    user = repo.get_by_id(user_id, owner_id=admin.id)
    return JSONResponse(status_code=200, content=_user_dict(user) if user else {"id": user_id})
