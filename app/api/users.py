"""User self-service endpoints (architecture sections 12, 14).

``PATCH /api/users/lang`` changes the caller's OWN ``users.lang`` — the
per-user i18n preference. Owner-scoped (architecture §11): the session
user can touch only their own row; there is no admin bypass, because a
language preference is personal, not administrative. The endpoint takes
no target id at all, so it can never address another account.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.i18n import request_locale, translate
from app.models import User
from app.repositories.users import UserRepository

router = APIRouter(prefix="/api/users", tags=["users"])


class LangRequest(BaseModel):
    lang: Literal["en", "uk"]  # en/uk (architecture §14); anything else → 422


@router.patch("/lang")
def update_lang(
    body: LangRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
) -> dict[str, object]:
    """Set the caller's ``users.lang`` (owner-scoped; session user only).

    On success the next page render already uses the new locale: the
    i18n middleware resolves ``users.lang`` first (it overrides the
    ``lb_lang`` cookie — architecture §14).
    """
    locale = request_locale(request)
    if user is None:
        raise HTTPException(status_code=401, detail=translate("auth.not_signed_in", locale))
    if not UserRepository(db).set_lang(user.id, body.lang, owner_id=user.id):
        # Unreachable in practice (lang validated by the model, the row is
        # the caller's own): fail loudly instead of a silent no-op.
        raise HTTPException(status_code=500, detail=translate("error.internal", locale))
    return {"id": user.id, "lang": body.lang}
