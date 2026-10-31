"""Theme resolution (roadmap 1.4, architecture section 3).

The theme is resolved per request and server-rendered on
``<html data-theme="...">`` by the theme middleware in ``app/main.py`` —
no flash-of-wrong-theme script is needed. Resolution mirrors the locale
chain in ``app/i18n/engine.py``:

1. the signed-in user's own settings row (``owner_id`` = the user, key
   ``theme``);
2. the admin-default settings row (``owner_id`` NULL, key ``theme``) —
   the fallback for a signed-in user who has not picked a theme yet
   (architecture §3: per-user settings with admin-default fallback);
3. anonymous: the ``lb_theme`` cookie;
4. default: ``light``.

Like the locale middleware, the settings table is consulted only when a
session is present: a pre-migration DB has no tables at all, and a valid
session token implies the DB is migrated (tokens are issued only by the
first-run wizard / login, which need the users table). The anonymous
cookie chain never touches the database.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.auth import resolve_session_user_id
from app.models import User
from app.repositories.settings import SettingsRepository

DEFAULT_THEME = "light"
SUPPORTED_THEMES = ("light", "dark")
THEME_SETTING_KEY = "theme"
THEME_COOKIE = "lb_theme"

#: How long an anonymous theme preference stays in the cookie.
THEME_COOKIE_TTL_SECONDS = 365 * 24 * 60 * 60


def resolve_theme(request: Request, db: Session | None) -> str:
    """Resolve the theme for a request (see module docstring for the order).

    ``db`` may be None: then settings rows cannot be read and the
    cookie → default chain is used.
    """
    uid = getattr(request.state, "session_user_id", None)
    if uid is None:
        uid = resolve_session_user_id(request)
    if uid is not None and db is not None:
        user = db.get(User, uid)
        if user is not None and user.active:
            # Per-user settings win: the own row first, then the
            # admin-default row (architecture §3), mirroring the
            # users.lang override in resolve_locale.
            value = SettingsRepository(db, user.id, actor=user).get_effective(THEME_SETTING_KEY)
            if isinstance(value, str) and value in SUPPORTED_THEMES:
                return value
            return DEFAULT_THEME
    cookie = request.cookies.get(THEME_COOKIE)
    if cookie in SUPPORTED_THEMES:
        return cookie
    return DEFAULT_THEME
