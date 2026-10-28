"""FastAPI application factory for the shift service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shift.__version__ import __version__
from shift.adapters.db.core import Database
from shift.config.logging import configure_logging
from shift.config.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application with logging, database and secret initialised."""
    resolved = settings or Settings.from_env()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved.database_path)
        database.migrate()
        application.state.database = database
        application.state.secret_key = resolved.read_or_create_secret()
        try:
            yield
        finally:
            database.close()

    app = FastAPI(title="Sib Shift", version=__version__, lifespan=lifespan)
    app.state.settings = resolved

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe used by the deployment wrapper and CI smoke test."""
        return {"status": "ok", "version": __version__}

    return app
