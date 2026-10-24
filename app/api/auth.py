"""Auth endpoints: login / logout (architecture section 12, roadmap 1.1).

Session-cookie auth. ``POST /api/auth/login`` verifies the password with
argon2id and, on success, sets a compact signed session cookie;
``POST /api/auth/logout`` clears it. Both are public (no prior session).
The first-run wizard endpoint (admin account + UI language) is task 1.2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import SessionManager
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
