from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, g

from src.api.auth import login_required
from src.services.doctor_room_service import DoctorRoomService

bp = Blueprint("doctor_room", __name__, url_prefix="/doctor")


def _require_doctor() -> bool:
    return bool(getattr(g, "user", None)) and g.user["role"] == "doctor"


def _get_doctor_staff_id() -> int | None:
    """Return the logged-in doctor's staff id from users.staff_id."""
    try:
        staff_id = g.user["staff_id"]
        return int(staff_id) if staff_id else None
    except Exception:
        return None


@bp.route("/room")
@login_required
def room_page():
    if not _require_doctor():
        return redirect(url_for("dashboard.index"))

    if not _get_doctor_staff_id():
        return render_template(
            "doctor/room.html",
            error="حساب کاربری پزشک به کادر درمان متصل نیست. از مدیریت کاربران، پزشک را با نام کاربری بسازید.",
        )

    return render_template("doctor/room.html")


@bp.route("/api/room/current")
@login_required
def api_room_current():
    if not _require_doctor():
        return jsonify({"error": "unauthorized"}), 403

    doctor_staff_id = _get_doctor_staff_id()
    if not doctor_staff_id:
        return jsonify({"error": "doctor_not_linked"}), 400

    service = DoctorRoomService()
    payload = service.get_current(doctor_staff_id)
    return jsonify({"success": True, "data": payload}), 200


@bp.route("/api/open-invoices")
@login_required
def api_open_invoices():
    if not _require_doctor():
        return jsonify({"error": "unauthorized"}), 403

    doctor_staff_id = _get_doctor_staff_id()
    if not doctor_staff_id:
        return jsonify({"error": "doctor_not_linked"}), 400

    query = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", type=int) or 50

    service = DoctorRoomService()
    results = service.search_open_invoices(query=query, limit=min(max(limit, 1), 100))

    return jsonify({"success": True, "items": results}), 200


@bp.route("/api/room/set", methods=["POST"])
@login_required
def api_room_set():
    if not _require_doctor():
        return jsonify({"error": "unauthorized"}), 403

    doctor_staff_id = _get_doctor_staff_id()
    if not doctor_staff_id:
        return jsonify({"error": "doctor_not_linked"}), 400

    invoice_id = request.form.get("invoice_id", type=int)
    if not invoice_id:
        return jsonify({"error": "invoice_id_required"}), 400

    service = DoctorRoomService()
    try:
        service.set_current_invoice(
            doctor_staff_id=doctor_staff_id,
            invoice_id=int(invoice_id),
            set_by_user_id=int(g.user["id"]),
        )
        return jsonify({"success": True}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "server_error"}), 500


@bp.route("/api/room/clear", methods=["POST"])
@login_required
def api_room_clear():
    if not _require_doctor():
        return jsonify({"error": "unauthorized"}), 403

    doctor_staff_id = _get_doctor_staff_id()
    if not doctor_staff_id:
        return jsonify({"error": "doctor_not_linked"}), 400

    service = DoctorRoomService()
    service.clear_current_invoice(doctor_staff_id=doctor_staff_id, set_by_user_id=int(g.user["id"]))
    return jsonify({"success": True}), 200
