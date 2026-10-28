"""FastAPI application factory for the shift service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from shift.__version__ import __version__
from shift.adapters.db.core import Database
from shift.api import routes_auth, routes_devices
from shift.api.errors import install_error_handlers
from shift.api.routes_events import router as events_router
from shift.api.routes_web import router as web_router
from shift.config.logging import configure_logging
from shift.config.settings import Settings
from shift.services.auth import AuthService
from shift.services.devices import DeviceService
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application with logging, database and secret initialised."""
    resolved = settings or Settings.from_env()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved.database_path)
        database.migrate()
        application.state.database = database
        application.state.events = EventStore(database, hash_chain=resolved.hash_chain)
        application.state.projections = ProjectionEngine(database)
        application.state.secret_key = resolved.read_or_create_secret()
        application.state.auth = AuthService(database, pepper=application.state.secret_key)
        application.state.devices = DeviceService(database)
        try:
            yield
        finally:
            database.close()

    app = FastAPI(
        title="Sib Shift",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = resolved
    install_error_handlers(app)
    app.include_router(routes_auth.router)
    app.include_router(routes_devices.router)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(events_router)
    app.include_router(web_router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe used by the deployment wrapper and CI smoke test."""
        return {"status": "ok", "version": __version__}

    return app
