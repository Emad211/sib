"""Shared pieces of the manager blueprint: access guards and the Jalali date parser.

Both guards must be applied BELOW ``@login_required`` (so ``g.user`` is set) and they
reproduce exactly what the old inline ``if g.user['role'] != 'manager'`` checks did.
"""

import functools
from datetime import datetime

import jdatetime
from flask import flash, g, jsonify, redirect, url_for

# Marker attribute set on every guarded view (and propagated by functools.wraps through
# ``login_required``); tests/test_manager_authz.py relies on it.
GUARD_ATTR = "_manager_guard"


def _is_manager():
    return g.user["role"] == "manager"


def manager_required(view=None, *, flash_message=None):
    """HTML routes: a non-manager is redirected to the reception page.

    Usable bare (``@manager_required``) or with the flash text some routes show
    (``@manager_required(flash_message='...')``).
    """

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not _is_manager():
                if flash_message is not None:
                    flash(flash_message, "error")
                return redirect(url_for("reception.index"))
            return fn(*args, **kwargs)

        setattr(wrapper, GUARD_ATTR, "html")
        return wrapper

    return decorate(view) if view is not None else decorate


def manager_api_required(view=None, *, body=None):
    """JSON / export routes: a non-manager gets ``body`` (default ``{'error': 'Unauthorized'}``) with HTTP 403."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not _is_manager():
                payload = {"error": "Unauthorized"} if body is None else dict(body)
                return jsonify(payload), 403
            return fn(*args, **kwargs)

        setattr(wrapper, GUARD_ATTR, "api")
        return wrapper

    return decorate(view) if view is not None else decorate


def jalali_to_start_end(jalali_str, is_start=True):
    """Parse a 'YYYY/MM/DD' Jalali string to a naive datetime (00:00:00, or 23:59:59 when
    ``is_start`` is False). Returns None for an empty or malformed value.

    Each report applies its own default window (7 or 30 days) when this returns None.
    """
    if not jalali_str:
        return None
    try:
        parts = jalali_str.split("/")
        jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
        gd = jd.togregorian()
        if is_start:
            return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
        else:
            return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
    except Exception:
        return None
