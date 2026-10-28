"""FastAPI application factory for the shift service."""

from fastapi import FastAPI

from shift.__version__ import __version__


def create_app() -> FastAPI:
    """Build the ASGI application; routers and lifespan are added per feature step."""
    app = FastAPI(title="Sib Shift", version=__version__)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe used by the deployment wrapper and CI smoke test."""
        return {"status": "ok", "version": __version__}

    return app
