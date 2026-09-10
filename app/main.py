"""Select an explicit persistence runtime; never fall back after a storage error."""

from app.config import settings

if settings().storage_backend == "sql":
    from app.sql_app import app, mcp_lifespan  # noqa: F401
else:
    from app.document_api import create_app

    app = create_app()
