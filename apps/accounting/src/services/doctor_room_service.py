from __future__ import annotations

from typing import Any, Optional

from src.adapters.sqlite.doctor_room_repo import DoctorRoomRepository


class DoctorRoomService:
    """Business rules for selecting the current patient in the doctor's room."""

    def __init__(self, repo: DoctorRoomRepository | None = None):
        self.repo = repo or DoctorRoomRepository()

    def search_open_invoices(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.repo.find_open_invoices(query=query, limit=limit)

    def set_current_invoice(self, doctor_staff_id: int, invoice_id: int, set_by_user_id: int) -> None:
        details = self.repo.get_open_invoice_details(invoice_id)
        if not details:
            raise ValueError("فاکتور یافت نشد یا بسته شده است")

        self.repo.upsert_room_state(
            doctor_staff_id=doctor_staff_id,
            active_invoice_id=invoice_id,
            set_by_user_id=set_by_user_id,
        )

    def clear_current_invoice(self, doctor_staff_id: int, set_by_user_id: int) -> None:
        self.repo.upsert_room_state(
            doctor_staff_id=doctor_staff_id,
            active_invoice_id=None,
            set_by_user_id=set_by_user_id,
        )

    def get_current(self, doctor_staff_id: int) -> Optional[dict[str, Any]]:
        state = self.repo.get_room_state(doctor_staff_id)
        if not state:
            return None

        active_invoice_id = state.get("active_invoice_id")
        if not active_invoice_id:
            return {"state": state, "current": None}

        current = self.repo.get_open_invoice_details(int(active_invoice_id))
        if not current:
            # invoice might have been closed; keep state but return empty current
            return {"state": state, "current": None}

        patient_id = int(current["patient_id"])
        history = self.repo.get_patient_recent_visits(patient_id, limit=10)

        return {
            "state": state,
            "current": current,
            "patient_recent_visits": history,
        }
