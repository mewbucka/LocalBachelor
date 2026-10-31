"""Tenant-isolation tests for UserRepository (architecture §11, task 1.7).

Mandatory negative tests: a user cannot read / update / delete any entity
of another user. For the users table the entities are accounts, and the
rules are: a plain user touches only their own row (the repository is
BOUND to the acting user's id); admins get the administrative scope (the
``*_admin`` methods: list / read / create / reset password / deactivate
any account) but do NOT read other users' tenant data.

Task 1.7: the old per-call ``owner_id=`` parameters are gone — the
repository is constructed per tenant (``UserRepository(session,
owner_id)``) and cross-tenant access is refused LOUDLY (``TenantScopeError``
on an unbound tenant method, ``PermissionError`` on a non-admin actor),
not with a silent None/False.
"""

import pytest

from app.repositories.base import TenantScopeError
from app.repositories.users import AdminGuardViolation, UserRepository


@pytest.fixture()
def seed(db_session):
    """The seeded instance: one admin + two plain users (bootstrap, unbound)."""
    repo = UserRepository(db_session, None)
    admin = repo.create("admin1", "admin-pass", role="admin")
    alice = repo.create("alice", "alice-pass", role="user")
    bob = repo.create("bob", "bob-pass", role="user")
    db_session.commit()
    return {"session": db_session, "admin": admin, "alice": alice, "bob": bob}


# -- authentication (the identity plane: unbound) ---------------------------


def test_authenticate_success(seed):
    repo = UserRepository(seed["session"], None)
    user = repo.authenticate("alice", "alice-pass")
    assert user is not None and user.id == seed["alice"].id


def test_authenticate_wrong_password(seed):
    repo = UserRepository(seed["session"], None)
    assert repo.authenticate("alice", "wrong") is None


def test_authenticate_unknown_user(seed):
    repo = UserRepository(seed["session"], None)
    assert repo.authenticate("nobody", "whatever") is None


def test_authenticate_inactive_user_rejected(seed):
    db = seed["session"]
    assert UserRepository(db, seed["admin"].id).set_active_admin(seed["alice"].id, False)
    db.commit()
    assert UserRepository(db, None).authenticate("alice", "alice-pass") is None


# -- negative isolation: user A cannot touch user B --------------------------


def test_user_cannot_read_others_row(seed):
    db = seed["session"]
    # alice (plain) can only ever see her own row: the tenant scope is her
    # bound row, and there is no tenant method that addresses bob's (or an
    # admin's) account.
    row = UserRepository(db, seed["alice"].id).get()
    assert row is not None and row.id == seed["alice"].id and row.username == "alice"


def test_unbound_instance_refuses_tenant_methods(seed):
    # An unbound instance (bootstrap / identity plane) cannot use the
    # tenant-scoped methods: TenantScopeError, not a silent no-op.
    unbound = UserRepository(seed["session"], None)
    with pytest.raises(TenantScopeError):
        unbound.get()
    with pytest.raises(TenantScopeError):
        unbound.set_lang("uk")


def test_user_cannot_reset_others_password(seed):
    db = seed["session"]
    # alice's tenant scope has no method that takes a target id: her
    # self-service update changes her OWN password only ...
    assert UserRepository(db, seed["alice"].id).update_password("hacked")
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.authenticate("bob", "bob-pass") is not None  # bob unchanged
    assert unbound.authenticate("bob", "hacked") is None


def test_user_can_change_own_password(seed):
    db = seed["session"]
    assert UserRepository(db, seed["alice"].id).update_password("new-alice-pass")
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.authenticate("alice", "alice-pass") is None
    assert unbound.authenticate("alice", "new-alice-pass") is not None


def test_user_cannot_deactivate_others(seed):
    db = seed["session"]
    # Deactivation is administrative: a plain user's instance gets
    # PermissionError from the admin guard, never a silent no-op.
    with pytest.raises(PermissionError):
        UserRepository(db, seed["alice"].id).set_active_admin(seed["bob"].id, False)
    db.commit()
    assert UserRepository(db, None).authenticate("bob", "bob-pass") is not None  # still active


def test_user_cannot_self_deactivate(seed):
    # Deactivation is administrative only (§11).
    with pytest.raises(PermissionError):
        UserRepository(seed["session"], seed["alice"].id).set_active_admin(
            seed["alice"].id, False
        )
    assert UserRepository(seed["session"], None).authenticate("alice", "alice-pass") is not None


def test_user_cannot_list_all_accounts(seed):
    with pytest.raises(PermissionError):
        UserRepository(seed["session"], seed["alice"].id).list_all_admin()


# -- admin scope (administrative, §11) ----------------------------------------


def test_admin_can_read_any_account(seed):
    row = UserRepository(seed["session"], seed["admin"].id).get_admin(seed["bob"].id)
    assert row is not None and row.username == "bob"


def test_admin_can_list_all_accounts(seed):
    rows = UserRepository(seed["session"], seed["admin"].id).list_all_admin()
    assert {r.username for r in rows} == {"admin1", "alice", "bob"}


def test_admin_can_reset_others_password(seed):
    db = seed["session"]
    repo = UserRepository(db, seed["admin"].id)
    assert repo.update_password_admin(seed["alice"].id, "new-alice-pass")
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.authenticate("alice", "alice-pass") is None
    assert unbound.authenticate("alice", "new-alice-pass") is not None


def test_admin_can_deactivate_user(seed):
    db = seed["session"]
    assert UserRepository(db, seed["admin"].id).set_active_admin(seed["bob"].id, False)
    db.commit()
    assert UserRepository(db, None).authenticate("bob", "bob-pass") is None


# -- last-active-admin guard (task 1.5) ----------------------------------------


def test_admin_cannot_deactivate_last_active_admin(seed):
    db = seed["session"]
    # admin1 is the only active admin; deactivating it would orphan the
    # instance, so the guard raises (distinct from a no-op False).
    with pytest.raises(AdminGuardViolation) as excinfo:
        UserRepository(db, seed["admin"].id).set_active_admin(seed["admin"].id, False)
    assert excinfo.value.code == "last_admin"
    db.commit()
    # The flag is unchanged: the admin can still authenticate.
    assert UserRepository(db, None).authenticate("admin1", "admin-pass") is not None


def test_new_sole_admin_cannot_self_deactivate(seed):
    db = seed["session"]
    other = UserRepository(db, seed["admin"].id).create_admin("admin2", "admin2-pass", role="admin")
    db.commit()
    # Two active admins: deactivating one is fine (a successor remains).
    assert UserRepository(db, other.id).set_active_admin(seed["admin"].id, False)
    db.commit()
    # admin1 is now inactive; admin2 is the sole active admin. Deactivating
    # admin2 (by anyone) would leave zero active admins -> blocked.
    with pytest.raises(AdminGuardViolation):
        UserRepository(db, other.id).set_active_admin(other.id, False)
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.authenticate("admin2", "admin2-pass") is not None  # still active


def test_admin_can_deactivate_self_when_other_admin_remains(seed):
    db = seed["session"]
    UserRepository(db, seed["admin"].id).create_admin("admin2", "admin2-pass", role="admin")
    db.commit()
    # Handover: the outgoing admin deactivates themself (a successor exists).
    assert UserRepository(db, seed["admin"].id).set_active_admin(seed["admin"].id, False)
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.authenticate("admin1", "admin-pass") is None  # stepped down
    assert unbound.authenticate("admin2", "admin2-pass") is not None


def test_reactivation_is_never_blocked_by_the_guard(seed):
    db = seed["session"]
    other = UserRepository(db, seed["admin"].id).create_admin("admin2", "admin2-pass", role="admin")
    db.commit()
    # Hand over (admin2 remains), then admin2 re-activates admin1. Setting
    # active=True skips the guard entirely, so this is always allowed.
    admin_repo = UserRepository(db, other.id)
    assert admin_repo.set_active_admin(seed["admin"].id, False)
    assert admin_repo.set_active_admin(seed["admin"].id, True)
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.authenticate("admin1", "admin-pass") is not None
    assert unbound.authenticate("admin2", "admin2-pass") is not None


def test_deactivating_a_non_admin_is_unaffected_by_the_guard(seed):
    db = seed["session"]
    # The guard only protects the last active ADMIN; ordinary accounts can
    # always be deactivated (the users-table "delete" path).
    assert UserRepository(db, seed["admin"].id).set_active_admin(seed["alice"].id, False)
    db.commit()
    assert UserRepository(db, None).authenticate("alice", "alice-pass") is None


# -- create / validation -------------------------------------------------------


def test_bootstrap_create_without_owner(db_session):
    # First-run bootstrap (task 1.2): no acting user yet.
    first = UserRepository(db_session, None).create("founder", "founder-pass", role="admin")
    db_session.commit()
    assert first.role == "admin"


def test_bound_create_refused(seed):
    # Account creation on a BOUND tenant is administrative work: the
    # bootstrap entry point is refused loudly, not a silent no-op.
    with pytest.raises(TenantScopeError):
        UserRepository(seed["session"], seed["alice"].id).create("eve", "eve-pass")


def test_non_admin_cannot_create_user(seed):
    with pytest.raises(PermissionError):
        UserRepository(seed["session"], seed["alice"].id).create_admin("carol", "carol-pass")


def test_admin_can_create_user(seed):
    db = seed["session"]
    carol = UserRepository(db, seed["admin"].id).create_admin("carol", "carol-pass")
    db.commit()
    assert carol.id is not None
    assert UserRepository(db, None).authenticate("carol", "carol-pass") is not None


def test_duplicate_username_rejected(seed):
    with pytest.raises(ValueError):
        UserRepository(seed["session"], seed["admin"].id).create_admin("alice", "other-pass")


# -- set_lang (architecture §14: self-service, no admin bypass) ---------------


def test_user_can_change_own_lang(seed):
    db = seed["session"]
    assert UserRepository(db, seed["alice"].id).set_lang("uk")
    db.commit()
    assert UserRepository(db, None).get_by_username("alice").lang == "uk"


def test_user_cannot_change_others_lang(seed):
    db = seed["session"]
    # alice's tenant scope can only address her own row: her self-service
    # change leaves bob's preference untouched (bob's row is not reachable
    # from alice's bound instance at all).
    assert UserRepository(db, seed["alice"].id).set_lang("uk")
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.get_by_username("alice").lang == "uk"
    assert unbound.get_by_username("bob").lang == "en"


def test_admin_cannot_change_others_lang(seed):
    # No admin bypass: the tenant scope of an admin-bound instance is the
    # admin's OWN row — it cannot address alice's preference.
    db = seed["session"]
    assert UserRepository(db, seed["admin"].id).set_lang("uk")
    db.commit()
    unbound = UserRepository(db, None)
    assert unbound.get_by_username("admin1").lang == "uk"
    assert unbound.get_by_username("alice").lang == "en"


def test_set_lang_invalid_lang_rejected(seed):
    with pytest.raises(ValueError):
        UserRepository(seed["session"], seed["alice"].id).set_lang("fr")


def test_set_lang_missing_target_returns_false(seed):
    # Binding to a non-existent tenant: the tenant scope matches nothing,
    # so the write is a no-op (the row simply does not exist).
    assert UserRepository(seed["session"], 999999).set_lang("uk") is False


def test_invalid_role_rejected(seed):
    with pytest.raises(ValueError):
        UserRepository(seed["session"], seed["admin"].id).create_admin("x", "y", role="superuser")


def test_invalid_lang_rejected(seed):
    with pytest.raises(ValueError):
        UserRepository(seed["session"], seed["admin"].id).create_admin("x", "y", lang="fr")
