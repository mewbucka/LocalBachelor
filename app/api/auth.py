"""Auth endpoints: login / logout / first-run (architecture section 12).

Session-cookie auth. ``POST /api/auth/login`` verifies the password with
argon2id and, on success, sets a compact signed session cookie;
``POST /api/auth/logout`` clears it. Both are public (no prior session).

First-run (roadmap 1.2): ``GET /api/auth/first-run`` reports whether the
instance still needs setup (users table empty); ``POST`` creates the FIRST
account (role=admin, active, ``users.lang``) — pre-setup ONLY, see the
endpoint docstring. The HTML twin (the ``/setup`` wizard page) lives in
``app/api/setup.py`` and shares the same pre-setup-only rule.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import SessionManager
from app.core.passwords import MIN_PASSWORD_LENGTH
from app.db.session import get_db
from app.repositories.users import UserRepository

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


def _session_manager(request: Request) -> SessionManager:
    return request.app.state.session_store


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    manager: SessionManager = Depends(_session_manager),
) -> dict[str, object]:
    """Verify credentials; on success set the session cookie."""
    user = UserRepository(db).authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    manager.set_cookie(response, user.id)
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "lang": user.lang,
    }


@router.post("/logout")
def logout(
    response: Response,
    manager: SessionManager = Depends(_session_manager),
) -> dict[str, str]:
    """Clear the session cookie (idempotent; public)."""
    manager.clear_cookie(response)
    return {"ok": "logged_out"}


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
        raise HTTPException(status_code=409, detail="setup already completed")
    try:
        user = repo.create(body.username, body.password, role="admin", lang=body.lang)
    except ValueError:
        # role and lang are already validated by the request model, so the
        # only ValueError ``create`` can raise here is "username already
        # taken" — a concurrent first-run POST won the race.
        raise HTTPException(status_code=409, detail="setup already completed") from None
    manager.set_cookie(response, user.id)
    return {"id": user.id, "username": user.username, "role": user.role, "lang": user.lang}
