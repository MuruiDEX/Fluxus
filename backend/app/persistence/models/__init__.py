"""Model registry.

Importing the usage models here guarantees `Base.metadata` is complete the
moment any model is imported (conftest's `create_all`, migrations metadata
checks, etc.), independent of which repository happens to be imported first.
"""

from app.persistence.models import usage  # noqa: F401
