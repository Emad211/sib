from dataclasses import dataclass
from datetime import datetime


@dataclass
class Patient:
    id: int
    name: str
    family_name: str
    national_id: str | None = None
    phone_number: str | None = None
    birthdate: str | None = None
    gender: str | None = None
    insurance_type: str | None = None
    insurance_expiry: str | None = None
    address: str | None = None
    is_foreign: bool = False
    created_by: str | None = None
    created_at: datetime | None = None

    @property
    def full_name(self):
        return f"{self.name} {self.family_name}"
