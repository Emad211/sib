import sys
import os
from pathlib import Path

# Add webapp to path
current_dir = Path(__file__).parent.parent
sys.path.append(str(current_dir))

from src.app import create_app
from src.adapters.sqlite.core import get_db
from src.services.auth_service import AuthService

def seed():
    app = create_app()
    with app.app_context():
        db = get_db()
        auth = AuthService()
        
        # 1. Create Users
        print("Creating users...")
        # register_user(username, password, role)
        auth.register_user("admin", "admin123", "admin")
        auth.register_user("reception1", "rec123", "reception")
        
        # 2. Medical Staff
        print("Adding medical staff...")
        db.execute("INSERT OR IGNORE INTO medical_staff (full_name, staff_type) VALUES (?, ?)", ("دکتر علوی", "doctor"))
        db.execute("INSERT OR IGNORE INTO medical_staff (full_name, staff_type) VALUES (?, ?)", ("دکتر رضایی", "doctor"))
        db.execute("INSERT OR IGNORE INTO medical_staff (full_name, staff_type) VALUES (?, ?)", ("خانم محمدی", "nurse"))
        db.execute("INSERT OR IGNORE INTO medical_staff (full_name, staff_type) VALUES (?, ?)", ("آقای کریمی", "nurse"))
        
        # 3. Visit Tariffs
        print("Adding visit tariffs...")
        # insurance_type, tariff_price, nursing_tariff, nursing_covers, is_active, is_supplementary, is_base_tariff
        db.execute("""
            INSERT OR IGNORE INTO visit_tariffs 
            (insurance_type, tariff_price, nursing_tariff, nursing_covers, is_active, is_base_tariff) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("آزاد", 150000, 0, 0, 1, 1))
        
        db.execute("""
            INSERT OR IGNORE INTO visit_tariffs 
            (insurance_type, tariff_price, nursing_tariff, nursing_covers, is_active, is_base_tariff) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("تامین اجتماعی", 120000, 0, 1, 1, 0))
        
        db.execute("""
            INSERT OR IGNORE INTO visit_tariffs 
            (insurance_type, tariff_price, nursing_tariff, nursing_covers, is_active, is_base_tariff) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("سلامت", 110000, 0, 1, 1, 0))

        # 4. Nursing Services
        print("Adding nursing services...")
        db.execute("INSERT OR IGNORE INTO nursing_services (service_name, unit_price) VALUES (?, ?)", ("تزریق عضلانی", 25000))
        db.execute("INSERT OR IGNORE INTO nursing_services (service_name, unit_price) VALUES (?, ?)", ("تزریق وریدی", 35000))
        db.execute("INSERT OR IGNORE INTO nursing_services (service_name, unit_price) VALUES (?, ?)", ("سرم تراپی", 85000))
        db.execute("INSERT OR IGNORE INTO nursing_services (service_name, unit_price) VALUES (?, ?)", ("پانسمان ساده", 45000))

        # 5. Procedure Tariffs
        print("Adding procedure services...")
        db.execute("INSERT OR IGNORE INTO procedure_tariffs (name, unit_price) VALUES (?, ?)", ("نوار قلب", 120000))
        db.execute("INSERT OR IGNORE INTO procedure_tariffs (name, unit_price) VALUES (?, ?)", ("بخیه", 250000))
        db.execute("INSERT OR IGNORE INTO procedure_tariffs (name, unit_price) VALUES (?, ?)", ("شستشوی گوش", 150000))

        # 6. Consumable Tariffs
        print("Adding consumable tariffs...")
        db.execute("INSERT OR IGNORE INTO consumable_tariffs (name, default_price, category) VALUES (?, ?, ?)", ("سرنگ 5 سی سی", 5000, "supply"))
        db.execute("INSERT OR IGNORE INTO consumable_tariffs (name, default_price, category) VALUES (?, ?, ?)", ("آنژیوکت آبی", 45000, "supply"))
        db.execute("INSERT OR IGNORE INTO consumable_tariffs (name, default_price, category) VALUES (?, ?, ?)", ("سرم نرمال سالین", 65000, "drug"))

        db.commit()
        print("Seeding completed successfully.")

if __name__ == "__main__":
    seed()
