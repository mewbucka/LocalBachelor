"""Stylesheet + theme-variable constraint tests (roadmap 1.4).

The app ships a SINGLE stylesheet (no build step). These tests pin the
invariants the lock-in depends on:

- ``/static/style.css`` is actually served (200, ``text/css``);
- the light (``:root``) and dark (``[data-theme="dark"]``) variants define
  the SAME set of ``--lb-*`` variables, so a theme is just an attribute
  switch, not a second stylesheet;
- every color lives INSIDE one of those two blocks — the rest of the sheet
  references ``var(--lb-*)`` only, with no hardcoded hex / rgb / hsl;
- templates carry no inline ``<style>`` blocks;
- every user-visible string on a rendered page is a catalog value (roadmap
  rule 8), so no hardcoded UI text leaks into the templates.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.i18n import load_catalog
from app.main import create_app
from app.models import Base
from app.repositories.users import UserRepository

STYLE_CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "style.css"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture()
def app(config_file) -> FastAPI:
    """Seeded (set-up) instance: admin, alice (en), petr (uk)."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    session = application.state.session_factory()
    repo = UserRepository(session)
    repo.create("admin", "admin-pass", role="admin")
    repo.create("alice", "alice-pass", role="user")
    repo.create("petr", "petr-pass", role="user", lang="uk")
    session.commit()
    session.close()
    return application


@pytest.fixture()
def fresh_app(config_file) -> FastAPI:
    """Fresh instance (no users) for the /setup wizard."""
    application = create_app(config_file)
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def fresh_client(fresh_app):
    with TestClient(fresh_app) as test_client:
        yield test_client


def _css() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


def _var_names(block: str) -> set[str]:
    return set(re.findall(r"--lb-[a-z0-9-]+", block))


# -- served + theme variants -----------------------------------------------------


def test_static_style_css_is_served(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert response.text == STYLE_CSS.read_text(encoding="utf-8")


def test_theme_variants_define_the_same_variables():
    css = _css()
    root = re.search(r":root\s*\{([^}]*)\}", css)
    dark = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', css)
    assert root is not None, "the :root block (light theme) is missing"
    assert dark is not None, 'the [data-theme="dark"] block is missing'
    light_vars = _var_names(root.group(1))
    dark_vars = _var_names(dark.group(1))
    assert light_vars, "the :root block defines no --lb-* variables"
    assert light_vars == dark_vars, (
        "theme variants must define the same variable set; "
        f"light-only={sorted(light_vars - dark_vars)}, dark-only={sorted(dark_vars - light_vars)}"
    )


def test_no_hardcoded_colors_outside_theme_blocks():
    css = _css()
    stripped = re.sub(r":root\s*\{[^}]*\}", "", css)
    stripped = re.sub(r'\[data-theme="dark"\]\s*\{[^}]*\}', "", stripped)
    hex_colors = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert not hex_colors, f"hardcoded hex colors outside the theme blocks: {hex_colors}"
    assert "rgb(" not in stripped, "hardcoded rgb() color outside the theme blocks"
    assert "hsl(" not in stripped, "hardcoded hsl() color outside the theme blocks"


def test_templates_carry_no_inline_style():
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        raw = path.read_text(encoding="utf-8")
        # Jinja strips {# ... #} comments at render time, so a "<style>" mention
        # in a comment is not a real style block; strip comments before scanning.
        text = re.sub(r"\{#.*?#\}", "", raw, flags=re.DOTALL)
        assert "<style" not in text.lower(), f"{path.name} carries an inline <style> block"


# -- visible text always comes from the catalog (roadmap rule 8) ------------------


class _VisibleTextExtractor(HTMLParser):
    """Collects visible text nodes, skipping <script>/<style> subtrees."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def _locale_of(html: str) -> str:
    match = re.search(r'<html[^>]*\blang="([a-z]{2})"', html)
    return match.group(1) if match else "en"


def _assert_visible_text_in_catalog(client, path, headers=None) -> None:
    html = client.get(path, headers=headers or {}).text
    locale = _locale_of(html)
    # The EN/UK language-switcher buttons are structural labels, not
    # translatable content; everything else must be a catalog value.
    allowed = set(load_catalog(locale).values()) | {"EN", "UK"}
    extractor = _VisibleTextExtractor()
    extractor.feed(html)
    for chunk in extractor.chunks:
        assert chunk in allowed, (
            f"un-cataloged visible text in {path} (locale {locale}): {chunk!r}"
        )


def test_setup_page_visible_text_in_catalog(fresh_client):
    _assert_visible_text_in_catalog(fresh_client, "/setup")


def test_setup_page_uk_visible_text_in_catalog(fresh_client):
    _assert_visible_text_in_catalog(
        fresh_client, "/setup", headers={"Accept-Language": "uk"}
    )


def test_login_page_visible_text_in_catalog(client):
    _assert_visible_text_in_catalog(client, "/login")


def test_login_page_uk_visible_text_in_catalog(client):
    _assert_visible_text_in_catalog(client, "/login", headers={"Accept-Language": "uk"})


def test_home_page_visible_text_in_catalog(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-pass"})
    _assert_visible_text_in_catalog(client, "/")


def test_home_page_uk_visible_text_in_catalog(client):
    client.post("/api/auth/login", json={"username": "petr", "password": "petr-pass"})
    _assert_visible_text_in_catalog(client, "/")
