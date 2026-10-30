"""Run the shift service with uvicorn on the configured LAN port (8443 by default)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn  # noqa: E402

from shift.app import create_app  # noqa: E402
from shift.config.settings import Settings  # noqa: E402


def main() -> int:
    """Serve the ASGI app using settings from the SHIFT_* environment variables."""
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
