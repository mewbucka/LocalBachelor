"""The /settings page (roadmap 2.1).

The read-only settings skeleton: every registered key, grouped by section
(Chat / Embeddings / Search / Ingestion / UI), showing its effective value with
a source badge (own / admin-default / code-default). The inputs are disabled —
editing lands in task 2.6. Anonymous visitors are sent to the login page, or to
the setup wizard on a fresh (no-user) instance, mirroring ``/`` in
``app/api/pages.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.settings import _effective_entries
from app.core.auth import get_current_user
from app.core.settings_registry import (
    MASKED,
    SECTION_ORDER,
    SETTING_SPECS,
    SettingSpec,
    keys_in_section,
)
from app.db.session import get_db
from app.i18n import request_locale, translate
from app.models import User
from app.repositories.users import UserRepository

router = APIRouter(tags=["settings-page"])


def _display_value(spec: SettingSpec, value: object, locale: str) -> str:
    """The human-readable value shown in the row (secret-safe)."""
    if spec.secret:
        # ``value`` is already masked (MASKED) or None from ``_effective_entries``.
        return MASKED if value == MASKED else translate("settings.not_set", locale)
    if value is None or value == "":
        return translate("settings.not_set", locale)
    if spec.type == "enum":
        return translate(f"settings.enum.{spec.name}.{value}", locale)
    return str(value)


def _build_sections(
    entries: dict[str, dict[str, object]], locale: str
) -> list[dict[str, object]]:
    """Group the effective entries into page sections with localized labels."""
    sections: list[dict[str, object]] = []
    for section in SECTION_ORDER:
        # Key is "rows", not "items": Jinja2 resolves ``section.items`` to the
        # dict's built-in ``.items`` method (attribute lookup wins), which is not
        # iterable and would crash the template. A non-colliding name avoids it.
        rows: list[dict[str, object]] = []
        for key in keys_in_section(section):
            spec = SETTING_SPECS[key]
            entry = entries[key]
            source = str(entry["source"])
            value = entry["value"]
            rows.append(
                {
                    "key": key,
                    "label": translate(f"settings.{key}", locale),
                    "display": _display_value(spec, value, locale),
                    "source": source,
                    "source_label": translate(f"settings.source.{source}", locale),
                }
            )
        sections.append(
            {
                "key": section,
                "title": translate(f"settings.section.{section}", locale),
                "rows": rows,
            }
        )
    return sections


@router.get("/settings")
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
) -> Response:
    if user is None:
        # Fresh instance (no users) goes to the setup wizard; otherwise login.
        if UserRepository(db, None).count() == 0:
            return RedirectResponse("/setup", status_code=303)
        return RedirectResponse("/login", status_code=303)
    locale = request_locale(request)
    sections = _build_sections(_effective_entries(user, db, mask_secrets=True), locale)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"sections": sections},
    )
