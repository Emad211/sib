"""FastAPI application factory for the shift service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from shift.__version__ import __version__
from shift.adapters.db.core import Database
from shift.api import deps, routes_auth, routes_catalog, routes_devices, routes_events, routes_settings, routes_web
from shift.api.errors import install_error_handlers
from shift.common.clock import SystemClock
from shift.config.logging import configure_logging
from shift.config.settings import Settings
from shift.domain.permissions import Permission
from shift.services.auth import AuthService
from shift.services.catalog import CatalogService
from shift.services.devices import DeviceService
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService


def _catalog_service(request: Request) -> CatalogService:
    """The catalog service created in the lifespan (manager routes only)."""
    return request.app.state.catalog


def _settings_service(request: Request) -> SettingsService:
    """The settings service created in the lifespan (manager routes only)."""
    return request.app.state.settings_service


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application with logging, database and secrets initialised."""
    resolved = settings or Settings.from_env()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        clock = SystemClock()
        database = Database(resolved.database_path)
        database.migrate()
        application.state.clock = clock
        application.state.database = database
        application.state.events = EventStore(database, clock=clock, hash_chain=resolved.hash_chain)
        application.state.projections = ProjectionEngine(database, clock=clock)
        application.state.secret_key = resolved.read_or_create_secret()
        application.state.auth = AuthService(database, clock=clock, pepper=application.state.secret_key)
        application.state.devices = DeviceService(database, clock=clock)
        application.state.catalog = CatalogService(database, clock=clock)
        application.state.settings_service = SettingsService(database, clock=clock)
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
    app.include_router(
        routes_catalog.build_catalog_router(
            guard=deps.require(Permission.CATALOG_MANAGE),
            catalog=_catalog_service,
            running_shift=deps.running_shift,
        )
    )
    app.include_router(
        routes_settings.build_settings_router(
            guard=deps.require(Permission.SETTINGS_CHANGE),
            settings=_settings_service,
            running_shift=deps.running_shift,
        )
    )

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(routes_events.router)
    app.include_router(routes_web.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe used by the deployment wrapper and CI smoke test."""
        return {"status": "ok", "version": __version__}

    return app
