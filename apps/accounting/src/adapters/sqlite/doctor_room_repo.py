from __future__ import annotations

from typing import Any, Optional

from src.adapters.sqlite.core import get_db


class DoctorRoomRepository:
    """DB operations for the doctor's room state and invoice search."""

    def get_room_state(self, doctor_staff_id: int) -> Optional[dict[str, Any]]:
        db = get_db()
        row = db.execute(
            "SELECT * FROM doctor_room_state WHERE doctor_staff_id = ?",
            (doctor_staff_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_room_state(self, doctor_staff_id: int, active_invoice_id: Optional[int], set_by_user_id: int) -> None:
        db = get_db()
        db.execute(
            """
            INSERT INTO doctor_room_state (doctor_staff_id, active_invoice_id, set_by_user_id, updated_at)
            VALUES (?, ?, ?, datetime('now', '+3 hours', '+30 minutes'))
            ON CONFLICT(doctor_staff_id)
            DO UPDATE SET
                active_invoice_id = excluded.active_invoice_id,
                set_by_user_id = excluded.set_by_user_id,
                updated_at = excluded.updated_at
            """,
            (doctor_staff_id, active_invoice_id, set_by_user_id),
        )
        db.commit()

    def find_open_invoices(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search open invoices by patient name or national_id."""
        db = get_db()

        q = (query or "").strip()
        if not q:
            return []

        like = f"%{q}%"

        rows = db.execute(
            """
            SELECT i.id AS invoice_id,
                   i.opened_at,
                   i.work_date,
                   i.shift,
                   i.opened_by,
                   COALESCE(i.opened_by_name, u_open.full_name, i.opened_by) AS opened_by_name,
                   p.id AS patient_id,
                   p.full_name AS patient_name,
                   p.national_id,
                   p.phone_number,
                   i.insurance_type,
                   i.supplementary_insurance
            FROM invoices i
            JOIN patients p ON p.id = i.patient_id
            LEFT JOIN users u_open ON u_open.username = i.opened_by
            WHERE i.status = 'open'
              AND (
                    p.full_name LIKE ?
                 OR COALESCE(p.national_id, '') LIKE ?
              )
            ORDER BY i.opened_at DESC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()

        return [dict(r) for r in rows]

    def get_open_invoice_details(self, invoice_id: int) -> Optional[dict[str, Any]]:
        """Get invoice + patient info for an open invoice. Returns None if invoice missing or closed."""
        db = get_db()
        row = db.execute(
            """
            SELECT i.id AS invoice_id,
                   i.opened_at,
                   i.work_date,
                   i.shift,
                   i.insurance_type,
                   i.supplementary_insurance,
                   i.opened_by,
                   COALESCE(i.opened_by_name, u_open.full_name, i.opened_by) AS opened_by_name,
                   p.id AS patient_id,
                   p.full_name AS patient_name,
                   p.national_id,
                   p.phone_number,
                   p.birthdate,
                   p.gender,
                   p.address,
                   p.is_foreign
            FROM invoices i
            JOIN patients p ON p.id = i.patient_id
            LEFT JOIN users u_open ON u_open.username = i.opened_by
            WHERE i.id = ? AND i.status = 'open'
            """,
            (invoice_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_patient_recent_visits(self, patient_id: int, limit: int = 10) -> list[dict[str, Any]]:
        db = get_db()
        rows = db.execute(
            """
            SELECT v.id,
                   v.visit_date,
                   v.work_date,
                   v.shift,
                   v.price,
                   COALESCE(ms.full_name, v.doctor_name) AS doctor_name,
                   v.notes,
                   v.invoice_id
            FROM visits v
            LEFT JOIN medical_staff ms ON ms.id = v.doctor_id
            WHERE v.patient_id = ?
            ORDER BY v.visit_date DESC
            LIMIT ?
            """,
            (patient_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
