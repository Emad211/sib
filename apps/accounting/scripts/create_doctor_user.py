"""
Script to create a sample doctor user for testing
Run this to create a doctor account
"""
import sys
import os
import sqlite3
import bcrypt

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config.settings import Config

def create_doctor_user():
    """Create a sample doctor user with credentials: doctor / doctor"""
    
    db_path = Config.DATABASE_PATH
    
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        print("Please run the application first to create the database.")
        return
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Check if doctor user already exists
        existing = conn.execute("SELECT id FROM users WHERE username = 'doctor'").fetchone()
        if existing:
            print("✓ Doctor user already exists (username: doctor)")
            return
        
        # Create password hash
        password = 'doctor'
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
        
        # Insert doctor user
        conn.execute("""
            INSERT INTO users (username, password_hash, role, full_name, is_active)
            VALUES (?, ?, ?, ?, ?)
        """, ('doctor', password_hash, 'doctor', 'دکتر علی محمدی', 1))
        
        conn.commit()
        
        print("✓ Doctor user created successfully!")
        print()
        print("Login credentials:")
        print("  Username: doctor")
        print("  Password: doctor")
        print("  Role: پزشک")
        print()
        print("You can now login through the web interface.")
        
    except Exception as e:
        print(f"Error creating doctor user: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == '__main__':
    create_doctor_user()
