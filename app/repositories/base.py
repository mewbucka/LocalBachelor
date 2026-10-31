"""Tenant-scoped repository base (architecture §11, task 1.7).

The enforcement point for tenant isolation: every tenant repository
derives from ``TenantScopedRepository`` so the owner filter is applied
INSIDE the repository, never by the caller.

Contract:

- The constructor REQUIRES ``owner_id`` (no default). There is no such
  thing as an unscoped repository instance, so no code path can reach an
  unfiltered query on a tenant table.
- ``owner_id`` is the bound tenant. Subclasses declare ``model`` (the ORM
  model they serve) and ``tenant_column`` (the column that identifies the
  tenant in that model's rows). The base refuses to construct a subclass
  that leaves either unset — it never guesses how a tenant is mapped.
  - Content tables (settings, projects, sources, chunks, jobs, notes):
    ``tenant_column`` is their ``owner_id`` column. ``owner_id=None``
    renders ``owner_id IS NULL`` — for settings that is the admin-default
    namespace, a legitimate shared namespace (§3).
  - Identity tables (users, which has no owner column): ``tenant_column``
    is the primary key — the tenant is the row itself. ``owner_id=None``
    matches nothing.
- ``_scoped()`` is the ONLY way a subclass query obtains its tenant
  filter. Subclasses must not issue any query on ``model`` that bypasses
  it (administrative operations use the explicitly named ``*_admin``
  methods plus their guard, not a "skip the filter" flag).
- Repositories whose tenant is the row itself (users) must call
  ``_require_bound()`` from every tenant method: an unbound instance
  would silently match nothing and a caller could mistake that for
  "not found", so it is refused loudly with ``TenantScopeError`` instead.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select
from sqlalchemy.orm import Session


class TenantScopeError(RuntimeError):
    """A tenant-scoped operation was attempted with no bound tenant.

    Raised rather than silently returning an empty result (see module
    docstring). Bootstrap paths (e.g. first-run account creation) run on
    a deliberately unbound instance and use their own explicitly named
    entry points, never the tenant-scoped ones.
    """


class TenantScopedRepository:
    """Base for tenant-scoped repositories (architecture §11).

    Subclasses set ``model`` and ``tenant_column`` and route every
    tenant query through :meth:`_scoped`.
    """

    #: The ORM model this repository serves.
    model: Any = None
    #: The column identifying the tenant in rows of ``model`` (see the
    #: module docstring: owner_id for content tables, primary key for
    #: identity tables).
    tenant_column: Any = None

    def __init__(self, session: Session, owner_id: int | None) -> None:
        name = type(self).__name__
        if self.model is None:
            raise TypeError(f"{name} must set 'model'")
        if self._tenant_column is None:
            raise TypeError(
                f"{name} must set 'tenant_column' (the column that "
                "identifies the tenant in rows of its model)"
            )
        self._session = session
        self._owner_id = owner_id

    @property
    def _tenant_column(self) -> Any:
        """The subclass's tenant column, read at CLASS level.

        ``tenant_column`` holds a SQLAlchemy ``InstrumentedAttribute``
        (a data descriptor): reading it through the instance
        (``self.tenant_column``) would invoke its ``__get__`` and crash
        before the mapper is configured. Class-level access returns the
        column object itself, which is what query-building needs.
        """
        return type(self).tenant_column

    @property
    def session(self) -> Session:
        return self._session

    @property
    def owner_id(self) -> int | None:
        """The bound tenant (``None`` = unbound / admin-default namespace)."""
        return self._owner_id

    def _scoped(self, statement: Select) -> Select:
        """Apply the tenant filter to ``statement``.

        The bound tenant's rows only: ``tenant_column == owner_id`` (an
        ``owner_id`` column renders ``IS NULL`` for the admin-default
        namespace; a primary key matches nothing).
        """
        return statement.where(self._tenant_column == self._owner_id)

    def _require_bound(self) -> None:
        """Refuse tenant-scoped operations on an unbound instance."""
        if self._owner_id is None:
            raise TenantScopeError(
                f"{type(self).__name__} requires a bound tenant; "
                "owner_id=None is only valid for the admin-default namespace"
            )
