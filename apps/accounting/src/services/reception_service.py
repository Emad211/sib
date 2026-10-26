from src.domain.patients import Patient
from src.domain.visits import Visit
from src.adapters.sqlite.patients_repo import PatientRepository
from src.adapters.sqlite.visits_repo import VisitRepository
from src.adapters.sqlite.tariffs_repo import TariffRepository
from src.common.utils import get_current_shift_window, iran_now

class ReceptionService:
    def __init__(self, patient_repo=None, visit_repo=None, tariff_repo=None):
        self.patient_repo = patient_repo or PatientRepository()
        self.visit_repo = visit_repo or VisitRepository()
        self.tariff_repo = tariff_repo or TariffRepository()

    def get_patient_by_national_id(self, national_id):
        return self.patient_repo.get_by_national_id(national_id)

    def register_patient(self, data):
        # Check if exists
        if data.get('national_id'):
            existing = self.patient_repo.get_by_national_id(data['national_id'])
            if existing:
                # Update? For now just return existing
                return existing

        patient = Patient(
            id=None,
            name=data['name'],
            family_name=data['family_name'],
            national_id=data.get('national_id'),
            phone_number=data.get('phone_number'),
            birthdate=data.get('birthdate'),
            gender=data.get('gender'),
            insurance_type=data.get('insurance_type'),
            is_foreign=data.get('is_foreign', False)
        )
        patient_id = self.patient_repo.create(patient)
        patient.id = patient_id
        return patient

    def create_visit(self, patient_id, visit_data, user_name):
        # Calculate shift
        shift, _, _ = get_current_shift_window()
        
        # Calculate Price
        doctor_name = visit_data.get('doctor_name')
        insurance_type = visit_data.get('insurance_type')
        
        # Determine doctor type for tariff lookup
        doctor_type = 'متخصص' if 'متخصص' in str(doctor_name) else 'عمومی'
        price = self.tariff_repo.get_price('visit', insurance_type, doctor_type)
        
        visit = Visit(
            id=None,
            patient_id=patient_id,
            doctor_name=doctor_name,
            visit_date=iran_now(),
            shift=shift,
            insurance_type=insurance_type,
            supplementary_insurance=visit_data.get('supplementary_insurance'),
            total_amount=price,
            reception_user=user_name,
            notes=visit_data.get('notes')
        )
        return self.visit_repo.create(visit)

    def get_today_visits(self, work_date: str = None):
        return self.visit_repo.get_today_visits(work_date)
    
    def add_or_get_patient(self, name, family_name, national_id=None, phone=None, is_foreign=False, user="system"):
        """Add new patient or get existing one (exact logic from desktop app)."""
        # Check if patient exists by national_id
        if national_id:
            existing = self.patient_repo.get_by_national_id(national_id)
            if existing:
                # Update existing patient info
                existing.name = name
                existing.family_name = family_name
                existing.phone_number = phone
                existing.is_foreign = is_foreign
                self.patient_repo.update(existing)
                return existing.id
        
        # Check by name+family+phone if no national_id
        if not national_id and phone:
            existing = self.patient_repo.get_by_name_and_phone(name, family_name, phone)
            if existing:
                return existing.id
        
        # Create new patient
        patient = Patient(
            id=None,
            name=name,
            family_name=family_name,
            national_id=national_id,
            phone_number=phone,
            is_foreign=is_foreign,
            created_by=user
        )
        return self.patient_repo.create(patient)
    
    def add_visit(self, patient_id, insurance_type, supplementary_insurance=None,
                  reception_user="system", doctor_name=None, notes="", invoice_id=None,
                  doctor_id=None):
        """Add new visit with automatic price calculation (desktop logic) and optional binding to invoice.
        Note: Visits do not have nurse - nurse is only for injections/procedures/consumables."""
        # Calculate current shift
        shift, _, _ = get_current_shift_window()
        
        # Resolve price via tariff repository rule engine
        price = self.tariff_repo.resolve_visit_price(insurance_type, supplementary_insurance)
        
        visit = Visit(
            id=None,
            patient_id=patient_id,
            doctor_name=doctor_name,
            visit_date=iran_now(),
            shift=shift,
            insurance_type=insurance_type,
            supplementary_insurance=supplementary_insurance,
            total_amount=price,
            reception_user=reception_user,
            notes=notes,
            invoice_id=invoice_id,
            doctor_id=doctor_id,
            nurse_id=None,  # Visits don't have nurses
        )
        return self.visit_repo.create(visit)
    
    def get_active_visit_tariffs(self):
        """Get list of active visit tariffs for insurance selection."""
        return self.tariff_repo.get_active_visit_tariffs()

    def get_active_supplementary_insurances(self):
        """Return active supplementary insurance options for frontend dropdown."""
        return self.tariff_repo.get_active_supplementary_insurances()
    
    def create_or_open_invoice(self, name, family_name, phone, national_id, insurance_type, supplementary_insurance, opened_by, is_foreign=False, doctor_id=None, nurse_id=None):
        """Create or find patient, then open invoice (desktop-style logic, without shift staff binding)."""
        # Add or get patient
        patient_id = self.add_or_get_patient(
            name=name,
            family_name=family_name,
            national_id=national_id,
            phone=phone,
            is_foreign=is_foreign,
            user=opened_by
        )
        # Ensure patient's insurance info is up-to-date (legacy desktop parity)
        try:
            existing_patient = self.patient_repo.get_by_id(patient_id)
            if existing_patient and (existing_patient.insurance_type != insurance_type):
                existing_patient.insurance_type = insurance_type
                # We keep supplementary insurance only on invoice level for now, not patient record.
                self.patient_repo.update(existing_patient)
        except Exception:
            # Non-critical; continue opening invoice even if update fails
            pass
        
        # Open invoice with insurance info and staff
        from src.adapters.sqlite.invoices_repo import InvoiceRepository
        invoice_repo = InvoiceRepository()
        
        # Get current shift
        shift, _, _ = get_current_shift_window()
        
        # در نسخه دسکتاپ، پزشک و پرستار در سطح شیفت نگه‌داری می‌شوند نه روی خود فاکتور.
        invoice_id = invoice_repo.open_invoice(
            patient_id=patient_id,
            insurance_type=insurance_type,
            supplementary_insurance=supplementary_insurance,
            opened_by=opened_by,
            shift=shift
        )
        
        return invoice_id, patient_id
