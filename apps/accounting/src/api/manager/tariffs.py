import logging

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.utils import format_jalali_datetime
from src.services import tariff_audit
from src.services.exports import table_response

from . import bp
from ._common import manager_api_required, manager_required

logger = logging.getLogger(__name__)


@bp.route("/tariffs")
@login_required
@manager_required(flash_message="دسترسی محدود: فقط مدیر می‌تواند به این بخش دسترسی داشته باشد.")
def tariffs_index():
    """صفحه اصلی تعرفه‌ها - هاب"""

    return render_template("manager/tariffs.html")


@bp.route("/tariffs/history")
@login_required
@manager_required(flash_message="دسترسی محدود")
def tariff_history():
    """Who changed which tariff, when, from what to what (ACC-2.8). CSV/Excel via ?format=."""
    from src.services.tariff_audit import KIND_LABELS

    kind = request.args.get("kind", "").strip()
    kind = kind if kind in KIND_LABELS else None
    rows = tariff_audit.history(get_db(), kind=kind, limit=20000)
    if request.args.get("format") in ("csv", "xlsx"):
        data = [
            [
                format_jalali_datetime(r["changed_at"]),
                r["username"] or "",
                r["kind_label"],
                r["label"] or "",
                r["action_label"],
                r["field"] or "",
                r["old_value"] or "",
                r["new_value"] or "",
            ]
            for r in rows
        ]
        headers = ["زمان", "کاربر", "نوع", "مورد", "عملیات", "تغییر", "قبل", "بعد"]
        return table_response(data, headers, "tariff_history", "تاریخچه تعرفه")
    for r in rows:
        r["changed_at_fa"] = format_jalali_datetime(r["changed_at"])
    return render_template(
        "manager/tariff_history.html", rows=rows[:500], total=len(rows), kind=kind, kinds=KIND_LABELS
    )


@bp.route("/tariffs/nursing", methods=["GET", "POST"])
@login_required
@manager_required(flash_message="دسترسی محدود")
def nursing_tariffs():
    """تعرفه خدمات پرستاری"""

    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            service_name = request.form.get("service_name", "").strip()
            unit_price = request.form.get("unit_price", "0")
            try:
                unit_price = float(unit_price.replace(",", ""))
            except Exception:
                unit_price = 0

            if service_name:
                cur = db.execute(
                    "INSERT INTO nursing_services (service_name, unit_price, is_active) VALUES (?, ?, 1)",
                    (service_name, unit_price),
                )
                tariff_audit.record_created(db, "nursing", cur.lastrowid, service_name, {"price": unit_price})
                db.commit()
                flash(f'خدمت "{service_name}" با موفقیت اضافه شد.', "success")

        elif action == "update":
            service_id = request.form.get("service_id")
            service_name = request.form.get("service_name", "").strip()
            unit_price = request.form.get("unit_price", "0")
            is_active = request.form.get("is_active") == "1"
            try:
                unit_price = float(unit_price.replace(",", ""))
            except Exception:
                unit_price = 0

            if service_id and service_name:
                old = db.execute("SELECT * FROM nursing_services WHERE id = ?", (service_id,)).fetchone()
                db.execute(
                    "UPDATE nursing_services SET service_name = ?, unit_price = ?, is_active = ? WHERE id = ?",
                    (service_name, unit_price, 1 if is_active else 0, service_id),
                )
                if old:
                    tariff_audit.record_update(
                        db,
                        "nursing",
                        service_id,
                        service_name,
                        {
                            "name": (old["service_name"], service_name),
                            "price": (old["unit_price"], unit_price),
                            "active": (old["is_active"], 1 if is_active else 0),
                        },
                    )
                db.commit()
                flash("تعرفه با موفقیت بروزرسانی شد.", "success")

        elif action == "delete":
            service_id = request.form.get("service_id")
            if service_id:
                old = db.execute("SELECT * FROM nursing_services WHERE id = ?", (service_id,)).fetchone()
                db.execute("DELETE FROM nursing_services WHERE id = ?", (service_id,))
                if old:
                    tariff_audit.record_deleted(
                        db, "nursing", service_id, old["service_name"], {"price": old["unit_price"]}
                    )
                db.commit()
                flash("خدمت با موفقیت حذف شد.", "success")

        return redirect(url_for("manager.nursing_tariffs"))

    services = db.execute("SELECT * FROM nursing_services ORDER BY is_active DESC, service_name").fetchall()

    return render_template("manager/tariffs_nursing.html", services=services)


@bp.route("/tariffs/insurance", methods=["GET", "POST"])
@login_required
@manager_required(flash_message="دسترسی محدود")
def insurance_tariffs():
    """تعرفه بیمه‌ها و تعریف انواع بیمه"""

    db = get_db()

    # اطمینان از وجود ستون‌های جدید
    try:
        db.execute("ALTER TABLE visit_tariffs ADD COLUMN nursing_covers INTEGER DEFAULT 0")
    except Exception:
        logger.warning("insurance_tariffs: ignored error", exc_info=True)
    try:
        db.execute("ALTER TABLE visit_tariffs ADD COLUMN is_base_tariff INTEGER DEFAULT 0")
    except Exception:
        logger.warning("insurance_tariffs: ignored error", exc_info=True)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            insurance_type = request.form.get("insurance_type", "").strip()
            tariff_price = request.form.get("tariff_price", "0")
            nursing_covers = request.form.get("nursing_covers") == "1"
            is_supplementary = request.form.get("is_supplementary") == "1"
            try:
                tariff_price = float(tariff_price.replace(",", ""))
            except Exception:
                tariff_price = 0

            if insurance_type:
                # چک کردن تکراری نبودن
                existing = db.execute(
                    "SELECT id FROM visit_tariffs WHERE insurance_type = ?", (insurance_type,)
                ).fetchone()
                if existing:
                    flash(f'بیمه "{insurance_type}" قبلاً تعریف شده است.', "error")
                else:
                    cur = db.execute(
                        "INSERT INTO visit_tariffs (insurance_type, tariff_price, nursing_covers, is_active, is_supplementary) VALUES (?, ?, ?, 1, ?)",
                        (insurance_type, tariff_price, 1 if nursing_covers else 0, 1 if is_supplementary else 0),
                    )
                    tariff_audit.record_created(
                        db,
                        "insurance",
                        cur.lastrowid,
                        insurance_type,
                        {
                            "price": tariff_price,
                            "nursing_covers": 1 if nursing_covers else 0,
                            "supplementary": 1 if is_supplementary else 0,
                        },
                    )
                    db.commit()
                    flash(f'بیمه "{insurance_type}" با موفقیت اضافه شد.', "success")

        elif action == "update":
            tariff_id = request.form.get("tariff_id")
            insurance_type = request.form.get("insurance_type", "").strip()
            tariff_price = request.form.get("tariff_price", "0")
            nursing_covers = request.form.get("nursing_covers") == "1"
            is_active = request.form.get("is_active") == "1"
            is_supplementary = request.form.get("is_supplementary") == "1"
            try:
                tariff_price = float(tariff_price.replace(",", ""))
            except Exception:
                tariff_price = 0

            if tariff_id and insurance_type:
                old = db.execute("SELECT * FROM visit_tariffs WHERE id = ?", (tariff_id,)).fetchone()
                db.execute(
                    "UPDATE visit_tariffs SET insurance_type = ?, tariff_price = ?, nursing_covers = ?, is_active = ?, is_supplementary = ? WHERE id = ?",
                    (
                        insurance_type,
                        tariff_price,
                        1 if nursing_covers else 0,
                        1 if is_active else 0,
                        1 if is_supplementary else 0,
                        tariff_id,
                    ),
                )
                if old:
                    tariff_audit.record_update(
                        db,
                        "insurance",
                        tariff_id,
                        insurance_type,
                        {
                            "name": (old["insurance_type"], insurance_type),
                            "price": (old["tariff_price"], tariff_price),
                            "nursing_covers": (old["nursing_covers"], 1 if nursing_covers else 0),
                            "active": (old["is_active"], 1 if is_active else 0),
                            "supplementary": (old["is_supplementary"], 1 if is_supplementary else 0),
                        },
                    )
                db.commit()
                flash("تعرفه بیمه با موفقیت بروزرسانی شد.", "success")

        elif action == "delete":
            tariff_id = request.form.get("tariff_id")
            if tariff_id:
                # اجازه حذف تعرفه پایه را ندهیم
                base_check = db.execute("SELECT * FROM visit_tariffs WHERE id = ?", (tariff_id,)).fetchone()
                if base_check and base_check["is_base_tariff"]:
                    flash("تعرفه پایه (آزاد) قابل حذف نیست.", "error")
                else:
                    db.execute("DELETE FROM visit_tariffs WHERE id = ?", (tariff_id,))
                    if base_check:
                        tariff_audit.record_deleted(
                            db,
                            "insurance",
                            tariff_id,
                            base_check["insurance_type"],
                            {"price": base_check["tariff_price"]},
                        )
                    db.commit()
                    flash("بیمه با موفقیت حذف شد.", "success")

        elif action == "set_base":
            # تنظیم تعرفه پایه (ویزیت آزاد)
            base_price = request.form.get("base_visit_price", "0")
            try:
                base_price = float(base_price.replace(",", ""))
            except Exception:
                base_price = 0

            # بررسی وجود تعرفه پایه
            existing_base = db.execute(
                "SELECT id, insurance_type, tariff_price FROM visit_tariffs WHERE is_base_tariff = 1"
            ).fetchone()
            if existing_base:
                db.execute("UPDATE visit_tariffs SET tariff_price = ? WHERE is_base_tariff = 1", (base_price,))
                tariff_audit.record_update(
                    db,
                    "insurance",
                    existing_base["id"],
                    existing_base["insurance_type"],
                    {"price": (existing_base["tariff_price"], base_price)},
                )
            else:
                cur = db.execute(
                    "INSERT INTO visit_tariffs (insurance_type, tariff_price, nursing_covers, is_active, is_supplementary, is_base_tariff) VALUES ('آزاد', ?, 0, 1, 0, 1)",
                    (base_price,),
                )
                tariff_audit.record_created(db, "insurance", cur.lastrowid, "آزاد", {"price": base_price})
            db.commit()
            flash("تعرفه پایه (آزاد) با موفقیت ذخیره شد.", "success")

        return redirect(url_for("manager.insurance_tariffs"))

    # دریافت تعرفه پایه
    base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()

    # دریافت سایر تعرفه‌ها (بدون تعرفه پایه)
    tariffs = db.execute(
        "SELECT * FROM visit_tariffs WHERE is_base_tariff = 0 OR is_base_tariff IS NULL ORDER BY is_active DESC, insurance_type"
    ).fetchall()

    return render_template("manager/tariffs_insurance.html", tariffs=tariffs, base_tariff=base_tariff)


@bp.route("/insurance/<string:insurance_type>/nursing_exclusions", methods=["GET"])
@login_required
@manager_api_required
def get_nursing_exclusions(insurance_type: str):
    """Return list of nursing services with exclusion flag for a given insurance."""
    db = get_db()
    # Load all nursing services
    services = db.execute(
        "SELECT id, service_name, unit_price, is_active FROM nursing_services ORDER BY service_name"
    ).fetchall()
    # Load exclusions for this insurance
    rows = db.execute(
        "SELECT nursing_service_id FROM insurance_nursing_exclusions WHERE insurance_type = ?", (insurance_type,)
    ).fetchall()
    excluded = {r["nursing_service_id"] for r in rows}
    result = []
    for s in services:
        result.append(
            {
                "id": s["id"],
                "service_name": s["service_name"],
                "unit_price": s["unit_price"],
                "is_active": s["is_active"],
                "excluded": s["id"] in excluded,
            }
        )
    return jsonify({"insurance_type": insurance_type, "services": result})


@bp.route("/insurance/<string:insurance_type>/nursing_exclusions", methods=["POST"])
@login_required
@manager_api_required
def set_nursing_exclusions(insurance_type: str):
    """Replace nursing exclusions for an insurance. Accepts JSON: { service_ids: [1,2,3] }"""
    data = request.get_json() or {}
    service_ids = data.get("service_ids")
    if service_ids is None:
        return jsonify({"error": "service_ids required"}), 400
    try:
        service_ids = [int(x) for x in service_ids]
    except Exception:
        return jsonify({"error": "service_ids must be integers list"}), 400
    db = get_db()
    before = {
        r["nursing_service_id"]
        for r in db.execute(
            "SELECT nursing_service_id FROM insurance_nursing_exclusions WHERE insurance_type = ?", (insurance_type,)
        )
    }
    # Replace: delete existing for this insurance, then insert provided ones
    db.execute("DELETE FROM insurance_nursing_exclusions WHERE insurance_type = ?", (insurance_type,))
    for sid in service_ids:
        db.execute(
            "INSERT INTO insurance_nursing_exclusions (insurance_type, nursing_service_id) VALUES (?, ?)",
            (insurance_type, sid),
        )
    tariff_audit.record_exclusions(db, insurance_type, before, set(service_ids))
    db.commit()
    return jsonify({"success": True, "insurance_type": insurance_type, "service_ids": service_ids})


@bp.route("/tariffs/consumables", methods=["GET", "POST"])
@login_required
@manager_required(flash_message="دسترسی محدود")
def consumables_tariffs():
    """تعرفه مصرفی‌ها - دارو و عمومی"""

    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            name = request.form.get("name", "").strip()
            default_price = request.form.get("default_price", "0")
            category = request.form.get("category", "supply")
            try:
                default_price = float(default_price.replace(",", ""))
            except Exception:
                default_price = 0

            if name:
                # چک کردن تکراری نبودن
                existing = db.execute("SELECT id FROM consumable_tariffs WHERE name = ?", (name,)).fetchone()
                if existing:
                    flash(f'مصرفی "{name}" قبلاً تعریف شده است.', "error")
                else:
                    cur = db.execute(
                        "INSERT INTO consumable_tariffs (name, default_price, category, is_active) VALUES (?, ?, ?, 1)",
                        (name, default_price, category),
                    )
                    tariff_audit.record_created(db, "consumable", cur.lastrowid, name, {"price": default_price})
                    db.commit()
                    flash(f'مصرفی "{name}" با موفقیت اضافه شد.', "success")

        elif action == "update":
            item_id = request.form.get("item_id")
            name = request.form.get("name", "").strip()
            default_price = request.form.get("default_price", "0")
            category = request.form.get("category", "supply")
            is_active = request.form.get("is_active") == "1"
            try:
                default_price = float(default_price.replace(",", ""))
            except Exception:
                default_price = 0

            if item_id and name:
                old = db.execute("SELECT * FROM consumable_tariffs WHERE id = ?", (item_id,)).fetchone()
                db.execute(
                    "UPDATE consumable_tariffs SET name = ?, default_price = ?, category = ?, is_active = ? WHERE id = ?",
                    (name, default_price, category, 1 if is_active else 0, item_id),
                )
                if old:
                    tariff_audit.record_update(
                        db,
                        "consumable",
                        item_id,
                        name,
                        {
                            "name": (old["name"], name),
                            "price": (old["default_price"], default_price),
                            "category": (old["category"], category),
                            "active": (old["is_active"], 1 if is_active else 0),
                        },
                    )
                db.commit()
                flash("مصرفی با موفقیت بروزرسانی شد.", "success")

        elif action == "delete":
            item_id = request.form.get("item_id")
            if item_id:
                old = db.execute("SELECT * FROM consumable_tariffs WHERE id = ?", (item_id,)).fetchone()
                db.execute("DELETE FROM consumable_tariffs WHERE id = ?", (item_id,))
                if old:
                    tariff_audit.record_deleted(db, "consumable", item_id, old["name"], {"price": old["default_price"]})
                db.commit()
                flash("مصرفی با موفقیت حذف شد.", "success")

        return redirect(url_for("manager.consumables_tariffs"))

    # فیلتر بر اساس دسته
    category_filter = request.args.get("category", "")

    if category_filter:
        items = db.execute(
            "SELECT * FROM consumable_tariffs WHERE category = ? ORDER BY is_active DESC, name", (category_filter,)
        ).fetchall()
    else:
        items = db.execute("SELECT * FROM consumable_tariffs ORDER BY is_active DESC, category, name").fetchall()

    drugs = [i for i in items if i["category"] == "drug"]
    supplies = [i for i in items if i["category"] == "supply"]

    return render_template(
        "manager/tariffs_consumables.html", items=items, drugs=drugs, supplies=supplies, category_filter=category_filter
    )
