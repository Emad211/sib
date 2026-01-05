from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class Patient:
    id: int
    name: str
    family_name: str
    national_id: Optional[str] = None
    phone_number: Optional[str] = None
    birthdate: Optional[str] = None
    gender: Optional[str] = None
    insurance_type: Optional[str] = None
    insurance_expiry: Optional[str] = None
    address: Optional[str] = None
    is_foreign: bool = False
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    @property
    def full_name(self):
        return f"{self.name} {self.family_name}"
