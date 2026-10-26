"""Manager area (/manager/*): one blueprint, split by topic (ACC-2.6 / DEBT-01).

The blueprint is created first; every submodule registers its routes on it at import.
Endpoint names (``manager.index``, ``manager.export_invoices_csv`` ...) did not change.
"""

from flask import Blueprint

bp = Blueprint("manager", __name__, url_prefix="/manager")

from . import (  # noqa: E402, F401  (imported for their route registrations)
    arrears,
    dashboard,
    exports,
    logs,
    payroll,
    reports,
    settings,
    tariffs,
    users,
)
