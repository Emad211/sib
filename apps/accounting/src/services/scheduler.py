"""
Automatic Backup Scheduler
Runs weekly backups of the database every Saturday at 3:00 AM
"""

import os
import shutil
import threading
import time
from pathlib import Path

from src.common.utils import iran_now


class BackupScheduler:
    """Background scheduler for automatic database backups"""
    
    def __init__(self, app=None):
        self.app = app
        self.running = False
        self.thread = None
        self.backup_interval_days = 7  # Weekly backup
        self.backup_hour = 3  # 3:00 AM
        self.backup_day = 5  # Saturday (0=Monday, 5=Saturday)
        
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        
        # استفاده مستقیم از مسیرهایی که در Config درست محاسبه شده‌اند
        self.db_path = Path(app.config['DATABASE_PATH'])
        self.backup_dir = Path(app.config['BACKUP_FOLDER'])
        
        # ایجاد پوشه backups در کنار exe (اگر وجود نداشته باشد)
        self.backup_dir.mkdir(exist_ok=True)
        
        # چاپ لاگ برای دیباگ (در exe دیده نمی‌شود چون noconsole است، ولی برای تست خوبه)
        print(f"[BackupScheduler] DB path: {self.db_path}")
        print(f"[BackupScheduler] Backup folder: {self.backup_dir}")
    
    def start(self):
        """Start the background scheduler"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        print(f"[BackupScheduler] Started - Weekly backup enabled (Saturdays at {self.backup_hour}:00)")
    
    def stop(self):
        """Stop the scheduler"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
    
    def _run_scheduler(self):
        """Main scheduler loop"""
        while self.running:
            try:
                now = iran_now()
                
                # Check if it's time for backup (Saturday at 3:00 AM)
                if now.weekday() == self.backup_day and now.hour == self.backup_hour:
                    # Check if we haven't already done a backup today
                    if self._should_backup():
                        self._create_backup()
                        # Sleep for 2 hours to avoid duplicate backups
                        time.sleep(7200)
                    else:
                        time.sleep(3600)  # Check again in 1 hour
                else:
                    # Sleep for 30 seconds between checks when idle
                    time.sleep(30)
                    
            except Exception as e:
                print(f"[BackupScheduler] Error: {e}")
                time.sleep(3600)  # Wait 1 hour on error
    
    def _should_backup(self):
        """Check if we should create a backup (no backup today yet)"""
        today = iran_now().strftime('%Y%m%d')
        
        for backup_file in self.backup_dir.glob('backup_auto_*.db'):
            if today in backup_file.name:
                return False
        
        return True
    
    def _create_backup(self):
        """Create an automatic backup"""
        if not self.db_path.exists():
            print(f"[BackupScheduler] Database not found: {self.db_path}")
            return False
        
        try:
            timestamp = iran_now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"backup_auto_{timestamp}.db"
            backup_path = self.backup_dir / backup_name
            
            shutil.copy2(self.db_path, backup_path)
            
            print(f"[BackupScheduler] Automatic backup created: {backup_name}")
            
            # Clean old automatic backups (keep last 4 weeks)
            self._cleanup_old_backups()
            
            return True
            
        except Exception as e:
            print(f"[BackupScheduler] Failed to create backup: {e}")
            return False

    def _cleanup_old_backups(self, keep_count=4):
        """Keep only the last N automatic backups"""
        try:
            auto_backups = sorted(
                self.backup_dir.glob('backup_auto_*.db'),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            
            # Remove old backups (keep last 4)
            for old_backup in auto_backups[keep_count:]:
                old_backup.unlink()
                print(f"[BackupScheduler] Removed old backup: {old_backup.name}")
                
        except Exception as e:
            print(f"[BackupScheduler] Cleanup error: {e}")


# Global scheduler instance
scheduler = BackupScheduler()


def init_scheduler(app):
    """Initialize and start the backup scheduler"""
    scheduler.init_app(app)
    scheduler.start()
    return scheduler
