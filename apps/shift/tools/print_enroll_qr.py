"""Print one pairing QR for one phone: one token, ten minutes, single use.

The QR encodes `<origin>/enroll#t=<token>`; the token sits in the URL fragment, so it
never reaches the server logs, the proxy access log or a `Referer` header. The image is
written to `--out` (SVG or PNG by extension) and the URL is printed for the operator.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import segno  # noqa: E402

from shift.adapters.db.core import Database  # noqa: E402
from shift.common.clock import to_tehran  # noqa: E402
from shift.common.jalali import format_jalali  # noqa: E402
from shift.services.devices import DeviceError, DeviceService  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Mint a token, write the QR and print the URL; exit 1 when the staff id is unknown."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the shift SQLite database")
    parser.add_argument("--staff-id", required=True, help="staff_id the phone will belong to")
    parser.add_argument("--origin", required=True, help="public origin, e.g. https://shift.example.ir")
    parser.add_argument("--out", type=Path, help="QR image to write (.svg or .png); omit to print to console")
    parser.add_argument("--created-by", default="cli", help="actor recorded on the token")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1

    database = Database(args.db)
    try:
        offer = DeviceService(database).mint(staff_id=args.staff_id, created_by=args.created_by, origin=args.origin)
    except DeviceError as exc:
        print(f"cannot mint an enrollment token: {exc}")
        return 1
    finally:
        database.close()

    qr = segno.make(offer.url, error="m")
    if args.out is None:
        qr.terminal(compact=True)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        qr.save(str(args.out), scale=8, border=2)

    expires = to_tehran(offer.expires_at)
    print(f"device_id: {offer.device_id}")
    print(f"staff_id:  {offer.staff_id}")
    print(f"valid_to:  {format_jalali(expires.date())} {expires:%H:%M} (Tehran)")
    print(f"url:       {offer.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
