-- Users table for authentication
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash BLOB NOT NULL,
    role TEXT NOT NULL DEFAULT 'reception', -- 'admin', 'manager', 'reception'
    full_name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER DEFAULT 0,
    locked_until TEXT,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Medical Staff (doctors and nurses)
CREATE TABLE IF NOT EXISTS medical_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    staff_type TEXT NOT NULL, -- 'doctor', 'nurse'
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Patients table
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    family_name TEXT NOT NULL,
    full_name TEXT GENERATED ALWAYS AS (name || ' ' || family_name) VIRTUAL,
    national_id TEXT UNIQUE,
    phone_number TEXT,
    birthdate TEXT,
    gender TEXT,
    insurance_type TEXT,
    insurance_expiry TEXT,
    address TEXT,
    is_foreign INTEGER DEFAULT 0,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    updated_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Visit Tariffs (pricing for different insurance types)
CREATE TABLE IF NOT EXISTS visit_tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insurance_type TEXT UNIQUE NOT NULL,
    tariff_price REAL NOT NULL DEFAULT 0,
    nursing_tariff REAL NOT NULL DEFAULT 0, -- تعرفه خدمات پرستاری برای این بیمه (0 = رایگان)
    nursing_covers INTEGER DEFAULT 0, -- explicit flag: whether nursing services are covered (1=true,0=false)
    is_active INTEGER DEFAULT 1,
    is_supplementary INTEGER DEFAULT 0,
    is_base_tariff INTEGER DEFAULT 0, -- آیا این تعرفه پایه/آزاد است؟
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    updated_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Invoices (فاکتورها - main container for patient billing)
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER,
    nurse_id INTEGER,
    status TEXT DEFAULT 'open', -- 'open', 'closed'
    insurance_type TEXT,
    supplementary_insurance TEXT,
    total_amount REAL DEFAULT 0,
    work_date TEXT, -- YYYY-MM-DD (manual work date)
    shift TEXT, -- 'morning', 'evening', 'night'
    opened_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    closed_at TIMESTAMP,
    opened_by TEXT,
    opened_by_name TEXT,
    closed_by TEXT,
    closed_by_name TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (doctor_id) REFERENCES medical_staff (id),
    FOREIGN KEY (nurse_id) REFERENCES medical_staff (id)
);

-- Visits table (linked to invoices)
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_name TEXT,
    visit_date TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    shift TEXT,
    work_date TEXT, -- YYYY-MM-DD (manual work date)
    insurance_type TEXT,
    supplementary_insurance TEXT,
    status TEXT DEFAULT 'pending',
    price REAL DEFAULT 0,
    payment_status TEXT DEFAULT 'unpaid', -- 'unpaid','paid'
    reception_user TEXT,
    notes TEXT,
    invoice_id INTEGER,
    doctor_id INTEGER,
    nurse_id INTEGER,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id),
    FOREIGN KEY (doctor_id) REFERENCES medical_staff (id),
    FOREIGN KEY (nurse_id) REFERENCES medical_staff (id)
);

-- Services/Items catalog (Tariffs)
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_price INTEGER NOT NULL,
    service_type TEXT NOT NULL -- 'visit', 'procedure', 'consumable'
);

-- Items included in a visit
CREATE TABLE IF NOT EXISTS visit_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    quantity INTEGER DEFAULT 1,
    price_at_time INTEGER NOT NULL,
    FOREIGN KEY (visit_id) REFERENCES visits (id),
    FOREIGN KEY (service_id) REFERENCES services (id)
);

-- Nursing services catalog (خدمات پرستاری)
CREATE TABLE IF NOT EXISTS nursing_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL UNIQUE,
    unit_price REAL NOT NULL DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Injection types (انواع تزریق) optional catalog
CREATE TABLE IF NOT EXISTS injection_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_name TEXT NOT NULL UNIQUE,
    base_price REAL NOT NULL DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Injections table (پرستاری / تزریقات)
CREATE TABLE IF NOT EXISTS injections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    injection_type TEXT NOT NULL,
    service_id INTEGER, -- reference to nursing_services.id
    injection_date TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    shift TEXT,
    work_date TEXT, -- YYYY-MM-DD (manual work date)
    count INTEGER DEFAULT 1,
    unit_price REAL DEFAULT 0,
    total_price REAL DEFAULT 0,
    patient_amount REAL DEFAULT NULL, -- amount to be paid by patient (if covered, 0)
    insurance_amount REAL DEFAULT NULL, -- amount to be paid by insurance
    covered_by_insurance INTEGER DEFAULT 0,
    reception_user TEXT,
    notes TEXT,
    invoice_id INTEGER,
    doctor_id INTEGER,
    nurse_id INTEGER,
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (service_id) REFERENCES nursing_services (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id),
    FOREIGN KEY (doctor_id) REFERENCES medical_staff (id),
    FOREIGN KEY (nurse_id) REFERENCES medical_staff (id)
);

-- Procedures table (کارهای عملی / پانسمان و غیره)
CREATE TABLE IF NOT EXISTS procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    procedure_type TEXT NOT NULL,
    procedure_date TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    shift TEXT,
    work_date TEXT, -- YYYY-MM-DD (manual work date)
    price REAL DEFAULT 0,
    reception_user TEXT,
    notes TEXT,
    invoice_id INTEGER,
    performer_type TEXT, -- 'doctor', 'nurse'
    performer_id INTEGER,
    doctor_id INTEGER,
    nurse_id INTEGER,
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id),
    FOREIGN KEY (doctor_id) REFERENCES medical_staff (id),
    FOREIGN KEY (nurse_id) REFERENCES medical_staff (id)
);

-- Consumables ledger (دفتر مصرفی‌ها) minimal for factoring
CREATE TABLE IF NOT EXISTS consumables_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER,
    item_name TEXT NOT NULL,
    category TEXT, -- 'drug' or 'supply'
    quantity REAL DEFAULT 1,
    unit_price REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    patient_provided INTEGER DEFAULT 0, -- if patient brought item, total_cost may be zero
    is_exception INTEGER DEFAULT 0, -- special flag if item is an 'undefined/exception' patient-brought drug
    usage_date TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    shift TEXT,
    work_date TEXT, -- YYYY-MM-DD (manual work date)
    reception_user TEXT,
    notes TEXT,
    invoice_id INTEGER,
    doctor_id INTEGER,
    nurse_id INTEGER,
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id),
    FOREIGN KEY (doctor_id) REFERENCES medical_staff (id),
    FOREIGN KEY (nurse_id) REFERENCES medical_staff (id)
);

-- Tariffs for consumables & drugs (unified)
CREATE TABLE IF NOT EXISTS consumable_tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    default_price REAL NOT NULL DEFAULT 0,
    category TEXT NOT NULL, -- 'supply' or 'drug'
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Insurance-specific exclusions for nursing services
CREATE TABLE IF NOT EXISTS insurance_nursing_exclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insurance_type TEXT NOT NULL,
    nursing_service_id INTEGER NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    FOREIGN KEY (nursing_service_id) REFERENCES nursing_services (id)
);

-- Procedure tariffs catalog (تعرفه کارهای عملی)
CREATE TABLE IF NOT EXISTS procedure_tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    unit_price REAL NOT NULL DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
);

-- Invoice item payments (پرداخت وضعیت هر آیتم)
CREATE TABLE IF NOT EXISTS invoice_item_payments (
    invoice_id INTEGER NOT NULL,
    item_type TEXT NOT NULL, -- 'visit','injection','procedure','consumable'
    item_id INTEGER NOT NULL,
    payment_type TEXT, -- 'cash','card','insurance'
    is_paid INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    PRIMARY KEY (invoice_id, item_type, item_id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id)
);

-- Payroll settings (تنظیمات حقوق کادر درمان)
CREATE TABLE IF NOT EXISTS payroll_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL UNIQUE,
    base_morning REAL DEFAULT 0,
    base_evening REAL DEFAULT 0,
    base_night REAL DEFAULT 0,
    visit_fee REAL DEFAULT 0,
    injection_percent REAL DEFAULT 0,
    procedure_percent REAL DEFAULT 0,
    tax_percent REAL DEFAULT 0,
    nursing_percent REAL DEFAULT 0,
    nurse_procedure_percent REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    updated_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    FOREIGN KEY (staff_id) REFERENCES medical_staff (id)
);

-- Activity logs (لاگ فعالیت‌های کاربران)
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action_type TEXT NOT NULL,
    action_category TEXT NOT NULL,
    description TEXT,
    target_type TEXT,
    target_id INTEGER,
    target_name TEXT,
    invoice_id INTEGER,
    patient_id INTEGER,
    patient_name TEXT,
    amount REAL DEFAULT 0,
    old_value TEXT,
    new_value TEXT,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id),
    FOREIGN KEY (patient_id) REFERENCES patients (id)
);

-- User active shift (شیفت فعال کاربر)
-- Tracks which shift a user is currently working in
-- This allows manual shift change independent of clock time
CREATE TABLE IF NOT EXISTS user_active_shift (
    user_id INTEGER PRIMARY KEY,
    active_shift TEXT NOT NULL,  -- 'morning', 'evening', 'night'
    work_date TEXT NOT NULL,     -- YYYY-MM-DD
    shift_started_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    FOREIGN KEY (user_id) REFERENCES users (id)
);
