from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class User:
    id: int
    username: str
    role: str
    password_hash: str
    created_at: Optional[datetime] = None

    @property
    def is_admin(self):
        return self.role == 'admin'
