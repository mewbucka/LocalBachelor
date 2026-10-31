"""Tenant-isolation tests for SettingsRepository (architecture §11, task 1.7).

The mandatory A≠B negative suite for the settings entity: a user cannot
read or write another user's settings rows. Settings are personal data:

- A personal namespace (``owner_id`` = a user) can be bound only by that
  owner (while active) — even an admin cannot bind another user's personal
  settings (mirrors ``UserRepository.set_lang``'s no-admin-bypass rule).
- The admin-default namespace (``owner_id`` NULL) is readable by all (the
  §3 fallback) but writable by an active admin only.

Task 1.7: the repository is constructed per tenant (``SettingsRepository(
session, owner_id, actor=...)``); cross-tenant access is refused LOUDLY
(``PermissionError`` in the constructor, ``False`` for a refused admin-
default write), never performed silently.
"""

import pytest
from sqlalchemy import select

from app.models import Setting
from app.repositories.settings import SettingsRepository
from app.repositories.users import UserRepository


@pytest.fixture()
def seed(db_session):
    """The seeded instance: one admin + two plain users (bootstrap, unbound)."""
    repo = UserRepository(db_session, None)
    admin = repo.create("admin1", "admin-pass", role="admin")
    alice = repo.create("alice", "alice-pass", role="user")
    bob = repo.create("bob", "bob-pass", role="user")
    db_session.commit()
    return {"session": db_session, "admin": admin, "alice": alice, "bob": bob}


# -- personal namespace: owner only (A≠B) --------------------------------------


def test_personal_namespace_requires_its_owner(seed):
    db = seed["session"]
    # A≠B: alice cannot bind bob's personal namespace.
    with pytest.raises(PermissionError):
        SettingsRepository(db, seed["bob"].id, actor=seed["alice"])


def test_admin_cannot_bind_others_personal_namespace(seed):
    # Even an admin cannot bind another user's personal settings: settings
    # are personal data (architecture §11), not administrative surface.
    db = seed["session"]
    with pytest.raises(PermissionError):
        SettingsRepository(db, seed["alice"].id, actor=seed["admin"])
    with pytest.raises(PermissionError):
        SettingsRepository(db, seed["bob"].id, actor=seed["admin"])


def test_inactive_actor_cannot_bind_personal_namespace(seed):
    db = seed["session"]
    # Deactivating alice (an admin act) makes her own namespace unbindable:
    # the actor must be active.
    assert UserRepository(db, seed["admin"].id).set_active_admin(seed["alice"].id, False)
    db.commit()
    with pytest.raises(PermissionError):
        SettingsRepository(db, seed["alice"].id, actor=seed["alice"])


def test_owner_can_read_and_write_own_namespace(seed):
    db = seed["session"]
    assert SettingsRepository(db, seed["alice"].id, actor=seed["alice"]).set("theme", "dark")
    db.commit()
    assert SettingsRepository(db, seed["alice"].id, actor=seed["alice"]).get("theme") == "dark"


def test_personal_rows_do_not_leak_across_users(seed):
    db = seed["session"]
    assert SettingsRepository(db, seed["bob"].id, actor=seed["bob"]).set("theme", "dark")
    db.commit()
    # alice's namespace must not see bob's row — neither via get (her scope
    # is her own rows) nor via get_effective (no admin-default row exists).
    alice = SettingsRepository(db, seed["alice"].id, actor=seed["alice"])
    assert alice.get("theme") is None
    assert alice.get_effective("theme") is None


# -- admin-default namespace (owner_id NULL) ------------------------------------


def test_admin_default_readable_by_anyone(seed):
    db = seed["session"]
    assert SettingsRepository(db, None, actor=seed["admin"]).set("theme", "dark")
    db.commit()
    # The admin-default namespace is readable without any actor.
    assert SettingsRepository(db, None).get("theme") == "dark"
    # Every user reaches it through the §3 fallback.
    for user in (seed["alice"], seed["bob"]):
        assert SettingsRepository(db, user.id, actor=user).get_effective("theme") == "dark"


def test_admin_default_writable_by_active_admin_only(seed):
    db = seed["session"]
    # No actor at all: the write is refused (False, not an exception) ...
    assert SettingsRepository(db, None).set("theme", "dark") is False
    # ... and so is a non-admin actor's.
    assert SettingsRepository(db, None, actor=seed["alice"]).set("theme", "dark") is False
    db.commit()
    # The refused writes created no row.
    assert SettingsRepository(db, None).get("theme") is None
    # An active admin: the write goes through.
    assert SettingsRepository(db, None, actor=seed["admin"]).set("theme", "dark") is True
    db.commit()
    assert SettingsRepository(db, None).get("theme") == "dark"


def test_inactive_admin_cannot_write_admin_default(seed):
    db = seed["session"]
    # A second admin is created so the last-active-admin guard permits
    # deactivating admin1, which is then done.
    admin2 = UserRepository(db, seed["admin"].id).create_admin(
        "admin2", "admin2-pass", role="admin"
    )
    db.commit()
    assert UserRepository(db, admin2.id).set_active_admin(seed["admin"].id, False)
    db.commit()
    # The deactivated admin can no longer write the shared default.
    assert SettingsRepository(db, None, actor=seed["admin"]).set("theme", "dark") is False
    db.commit()
    assert SettingsRepository(db, None).get("theme") is None


# -- get_effective: own row → admin default → None --------------------------------


def test_get_effective_own_row_wins_over_admin_default(seed):
    db = seed["session"]
    SettingsRepository(db, seed["alice"].id, actor=seed["alice"]).set("theme", "light")
    SettingsRepository(db, None, actor=seed["admin"]).set("theme", "dark")
    db.commit()
    alice = SettingsRepository(db, seed["alice"].id, actor=seed["alice"])
    assert alice.get_effective("theme") == "light"


def test_get_effective_falls_back_to_admin_default(seed):
    db = seed["session"]
    SettingsRepository(db, None, actor=seed["admin"]).set("theme", "dark")
    db.commit()
    bob = SettingsRepository(db, seed["bob"].id, actor=seed["bob"])
    assert bob.get("theme") is None  # no personal row ...
    assert bob.get_effective("theme") == "dark"  # ... so the default applies


def test_get_effective_none_when_no_row_anywhere(seed):
    alice = SettingsRepository(seed["session"], seed["alice"].id, actor=seed["alice"])
    assert alice.get_effective("theme") is None


# -- typed JSON values + upsert semantics ------------------------------------------


def test_values_are_typed_json(seed):
    db = seed["session"]
    assert SettingsRepository(db, seed["alice"].id, actor=seed["alice"]).set(
        "prefs", {"density": "compact"}
    )
    db.commit()
    alice = SettingsRepository(db, seed["alice"].id, actor=seed["alice"])
    assert alice.get("prefs") == {"density": "compact"}


def test_set_upserts_the_existing_row(seed):
    db = seed["session"]
    repo = SettingsRepository(db, seed["alice"].id, actor=seed["alice"])
    assert repo.set("theme", "light") is True
    assert repo.set("theme", "dark") is True
    db.commit()
    # One row per (owner, key); the newest value wins.
    rows = list(
        db.scalars(
            select(Setting).where(Setting.owner_id == seed["alice"].id, Setting.key == "theme")
        )
    )
    assert len(rows) == 1
    assert rows[0].value == '"dark"'


def test_corrupted_value_treated_as_absent(seed):
    db = seed["session"]
    # A row with unparseable JSON is treated as absent (both for get and
    # the get_effective fallback), not as a failed request.
    db.add(Setting(owner_id=seed["alice"].id, key="theme", value="not-json"))
    SettingsRepository(db, None, actor=seed["admin"]).set("theme", "dark")
    db.commit()
    alice = SettingsRepository(db, seed["alice"].id, actor=seed["alice"])
    assert alice.get("theme") is None
    # The fallback still reaches the intact admin-default row.
    assert alice.get_effective("theme") == "dark"
