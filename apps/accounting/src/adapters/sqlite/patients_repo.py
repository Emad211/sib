from src.adapters.sqlite.core import get_db
from src.domain.patients import Patient

class PatientRepository:
    def get_by_national_id(self, national_id: str) -> Patient:
        db = get_db()
        row = db.execute(
            'SELECT * FROM patients WHERE national_id = ?', (national_id,)
        ).fetchone()
        return self._map_row(row) if row else None

    def get_by_id(self, patient_id: int) -> Patient:
        db = get_db()
        row = db.execute(
            'SELECT * FROM patients WHERE id = ?', (patient_id,)
        ).fetchone()
        return self._map_row(row) if row else None
    
    def get_by_name_and_phone(self, name: str, family_name: str, phone: str) -> Patient:
        """Get patient by name and phone number (for fuzzy matching)."""
        db = get_db()
        row = db.execute(
            'SELECT * FROM patients WHERE name = ? AND family_name = ? AND phone_number = ?',
            (name, family_name, phone)
        ).fetchone()
        return self._map_row(row) if row else None

    def search_by_name(self, query: str):
        db = get_db()
        rows = db.execute(
            'SELECT * FROM patients WHERE name LIKE ? OR family_name LIKE ?', 
            (f'%{query}%', f'%{query}%')
        ).fetchall()
        return [self._map_row(row) for row in rows]

    def create(self, patient: Patient) -> int:
        db = get_db()
        cursor = db.execute(
            '''INSERT INTO patients (
                name, family_name, national_id, phone_number, birthdate, 
                gender, insurance_type, insurance_expiry, address, is_foreign, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (patient.name, patient.family_name, patient.national_id, patient.phone_number,
             patient.birthdate, patient.gender, patient.insurance_type, 
             patient.insurance_expiry, patient.address, patient.is_foreign, 
             getattr(patient, 'created_by', 'system'))
        )
        db.commit()
        return cursor.lastrowid

    def update(self, patient: Patient):
        db = get_db()
        db.execute(
            '''UPDATE patients SET 
                name=?, family_name=?, phone_number=?, birthdate=?, 
                gender=?, insurance_type=?, insurance_expiry=?, address=?, is_foreign=?,
                updated_at=datetime('now', '+3 hours', '+30 minutes')
               WHERE id=?''',
            (patient.name, patient.family_name, patient.phone_number,
             patient.birthdate, patient.gender, patient.insurance_type, 
             patient.insurance_expiry, patient.address, patient.is_foreign, patient.id)
        )
        db.commit()

    def _map_row(self, row) -> Patient:
        return Patient(
            id=row['id'],
            name=row['name'],
            family_name=row['family_name'],
            national_id=row['national_id'],
            phone_number=row['phone_number'],
            birthdate=row['birthdate'],
            gender=row['gender'],
            insurance_type=row['insurance_type'],
            insurance_expiry=row['insurance_expiry'],
            address=row['address'],
            is_foreign=bool(row['is_foreign']),
            created_at=row['created_at']
        )
