"""i18n engine (architecture §14, roadmap 1.3).

Catalogs: flat ``key → string`` JSON maps, one per supported locale
(``catalogs/en.json``, ``catalogs/uk.json``). Every user-visible string in
the app goes through them (roadmap rule 8); the RAG system prompt template
is English and stays OUT of the catalogs (architecture §14).

Locale resolution per request (in order):

1. logged-in user's ``users.lang`` (overrides everything, including the
   ``lb_lang`` cookie — on login the per-user preference wins);
2. anonymous: the ``lb_lang`` cookie;
3. anonymous: ``Accept-Language`` — ``uk`` wins only on an EXPLICIT ``uk*``
   tag (no fuzzy mapping: ``ua`` or ``en`` never produce ``uk``);
4. default: ``en``.

Surfaces:

- ``translate(key, locale)`` — Python helper; a missing key RAISES
  ``KeyError`` (no silent key echo).
- Jinja global ``_()`` — bound to the request locale (set on
  ``request.state`` by the middleware in ``app/main.py``).
- Jinja global ``i18n_catalog_json()`` — the current catalog embedded once
  per page as ``<script type="application/json" id="i18n-catalog">`` for
  the tiny dependency-free vanilla-JS ``window._(key)`` helper in
  ``templates/base.html``.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from jinja2 import pass_context
from markupsafe import Markup
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.core.auth import resolve_session_user_id
from app.models import User

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "uk")
LANG_COOKIE = "lb_lang"

#: How long an anonymous language preference stays in the cookie.
LANG_COOKIE_TTL_SECONDS = 365 * 24 * 60 * 60

_CATALOGS_DIR = Path(__file__).resolve().parent / "catalogs"
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@cache
def load_catalog(locale: str) -> dict[str, str]:
    """Load (and cache) the flat key→string catalog for ``locale``.

    Raises KeyError for an unsupported locale and ValueError when the file
    is not a flat key→string map, so a broken catalog fails loudly at
    startup rather than at render time.
    """
    if locale not in SUPPORTED_LOCALES:
        supported = ", ".join(SUPPORTED_LOCALES)
        raise KeyError(f"unsupported locale: {locale!r} (supported: {supported})")
    path = _CATALOGS_DIR / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load i18n catalog {path}: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ValueError(f"i18n catalog {path} must be a non-empty JSON object")
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise ValueError(f"i18n catalog {path} must map non-empty keys to non-empty strings")
    return data


def translate(key: str, locale: str) -> str:
    """Translate ``key`` in ``locale``.

    A missing key RAISES ``KeyError`` — there is deliberately no fallback
    to the default locale and no silent echo of the key, so a catalog drift
    surfaces immediately (the parity test in tests/test_i18n.py keeps en/uk
    key sets identical).
    """
    catalog = load_catalog(locale)
    if key not in catalog:
        raise KeyError(f"missing i18n key {key!r} in locale {locale!r}")
    return catalog[key]


def _accept_language_wants_uk(header: str) -> bool:
    """True when Accept-Language contains an explicit ``uk*`` tag.

    ``uk`` wins ONLY on an explicit tag (``uk`` or ``uk-…``); ``ua``,
    ``en``, and any other tag never map to ``uk``. Tags with ``q=0``
    (explicit rejection) are ignored.
    """
    for part in header.split(","):
        pieces = [p.strip() for p in part.split(";")]
        tag = pieces[0].lower() if pieces else ""
        if not tag or not (tag == "uk" or tag.startswith("uk-")):
            continue
        quality = 1.0
        for param in pieces[1:]:
            if param.lower().startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def _anonymous_locale(request: Request) -> str:
    """Anonymous chain: lb_lang cookie → Accept-Language (uk*) → default en."""
    cookie = request.cookies.get(LANG_COOKIE)
    if cookie in SUPPORTED_LOCALES:
        return cookie
    if _accept_language_wants_uk(request.headers.get("accept-language", "")):
        return "uk"
    return DEFAULT_LOCALE


def resolve_locale(request: Request, db: Session | None) -> str:
    """Resolve the locale for a request (see module docstring for the order).

    ``db`` may be None: then a logged-in user's preference cannot be read
    and the anonymous chain is used (callers that can open a session
    should pass one).
    """
    uid = getattr(request.state, "session_user_id", None)
    if uid is None:
        uid = resolve_session_user_id(request)
    if uid is not None and db is not None:
        user = db.get(User, uid)
        if user is not None and user.active and user.lang in SUPPORTED_LOCALES:
            # users.lang overrides the cookie (architecture §14: on login
            # the per-user preference wins).
            return user.lang
    return _anonymous_locale(request)


def request_locale(request: Request) -> str:
    """Locale the i18n middleware resolved for this request.

    Falls back to the default locale when the middleware did not run
    (defensive; the app factory registers it for every request).
    """
    locale = getattr(request.state, "locale", None)
    if locale in SUPPORTED_LOCALES:
        return locale
    return DEFAULT_LOCALE


@pass_context
def jinja_translate(context, key: str) -> Markup:
    """Jinja global ``_()``: translate ``key`` in the request's locale.

    Returns a Markup string so autoescaping does not mangle the catalog
    values (they are developer-authored, never user input; user input in
    templates stays autoescaped as usual). A missing key raises KeyError.
    """
    request = context.get("request")
    locale = request_locale(request) if request is not None else DEFAULT_LOCALE
    return Markup(translate(key, locale))


@pass_context
def jinja_locale(context) -> str:
    """Jinja global: the request's resolved locale (for <html lang=…> etc.)."""
    request = context.get("request")
    return request_locale(request) if request is not None else DEFAULT_LOCALE


@pass_context
def jinja_catalog_json(context) -> str:
    """The current catalog as HTML-safe JSON for the per-page embedded
    ``<script type="application/json" id="i18n-catalog">`` block.

    ``<`` is escaped to ``\\u003c`` so the JSON can never terminate the
    script tag; ``window._`` in base.html reads this catalog.
    """
    request = context.get("request")
    locale = request_locale(request) if request is not None else DEFAULT_LOCALE
    payload = json.dumps(load_catalog(locale), ensure_ascii=False, sort_keys=True)
    return payload.replace("<", "\\u003c")


def create_templates() -> Jinja2Templates:
    """The shared template env with the i18n globals registered.

    Wired onto ``app.state.templates`` by the app factory; all HTML
    routes render through it so ``_()`` / ``i18n_catalog_json()`` /
    ``jinja_locale()`` are available in every template.
    """
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["_"] = jinja_translate
    templates.env.globals["i18n_catalog_json"] = jinja_catalog_json
    templates.env.globals["jinja_locale"] = jinja_locale
    return templates


def redirect_back_target(request: Request) -> str:
    """Where the language switcher sends the browser after switching.

    The Referer header's path+query when it is same-origin, else the app
    root. (Absolute cross-origin referers are never followed.)
    """
    referer = request.headers.get("referer", "")
    if referer:
        parts = urlsplit(referer)
        if parts.netloc and parts.netloc == request.url.netloc and parts.path:
            return parts.path + (f"?{parts.query}" if parts.query else "")
    return "/"
