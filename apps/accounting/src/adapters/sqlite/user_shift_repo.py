"""Repository for managing user's active shift.

Shift switching is fully manual.

Legacy versions mixed manual shift selection with clock-based boundaries
to mark shifts as "overdue" and prompt the user.
All time-based rules have been removed.
"""
from typing import Optional, Dict
from datetime import datetime, timedelta

from src.common.utils import iran_now

from src.adapters.sqlite.core import get_db


class UserShiftRepository:
    """Repository for user active shift management."""

    def _ensure_table(self):
        """Create table if not exists."""
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_active_shift (
                user_id INTEGER PRIMARY KEY,
                active_shift TEXT NOT NULL,
                work_date TEXT NOT NULL,
                shift_started_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        db.commit()

    def get_user_active_shift(self, user_id: int) -> Optional[Dict]:
        """
        Get the user's current active shift info.
        Returns dict with: active_shift, work_date, shift_started_at
        Returns None if user has no active shift record.
        """
        self._ensure_table()
        db = get_db()
        row = db.execute(
            "SELECT * FROM user_active_shift WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_user_active_shift(self, user_id: int, shift: str, work_date: str) -> None:
        """
        Set/update the user's active shift.
        Called when user confirms shift change.
        """
        self._ensure_table()
        db = get_db()
        now = iran_now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute("""
            INSERT INTO user_active_shift (user_id, active_shift, work_date, shift_started_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                active_shift = excluded.active_shift,
                work_date = excluded.work_date,
                shift_started_at = excluded.shift_started_at
        """, (user_id, shift, work_date, now))
        db.commit()

    def mark_shift_overdue(self, user_id: int) -> None:
        """No-op: manual shifts do not expire automatically."""
        pass

    def update_last_prompt(self, user_id: int) -> None:
        """No-op: manual shifts do not prompt."""
        pass

    def clear_user_shift(self, user_id: int) -> None:
        """Remove user's active shift record (on logout)."""
        self._ensure_table()
        db = get_db()
        db.execute("DELETE FROM user_active_shift WHERE user_id = ?", (user_id,))
        db.commit()

    def get_effective_shift_for_user(self, user_id: int) -> tuple[str, str, bool, bool]:
        """
        Get the effective (manual) shift for a user.
        
        Returns:
            (shift_name, work_date, is_overdue, should_prompt)
            - shift_name: 'morning', 'evening', or 'night'
            - work_date: YYYY-MM-DD
            - is_overdue: always False
            - should_prompt: always False
        """
        self._ensure_table()

        now = iran_now()
        user_shift = self.get_user_active_shift(user_id)
        
        if not user_shift:
            # Default on first use.
            default_shift = 'morning'
            default_work_date = now.strftime('%Y-%m-%d')
            self.set_user_active_shift(user_id, default_shift, default_work_date)
            return (default_shift, default_work_date, False, False)

        return (user_shift['active_shift'], user_shift['work_date'], False, False)
