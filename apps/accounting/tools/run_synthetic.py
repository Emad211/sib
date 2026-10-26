"""Run the accounting app on a synthetic database (never on real data) for manual checks.

    python tools/run_synthetic.py [DB_PATH] [--port 8081]

Default DB: sib/_data/synthetic/clinic_new.db (create it with tools/make_synthetic_db.py).
Logins: manager1 / reception1 / reception2, password `synthetic-pass`.
"""

import argparse
import os
import sys

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_ROOT)
DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(_APP_ROOT)), "_data", "synthetic", "clinic_new.db")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default=DEFAULT_DB)
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args(argv)
    if "_data" + os.sep + "prod_snapshot" in os.path.abspath(a.db):
        sys.exit("refusing to run on a production snapshot")

    from src.config.settings import Config

    Config.DATABASE_PATH = os.path.abspath(a.db)
    Config.BACKUP_FOLDER = os.path.join(os.path.dirname(Config.DATABASE_PATH), "backups")
    from src.adapters.sqlite.core import ensure_database_ready
    from src.app import create_app

    ensure_database_ready()
    create_app().run(host="127.0.0.1", port=a.port, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
