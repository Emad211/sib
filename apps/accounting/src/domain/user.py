from dataclasses import dataclass
from datetime import datetime


@dataclass
class User:
    id: int
    username: str
    role: str
    password_hash: str
    created_at: datetime | None = None

    @property
    def is_admin(self):
        return self.role == "admin"
