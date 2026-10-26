"""SQLAlchemy models (architecture section 3).

Importing this package registers every model on ``Base.metadata``, which
is what alembic autogenerate and ``Base.metadata.create_all`` rely on.
"""

from app.models.base import Base, utc_now_iso
from app.models.settings import Setting
from app.models.user import User

__all__ = ["Base", "Setting", "User", "utc_now_iso"]
