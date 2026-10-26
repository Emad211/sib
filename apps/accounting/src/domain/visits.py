from dataclasses import dataclass
from datetime import datetime


@dataclass
class Visit:
    id: int
    patient_id: int
    doctor_name: str | None
    visit_date: datetime
    shift: str | None = None
    insurance_type: str | None = None
    supplementary_insurance: str | None = None
    status: str = "pending"
    payment_status: str = "unpaid"
    total_amount: int = 0
    reception_user: str | None = None
    notes: str | None = None
    invoice_id: int | None = None  # binding to invoice (fix for add_visit)
    doctor_id: int | None = None  # پزشک مسئول
    nurse_id: int | None = None  # پرستار مسئول

    # Joined fields (optional)
    patient_name: str | None = None
