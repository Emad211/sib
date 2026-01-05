from datetime import datetime, timedelta
from typing import Optional, List

import bcrypt
from werkzeug.security import check_password_hash

from src.adapters.sqlite.auth_repo import AuthRepository
from src.common.utils import iran_now


class AuthService:
    """High-level auth logic mirroring the desktop application's behavior."""

    def __init__(self, repo: AuthRepository | None = None):
        self.repo = repo or AuthRepository()

    # ---- Internal helpers (lockout logic) ----
    def _is_locked(self, user_row: dict) -> bool:
        locked_until = user_row.get("locked_until")
        if not locked_until:
            return False
        try:
            lu = datetime.fromisoformat(str(locked_until))
            return iran_now() < lu
        except Exception:
            return False

    def _increment_failed(self, user_row: dict):
        new_val = (user_row.get("failed_attempts") or 0) + 1
        lock_until: Optional[str] = None
        if new_val >= 5:
            lock_until = (iran_now() + timedelta(minutes=15)).isoformat(timespec="seconds")
            new_val = 0
        self.repo.update_failed_attempts(user_row["id"], new_val, lock_until)

    def _reset_failed(self, user_row: dict):
        self.repo.reset_failed_attempts(user_row["id"])

    # ---- Core login attempt ----
    def _attempt_login(self, username: str, password: str, expected_role: str) -> Optional[dict]:
        username = username.strip()
        user = self.repo.get_raw_by_username(username)

        if not user:
            # No user, nothing to increment; desktop app logged security event here
            return None

        user_dict = dict(user)

        # Role check
        if user_dict.get("role") != expected_role:
            return None

        # Lockout check before password verify
        if self._is_locked(user_dict):
            return None

        stored_hash = user_dict.get("password_hash")
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode("utf-8")

        password_ok = False
        migrated = False
        try:
            # If stored value is bytes and looks like bcrypt ($2...), use bcrypt
            if stored_hash and isinstance(stored_hash, (bytes, bytearray)) and stored_hash.startswith(b"$2"):
                password_ok = bcrypt.checkpw(password.encode("utf-8"), stored_hash)
            else:
                # Try werkzeug's generic check which supports pbkdf2/scrypt/others
                stored_str = stored_hash.decode("utf-8") if isinstance(stored_hash, (bytes, bytearray)) else stored_hash
                if stored_str:
                    try:
                        password_ok = check_password_hash(stored_str, password)
                        # If legacy check succeeded, migrate to bcrypt for uniformity
                        if password_ok:
                            salt = bcrypt.gensalt()
                            new_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
                            # Update DB using repo method
                            try:
                                self.repo.update_user_password(user_dict['id'], new_hash)
                                migrated = True
                            except Exception:
                                pass
                    except Exception:
                        # check_password_hash may raise for unexpected formats
                        password_ok = False
                else:
                    password_ok = False
        except Exception:
            password_ok = False

        if not password_ok:
            # Wrong password or unsupported hash: increment failed attempts (if not already locked)
            if not self._is_locked(user_dict):
                self._increment_failed(user_dict)
            return None

        # Success path
        self._reset_failed(user_dict)
        self.repo.set_last_login(user_dict["id"])
        return user_dict

    # ---- Public API (manager) ----
    def validate_manager(self, username: str, password: str) -> Optional[dict]:
        return self._attempt_login(username, password, "manager")

    # ---- Reception ----
    def get_reception_users(self) -> List[str]:
        return self.repo.get_reception_usernames()

    def validate_reception(self, username: str, password: str) -> Optional[dict]:
        return self._attempt_login(username, password, "reception")

    # ---- User creation for CLI / setup ----
    def register_user(self, username: str, password: str, role: str = "reception", full_name: str | None = None) -> bool:
        # Reuse low-level repo + bcrypt like desktop app
        if self.repo.get_raw_by_username(username):
            return False

        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password.encode("utf-8"), salt)
        return self.repo.create_user(username, password_hash, role, full_name)
