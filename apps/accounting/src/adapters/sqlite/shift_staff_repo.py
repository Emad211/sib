from typing import Optional, Dict
from datetime import datetime

from src.adapters.sqlite.core import get_db
from src.common.utils import get_current_shift_name


class ShiftStaffRepository:
    """Repository for managing shift-level doctor/nurse assignment (کادر درمان شیفت)."""

    def _current_shift(self) -> str:
        return get_current_shift_name()
    
    def _get_work_date_for_shift(self, shift: str = None) -> str:
        """Return work_date for shift_staff.

        With manual shift switching, work_date is not derived from clock time.
        We use the centralized utility which prefers `g.user_shift_status['work_date']`.
        """
        from src.common.utils import get_work_date_for_datetime
        return get_work_date_for_datetime()

    def set_shift_staff(self, work_date: Optional[str], shift: Optional[str], doctor_id: Optional[int], nurse_id: Optional[int]) -> None:
        """Upsert staff for a given date + shift. If work_date/shift not given, use today/current shift."""
        db = get_db()
        if not shift:
            shift = self._current_shift()
        if not work_date:
            work_date = self._get_work_date_for_shift(shift)

        # Simple upsert table stored in auxiliary table 'shift_staff'
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_staff (
                work_date TEXT NOT NULL,
                shift TEXT NOT NULL,
                doctor_id INTEGER,
                nurse_id INTEGER,
                PRIMARY KEY (work_date, shift)
            )
            """
        )

        db.execute(
            """
            INSERT INTO shift_staff (work_date, shift, doctor_id, nurse_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(work_date, shift) DO UPDATE SET
                doctor_id = excluded.doctor_id,
                nurse_id = excluded.nurse_id
            """,
            (work_date, shift, doctor_id, nurse_id),
        )
        db.commit()

    def get_shift_staff(self, work_date: Optional[str] = None, shift: Optional[str] = None) -> Optional[Dict]:
        """Return staff assignment for date+shift, or None."""
        db = get_db()
        if not shift:
            shift = self._current_shift()
        if not work_date:
            work_date = self._get_work_date_for_shift(shift)

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_staff (
                work_date TEXT NOT NULL,
                shift TEXT NOT NULL,
                doctor_id INTEGER,
                nurse_id INTEGER,
                PRIMARY KEY (work_date, shift)
            )
            """
        )

        row = db.execute(
            "SELECT work_date, shift, doctor_id, nurse_id FROM shift_staff WHERE work_date = ? AND shift = ?",
            (work_date, shift),
        ).fetchone()
        return dict(row) if row else None
