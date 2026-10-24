"""Data-access layer: tenant-scoped repositories (architecture section 11).

Every query filters by owner (or project membership) INSIDE the repository,
so a method can never return another tenant's row by caller mistake.
"""
