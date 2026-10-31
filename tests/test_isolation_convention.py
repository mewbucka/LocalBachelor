"""Tenant-isolation convention tests (architecture §11, task 1.7).

Structural guards so a new entity (Phase 3+) cannot silently regress to
caller-side owner filters:

1. every ``*Repository`` class in ``app/repositories/`` derives from
   ``TenantScopedRepository`` — the only base that applies the owner
   filter INSIDE the repository, never by the caller;
2. every repository constructor REQUIRES ``owner_id`` (no default): an
   unscoped repository instance cannot be constructed.

The mandatory per-entity A≠B negative suites (roadmap task → suite):

- users    → tests/test_repositories_users.py     (1.1, 1.7)
- settings → tests/test_repositories_settings.py  (1.7)
- projects → task 3.2 (the suite lands with the entity)
- sources  → task 3.6 (the suite lands with the entity)
- chunks   → tasks 3.7/3.8 (the suite lands with the entity)
- jobs     → task 3.5 (the suite lands with the entity)
- notes    → task 5.5 (the suite lands with the entity)

When a Phase 3+ entity lands, its repository module is picked up here
automatically (checks 1–2 apply) and its A≠B suite must be added to
``REQUIRED_NEGATIVE_SUITES``.
"""

import importlib
import inspect
import pkgutil
import types

import pytest

import app.repositories
from app.repositories.base import TenantScopedRepository

# The A≠B negative suite every repository module present today must ship.
REQUIRED_NEGATIVE_SUITES = {
    "users": "tests.test_repositories_users",
    "settings": "tests.test_repositories_settings",
}


def _repository_modules() -> list[types.ModuleType]:
    """Every module under ``app/repositories/`` (imported)."""
    return [
        importlib.import_module(f"app.repositories.{info.name}")
        for info in pkgutil.iter_modules(app.repositories.__path__)
    ]


def _repository_classes(module: types.ModuleType) -> list[type]:
    """The ``*Repository`` classes visible in ``module`` (the base excluded)."""
    return [
        cls
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if _.endswith("Repository") and cls is not TenantScopedRepository
    ]


def _owner_id_violation(cls: type) -> str | None:
    """The owner_id convention violation in ``cls``'s constructor, if any."""
    init = cls.__init__  # its own override, or the base's through the MRO
    owner = inspect.signature(init).parameters.get("owner_id")
    if owner is None:
        return "no owner_id parameter"
    if owner.default is not inspect.Parameter.empty:
        return f"owner_id defaults to {owner.default!r}"
    return None


# -- the convention ----------------------------------------------------------------


def test_repository_package_is_not_empty():
    # A packaging regression that empties the package must not silently
    # pass the convention (an empty package trivially satisfies it).
    assert _repository_modules()


def test_every_repository_derives_from_the_scoped_base():
    offenders = [
        f"{module.__name__}.{cls.__name__}"
        for module in _repository_modules()
        for cls in _repository_classes(module)
        if not issubclass(cls, TenantScopedRepository)
    ]
    assert not offenders, f"repositories not on the scoped base: {offenders}"


def test_every_repository_requires_owner_id():
    offenders = []
    for module in _repository_modules():
        for cls in _repository_classes(module):
            if not issubclass(cls, TenantScopedRepository):
                continue  # already reported by the base-derivation test
            violation = _owner_id_violation(cls)
            if violation:
                offenders.append(f"{module.__name__}.{cls.__name__}: {violation}")
    assert not offenders, f"constructors not requiring owner_id: {offenders}"


# -- the mandatory per-entity A≠B suites ---------------------------------------------


@pytest.mark.parametrize(("entity", "suite"), sorted(REQUIRED_NEGATIVE_SUITES.items()))
def test_required_negative_suite_exists(entity: str, suite: str) -> None:
    # The entity's A≠B negative suite must exist and import cleanly.
    try:
        importlib.import_module(suite)
    except ImportError as exc:
        pytest.fail(f"entity {entity!r} is missing its A≠B negative suite {suite!r}: {exc}")
