"""CSRF protection for every state-changing request (SEC-05, ACC-2.1).

* One random token per session (`session["_csrf"]`).
* Every POST/PUT/PATCH/DELETE must carry it, in the `X-CSRFToken` header (fetch/XHR)
  or the `csrf_token` form field (classic forms). Missing or wrong -> 400, nothing runs.
* Every HTML response gets `<meta name="csrf-token">` plus `static/js/csrf.js`, which adds
  the token to same-origin fetch/XHR calls and to every form when it is submitted. No
  template had to change, and pages added later are protected automatically.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from flask import Flask, jsonify, render_template, request, session, url_for
from markupsafe import escape

SESSION_KEY = "_csrf"
HEADER = "X-CSRFToken"
FIELD = "csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

log = logging.getLogger("hesabdari.csrf")


def get_token() -> str:
    token = session.get(SESSION_KEY)
    if not token:
        token = session[SESSION_KEY] = secrets.token_urlsafe(32)
    return token


def _wants_json() -> bool:
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or HEADER in request.headers
        or "application/json" in request.headers.get("Accept", "")
    )


def init_csrf(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = get_token

    @app.before_request
    def _check_csrf():
        if request.method not in UNSAFE_METHODS or app.config.get("CSRF_DISABLED"):
            return None
        expected = session.get(SESSION_KEY)
        sent = request.headers.get(HEADER) or request.form.get(FIELD)
        if expected and sent and hmac.compare_digest(str(sent), str(expected)):
            return None
        log.warning("CSRF check failed: %s %s", request.method, request.path)
        message = "نشست شما منقضی شده یا درخواست نامعتبر است. صفحه را دوباره باز کنید و دوباره امتحان کنید."
        if _wants_json():
            return jsonify({"success": False, "error": message}), 400
        return render_template("errors/error.html", code=400, title="درخواست نامعتبر", message=message, ref=None), 400

    @app.after_request
    def _inject(resp):
        if resp.mimetype != "text/html" or resp.direct_passthrough or 300 <= resp.status_code < 400:
            return resp
        body = resp.get_data(as_text=True)
        marker = "</head>" if "</head>" in body else ("</body>" if "</body>" in body else None)
        if marker is None:
            return resp
        tag = (
            f'<meta name="csrf-token" content="{escape(get_token())}">'
            f'<script src="{url_for("static", filename="js/csrf.js")}"></script>'
        )
        resp.set_data(body.replace(marker, tag + marker, 1))
        return resp
