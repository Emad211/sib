"""Run the shift service with uvicorn on the clinic LAN port (8443)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn  # noqa: E402

from shift.app import create_app  # noqa: E402

HOST = "127.0.0.1"
PORT = 8443


def main() -> int:
    """Serve the ASGI app; host and port are overridable in the config step."""
    uvicorn.run(create_app(), host=HOST, port=PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
