"""Manager area (/manager/*): one blueprint, split by topic (ACC-2.6 / DEBT-01).

The blueprint is created first; every submodule registers its routes on it at import.
Endpoint names (``manager.index``, ``manager.export_invoices_csv`` ...) did not change.
"""

import logging
import sqlite3

from flask import Blueprint, flash, redirect, request, url_for

bp = Blueprint("manager", __name__, url_prefix="/manager")

log = logging.getLogger(__name__)


@bp.errorhandler(sqlite3.IntegrityError)
def _integrity_error(error):
    """A duplicate name (nursing service, consumable, insurance, username ...) used to end in a 500 page.
    Nothing was saved; tell the manager and send them back to the page they were on."""
    from src.adapters.sqlite.core import get_db

    get_db().rollback()
    log.warning("manager form rejected by the database: %s %s (%s)", request.method, request.path, error)
    flash("این مورد قبلاً وجود دارد یا با داده‌های دیگر ناسازگار است؛ چیزی ذخیره نشد.", "error")
    rule = request.url_rule
    if rule is not None and "GET" in rule.methods:
        return redirect(request.path)
    return redirect(url_for("manager.index"))


from . import (  # noqa: E402, F401  (imported for their route registrations)
    arrears,
    dashboard,
    expenses,
    exports,
    logs,
    payroll,
    reports,
    settings,
    tariffs,
    users,
)
