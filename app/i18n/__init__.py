"""i18n (architecture §14, roadmap 1.3): en/uk catalogs + engine.

Public surface: ``translate``, ``resolve_locale``, ``load_catalog``,
``create_templates``, ``SUPPORTED_LOCALES``, ``DEFAULT_LOCALE``,
``LANG_COOKIE``, ``LANG_COOKIE_TTL_SECONDS``, ``redirect_back_target``.
"""

from app.i18n.engine import (
    DEFAULT_LOCALE,
    LANG_COOKIE,
    LANG_COOKIE_TTL_SECONDS,
    SUPPORTED_LOCALES,
    create_templates,
    jinja_catalog_json,
    jinja_locale,
    jinja_translate,
    load_catalog,
    redirect_back_target,
    request_locale,
    resolve_locale,
    translate,
)

__all__ = [
    "DEFAULT_LOCALE",
    "LANG_COOKIE",
    "LANG_COOKIE_TTL_SECONDS",
    "SUPPORTED_LOCALES",
    "create_templates",
    "jinja_catalog_json",
    "jinja_locale",
    "jinja_translate",
    "load_catalog",
    "redirect_back_target",
    "request_locale",
    "resolve_locale",
    "translate",
]
