"""Per-person performance over a date range (FB-26). Closed invoices only; consumables are shown, never revenue."""


def _rows(db, sql, *args):
    return [dict(r) for r in db.execute(sql, args)]


def doctors(db, d1: str, d2: str) -> list[dict]:
    """Visits, procedures and injections per doctor."""
    out = {}

    def add(sql, key):
        for r in _rows(db, sql, d1, d2):
            out.setdefault(r["id"], {"id": r["id"], "name": r["name"], "visits": 0, "procedures": 0, "injections": 0})[
                key
            ] = r["n"]

    add(
        """SELECT s.id, s.full_name AS name, COUNT(*) AS n FROM visits v JOIN invoices i ON i.id = v.invoice_id AND i.status='closed'
           JOIN medical_staff s ON s.id = v.doctor_id WHERE v.work_date BETWEEN ? AND ? GROUP BY s.id""",
        "visits",
    )
    add(
        """SELECT s.id, s.full_name AS name, COUNT(*) AS n FROM procedures p JOIN invoices i ON i.id = p.invoice_id AND i.status='closed'
           JOIN medical_staff s ON s.id = p.doctor_id WHERE p.work_date BETWEEN ? AND ? GROUP BY s.id""",
        "procedures",
    )
    add(
        """SELECT s.id, s.full_name AS name, COUNT(*) AS n FROM injections j JOIN invoices i ON i.id = j.invoice_id AND i.status='closed'
           JOIN medical_staff s ON s.id = j.doctor_id WHERE j.work_date BETWEEN ? AND ? GROUP BY s.id""",
        "injections",
    )
    return sorted(out.values(), key=lambda r: -(r["visits"] + r["procedures"] + r["injections"]))


def nurses(db, d1: str, d2: str) -> list[dict]:
    """Nursing and procedure amounts per nurse (Toman)."""
    out = {}
    for key, sql in (
        (
            "nursing",
            """SELECT s.id, s.full_name AS name, SUM(j.total_price) AS n FROM injections j
               JOIN invoices i ON i.id = j.invoice_id AND i.status='closed' JOIN medical_staff s ON s.id = j.nurse_id
               WHERE j.work_date BETWEEN ? AND ? GROUP BY s.id""",
        ),
        (
            "procedures",
            """SELECT s.id, s.full_name AS name, SUM(p.price) AS n FROM procedures p
               JOIN invoices i ON i.id = p.invoice_id AND i.status='closed' JOIN medical_staff s ON s.id = p.nurse_id
               WHERE p.work_date BETWEEN ? AND ? GROUP BY s.id""",
        ),
    ):
        for r in _rows(db, sql, d1, d2):
            out.setdefault(r["id"], {"id": r["id"], "name": r["name"], "nursing": 0, "procedures": 0})[key] = r["n"] or 0
    for r in out.values():
        r["total"] = r["nursing"] + r["procedures"]
    return sorted(out.values(), key=lambda r: -r["total"])


def receptionists(db, d1: str, d2: str) -> list[dict]:
    """Per receptionist (who closed the invoice): visit count and amounts by kind."""
    out = {}

    def row(user, name):
        return out.setdefault(
            user,
            {"user": user, "name": name or user, "visits": 0, "nursing": 0, "procedures": 0, "consumables": 0, "drugs": 0},
        )

    for r in _rows(
        db,
        """SELECT i.closed_by AS u, COALESCE(i.closed_by_name, i.closed_by) AS name, COUNT(v.id) AS n FROM visits v
           JOIN invoices i ON i.id = v.invoice_id AND i.status='closed' WHERE v.work_date BETWEEN ? AND ? GROUP BY i.closed_by""",
        d1,
        d2,
    ):
        row(r["u"], r["name"])["visits"] = r["n"]
    for key, sql in (
        (
            "nursing",
            """SELECT i.closed_by AS u, COALESCE(i.closed_by_name, i.closed_by) AS name, SUM(j.total_price) AS n FROM injections j
               JOIN invoices i ON i.id = j.invoice_id AND i.status='closed' WHERE j.work_date BETWEEN ? AND ? GROUP BY i.closed_by""",
        ),
        (
            "procedures",
            """SELECT i.closed_by AS u, COALESCE(i.closed_by_name, i.closed_by) AS name, SUM(p.price) AS n FROM procedures p
               JOIN invoices i ON i.id = p.invoice_id AND i.status='closed' WHERE p.work_date BETWEEN ? AND ? GROUP BY i.closed_by""",
        ),
        (
            "consumables",
            """SELECT i.closed_by AS u, COALESCE(i.closed_by_name, i.closed_by) AS name, SUM(c.total_cost) AS n FROM consumables_ledger c
               JOIN invoices i ON i.id = c.invoice_id AND i.status='closed'
               WHERE c.work_date BETWEEN ? AND ? AND COALESCE(c.category,'') != 'drug' GROUP BY i.closed_by""",
        ),
        (
            "drugs",
            """SELECT i.closed_by AS u, COALESCE(i.closed_by_name, i.closed_by) AS name, SUM(c.total_cost) AS n FROM consumables_ledger c
               JOIN invoices i ON i.id = c.invoice_id AND i.status='closed'
               WHERE c.work_date BETWEEN ? AND ? AND c.category = 'drug' GROUP BY i.closed_by""",
        ),
    ):
        for r in _rows(db, sql, d1, d2):
            row(r["u"], r["name"])[key] = r["n"] or 0
    for r in out.values():
        r["total"] = r["nursing"] + r["procedures"] + r["consumables"] + r["drugs"]
    return sorted(out.values(), key=lambda r: -r["total"])
