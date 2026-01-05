from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class Visit:
    id: int
    patient_id: int
    doctor_name: Optional[str]
    visit_date: datetime
    shift: Optional[str] = None
    insurance_type: Optional[str] = None
    supplementary_insurance: Optional[str] = None
    status: str = 'pending'
    payment_status: str = 'unpaid'
    total_amount: int = 0
    reception_user: Optional[str] = None
    notes: Optional[str] = None
    invoice_id: Optional[int] = None  # binding to invoice (fix for add_visit)
    doctor_id: Optional[int] = None   # پزشک مسئول
    nurse_id: Optional[int] = None    # پرستار مسئول
    
    # Joined fields (optional)
    patient_name: Optional[str] = None
