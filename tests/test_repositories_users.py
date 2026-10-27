"""Tenant-isolation tests for UserRepository (architecture section 11).

Mandatory negative tests: a user cannot read / update / delete any entity
of another user. For the users table the entities are accounts, and the
rules are: a plain user touches only their own row; admins get the
administrative scope (list / read / reset password / deactivate any
account) but do NOT read other users' tenant data.
"""

import pytest

from app.repositories.users import AdminGuardViolation, UserRepository


@pytest.fixture()
def repo(db_session):
    return UserRepository(db_session)


@pytest.fixture()
def users(repo, db_session):
    admin = repo.create("admin1", "admin-pass", role="admin")
    alice = repo.create("alice", "alice-pass", role="user")
    bob = repo.create("bob", "bob-pass", role="user")
    db_session.commit()
    return {"admin": admin, "alice": alice, "bob": bob}


# -- authentication --------------------------------------------------------


def test_authenticate_success(repo, users):
    user = repo.authenticate("alice", "alice-pass")
    assert user is not None and user.id == users["alice"].id


def test_authenticate_wrong_password(repo, users):
    assert repo.authenticate("alice", "wrong") is None


def test_authenticate_unknown_user(repo, users):
    assert repo.authenticate("nobody", "whatever") is None


def test_authenticate_inactive_user_rejected(repo, users, db_session):
    assert repo.set_active(users["alice"].id, False, owner_id=users["admin"].id)
    db_session.commit()
    assert repo.authenticate("alice", "alice-pass") is None


# -- negative isolation: user A cannot touch user B ------------------------


def test_user_cannot_read_others_row(repo, users):
    # alice (plain) cannot read bob's account ...
    assert repo.get_by_id(users["bob"].id, owner_id=users["alice"].id) is None
    # ...nor an admin's account.
    assert repo.get_by_id(users["admin"].id, owner_id=users["alice"].id) is None


def test_user_can_read_own_row(repo, users):
    row = repo.get_by_id(users["alice"].id, owner_id=users["alice"].id)
    assert row is not None and row.username == "alice"


def test_user_cannot_reset_others_password(repo, users, db_session):
    assert repo.update_password(users["bob"].id, "hacked", owner_id=users["alice"].id) is False
    db_session.commit()
    assert repo.authenticate("bob", "bob-pass") is not None  # unchanged
    assert repo.authenticate("bob", "hacked") is None


def test_user_can_change_own_password(repo, users, db_session):
    assert repo.update_password(users["alice"].id, "new-alice-pass", owner_id=users["alice"].id)
    db_session.commit()
    assert repo.authenticate("alice", "alice-pass") is None
    assert repo.authenticate("alice", "new-alice-pass") is not None


def test_user_cannot_deactivate_others(repo, users, db_session):
    assert repo.set_active(users["bob"].id, False, owner_id=users["alice"].id) is False
    db_session.commit()
    assert repo.authenticate("bob", "bob-pass") is not None  # still active


def test_user_cannot_self_deactivate(repo, users, db_session):
    # Deactivation is administrative only (section 11).
    assert repo.set_active(users["alice"].id, False, owner_id=users["alice"].id) is False
    db_session.commit()
    assert repo.authenticate("alice", "alice-pass") is not None


def test_user_cannot_list_all_accounts(repo, users):
    assert repo.list_all(owner_id=users["alice"].id) == []


# -- admin scope (administrative, section 11) ------------------------------


def test_admin_can_read_any_account(repo, users):
    row = repo.get_by_id(users["bob"].id, owner_id=users["admin"].id)
    assert row is not None and row.username == "bob"


def test_admin_can_list_all_accounts(repo, users):
    rows = repo.list_all(owner_id=users["admin"].id)
    assert {r.username for r in rows} == {"admin1", "alice", "bob"}


def test_admin_can_reset_others_password(repo, users, db_session):
    assert repo.update_password(users["alice"].id, "new-alice-pass", owner_id=users["admin"].id)
    db_session.commit()
    assert repo.authenticate("alice", "alice-pass") is None
    assert repo.authenticate("alice", "new-alice-pass") is not None


def test_admin_can_deactivate_user(repo, users, db_session):
    assert repo.set_active(users["bob"].id, False, owner_id=users["admin"].id)
    db_session.commit()
    assert repo.authenticate("bob", "bob-pass") is None


# -- last-active-admin guard (task 1.5) --------------------------------------


def test_admin_cannot_deactivate_last_active_admin(repo, users, db_session):
    # admin1 is the only active admin; deactivating it would orphan the
    # instance, so the guard raises (distinct from a no-op False).
    with pytest.raises(AdminGuardViolation) as excinfo:
        repo.set_active(users["admin"].id, False, owner_id=users["admin"].id)
    assert excinfo.value.code == "last_admin"
    db_session.commit()
    # The flag is unchanged: the admin can still authenticate.
    assert repo.authenticate("admin1", "admin-pass") is not None


def test_new_sole_admin_cannot_self_deactivate(repo, db_session, users):
    other = repo.create("admin2", "admin2-pass", role="admin")
    db_session.commit()
    # Two active admins: deactivating one is fine (a successor remains).
    assert repo.set_active(users["admin"].id, False, owner_id=other.id)
    db_session.commit()
    # admin1 is now inactive; admin2 is the sole active admin. Deactivating
    # admin2 (by anyone) would leave zero active admins -> blocked.
    with pytest.raises(AdminGuardViolation):
        repo.set_active(other.id, False, owner_id=other.id)
    db_session.commit()
    assert repo.authenticate("admin2", "admin2-pass") is not None  # still active


def test_admin_can_deactivate_self_when_other_admin_remains(repo, db_session, users):
    repo.create("admin2", "admin2-pass", role="admin")
    db_session.commit()
    # Handover: the outgoing admin deactivates themself (a successor exists).
    assert repo.set_active(users["admin"].id, False, owner_id=users["admin"].id)
    db_session.commit()
    assert repo.authenticate("admin1", "admin-pass") is None  # stepped down
    assert repo.authenticate("admin2", "admin2-pass") is not None


def test_reactivation_is_never_blocked_by_the_guard(repo, db_session, users):
    other = repo.create("admin2", "admin2-pass", role="admin")
    db_session.commit()
    # Hand over (admin2 remains), then admin2 re-activates admin1. Setting
    # active=True skips the guard entirely, so this is always allowed.
    assert repo.set_active(users["admin"].id, False, owner_id=other.id)
    assert repo.set_active(users["admin"].id, True, owner_id=other.id)
    db_session.commit()
    assert repo.authenticate("admin1", "admin-pass") is not None
    assert repo.authenticate("admin2", "admin2-pass") is not None


def test_deactivating_a_non_admin_is_unaffected_by_the_guard(repo, users, db_session):
    # The guard only protects the last active ADMIN; ordinary accounts can
    # always be deactivated (the users-table "delete" path).
    assert repo.set_active(users["alice"].id, False, owner_id=users["admin"].id)
    db_session.commit()
    assert repo.authenticate("alice", "alice-pass") is None


# -- create / validation ---------------------------------------------------


def test_non_admin_cannot_create_user(repo, users):
    with pytest.raises(PermissionError):
        repo.create("carol", "carol-pass", owner_id=users["alice"].id)


def test_admin_can_create_user(repo, users, db_session):
    carol = repo.create("carol", "carol-pass", owner_id=users["admin"].id)
    db_session.commit()
    assert carol.id is not None
    assert repo.authenticate("carol", "carol-pass") is not None


def test_bootstrap_create_without_owner(repo, db_session):
    # First-run bootstrap (task 1.2): no acting user yet.
    first = repo.create("founder", "founder-pass", role="admin")
    db_session.commit()
    assert first.role == "admin"


def test_duplicate_username_rejected(repo, users):
    with pytest.raises(ValueError):
        repo.create("alice", "other-pass", owner_id=users["admin"].id)


# -- set_lang (architecture §14: self-service, no admin bypass) ---------------


def test_user_can_change_own_lang(repo, users, db_session):
    assert repo.set_lang(users["alice"].id, "uk", owner_id=users["alice"].id)
    db_session.commit()
    assert repo.get_by_username("alice").lang == "uk"


def test_user_cannot_change_others_lang(repo, users, db_session):
    assert repo.set_lang(users["bob"].id, "uk", owner_id=users["alice"].id) is False
    db_session.commit()
    assert repo.get_by_username("bob").lang == "en"


def test_admin_cannot_change_others_lang(repo, users, db_session):
    # No admin bypass: a language preference is personal, not administrative.
    assert repo.set_lang(users["alice"].id, "uk", owner_id=users["admin"].id) is False
    db_session.commit()
    assert repo.get_by_username("alice").lang == "en"


def test_set_lang_invalid_lang_rejected(repo, users):
    with pytest.raises(ValueError):
        repo.set_lang(users["alice"].id, "fr", owner_id=users["alice"].id)


def test_set_lang_missing_target_returns_false(repo, users):
    assert repo.set_lang(999999, "uk", owner_id=999999) is False


def test_invalid_role_rejected(repo, users):
    with pytest.raises(ValueError):
        repo.create("x", "y", role="superuser", owner_id=users["admin"].id)


def test_invalid_lang_rejected(repo, users):
    with pytest.raises(ValueError):
        repo.create("x", "y", lang="fr", owner_id=users["admin"].id)
