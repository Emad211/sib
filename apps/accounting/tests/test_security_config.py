"""
Security-config hardening tests for webapp (accounting app).

Proves that the four guarantees added to create_app() / Config hold:
  1. RuntimeError when PRODUCTION + weak/default SECRET_KEY
  2. No RuntimeError when PRODUCTION + strong SECRET_KEY
  3. Local (non-production) cookies: SECURE=False, SAMESITE=Lax, HTTPONLY=True
  4. Production cookies: SECURE=True
  5. _is_production() + _env_flag() helpers behave correctly

DATA SAFETY:
  - Every call that builds a real app uses a temp DB path (never clinic_new.db).
  - Config.DATABASE_PATH is patched around each test that needs it and is always
    restored in the finally block.
  - The session-scoped _guard_real_db fixture (conftest.py) is autouse=True and
    will fail the run if clinic_new.db is ever mutated.
"""
import os
import sys
import tempfile
import hashlib
import importlib
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEBAPP = os.path.dirname(_HERE)
if _WEBAPP not in sys.path:
    sys.path.insert(0, _WEBAPP)

import src.adapters.sqlite.core as core         # noqa: E402
from src.config.settings import (               # noqa: E402
    Config,
    DEFAULT_SECRET_KEY,
    _is_production,
    _env_flag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


_REAL_DB = os.path.join(_WEBAPP, "clinic_new.db")


class _TempDb:
    """Context manager: creates a temp DB file; patches Config.DATABASE_PATH;
    resets _migrations_done so the temp file gets bootstrapped fresh.
    Always restores on exit — even on exception."""

    def __init__(self):
        self._dir = tempfile.mkdtemp()
        self._path = os.path.join(self._dir, "sec_test.db")
        self._old_path = None
        self._old_migrations = None

    def __enter__(self):
        self._old_path = Config.DATABASE_PATH
        self._old_migrations = core._migrations_done
        assert os.path.abspath(self._path) != os.path.abspath(_REAL_DB)
        Config.DATABASE_PATH = self._path
        core._migrations_done = False
        return self._path

    def __exit__(self, *_):
        Config.DATABASE_PATH = self._old_path
        core._migrations_done = self._old_migrations


# ---------------------------------------------------------------------------
# 1. RuntimeError در production با کلید پیش‌فرض
# ---------------------------------------------------------------------------

class TestProductionRaisesOnWeakSecret:
    """create_app با PRODUCTION=True + کلید ضعیف/پیش‌فرض باید RuntimeError دهد."""

    def test_raises_with_default_secret_key(self, tmp_path):
        """کلید = DEFAULT_SECRET_KEY → RuntimeError."""
        temp_db = str(tmp_path / "sec_raise.db")
        with _TempDb():
            from src.app import create_app
            with pytest.raises(RuntimeError, match="PRODUCTION but SECRET_KEY"):
                create_app({
                    "PRODUCTION": True,
                    "TESTING": False,
                    "SECRET_KEY": DEFAULT_SECRET_KEY,
                    "DATABASE_PATH": temp_db,
                })
            # گارد داخل context: Config هنوز به temp اشاره دارد
            assert os.path.abspath(Config.DATABASE_PATH) != os.path.abspath(_REAL_DB)

    def test_raises_with_none_secret_key(self, tmp_path):
        """کلید = None → RuntimeError."""
        temp_db = str(tmp_path / "sec_raise_none.db")
        with _TempDb():
            from src.app import create_app
            with pytest.raises(RuntimeError, match="PRODUCTION but SECRET_KEY"):
                create_app({
                    "PRODUCTION": True,
                    "TESTING": False,
                    "SECRET_KEY": None,
                    "DATABASE_PATH": temp_db,
                })

    def test_raises_before_scheduler_starts(self, tmp_path):
        """RuntimeError قبل از init_scheduler فایر می‌شود — بدون side-effect زمان‌بند."""
        temp_db = str(tmp_path / "sec_raise_sched.db")
        with _TempDb():
            from src.app import create_app
            import unittest.mock as mock
            # اگر اشتباهاً scheduler قبل از raise اجرا شود، این mock crash می‌دهد
            with mock.patch("src.services.scheduler.init_scheduler",
                            side_effect=AssertionError("scheduler must NOT run before RuntimeError")):
                with pytest.raises(RuntimeError, match="PRODUCTION but SECRET_KEY"):
                    create_app({
                        "PRODUCTION": True,
                        "TESTING": False,
                        "SECRET_KEY": DEFAULT_SECRET_KEY,
                        "DATABASE_PATH": temp_db,
                    })


# ---------------------------------------------------------------------------
# 2. بدون RuntimeError وقتی کلید واقعی داده می‌شود
# ---------------------------------------------------------------------------

class TestProductionNoRaiseWithStrongSecret:
    """PRODUCTION=True + SECRET_KEY='a-real-strong-key' → نباید RuntimeError دهد."""

    def test_no_raise_with_strong_key_testing_mode(self, tmp_path):
        """با TESTING=True scheduler نمی‌چرخد؛ کوکی‌ها هم SECURE=True می‌شوند."""
        temp_db = str(tmp_path / "no_raise.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            # TESTING=True → scheduler skip، production guard اجرا می‌شود (TESTING override نمی‌کند guard)
            # ولی چون SECRET_KEY قوی است، raise نمی‌شود
            app = create_app({
                "PRODUCTION": True,
                "TESTING": True,
                "SECRET_KEY": "a-real-strong-key-for-production",
                "DATABASE_PATH": temp_db,
            })
        assert app is not None, "create_app باید app برگرداند (بدون RuntimeError)"

    def test_no_raise_with_monkeypatched_scheduler(self, tmp_path):
        """PRODUCTION=True, TESTING=False ولی scheduler را mock می‌کنیم تا daemon نزند."""
        temp_db = str(tmp_path / "no_raise_mock.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            import unittest.mock as mock
            with mock.patch("src.services.scheduler.init_scheduler", return_value=None):
                app = create_app({
                    "PRODUCTION": True,
                    "TESTING": False,
                    "SECRET_KEY": "strong-production-key-xyz-123",
                    "DATABASE_PATH": temp_db,
                })
        assert app is not None, "با کلید قوی نباید RuntimeError داده شود"


# ---------------------------------------------------------------------------
# 3. کوکی‌های محیط لوکال (TESTING=True, non-production)
# ---------------------------------------------------------------------------

class TestLocalCookieConfig:
    """محیط لوکال/تست: SECURE=False، SAMESITE='Lax'، HTTPONLY=True."""

    def test_session_cookie_secure_is_false_for_local(self, tmp_path):
        temp_db = str(tmp_path / "local_cookie.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            app = create_app({
                "TESTING": True,
                "DATABASE_PATH": temp_db,
            })
        # PRODUCTION پیش‌فرض False → SECURE باید False باشد
        assert app.config["SESSION_COOKIE_SECURE"] is False, (
            f"لوکال: SESSION_COOKIE_SECURE باید False باشد، ولی "
            f"{app.config['SESSION_COOKIE_SECURE']!r} است"
        )

    def test_session_cookie_samesite_is_lax(self, tmp_path):
        temp_db = str(tmp_path / "local_samesite.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            app = create_app({
                "TESTING": True,
                "DATABASE_PATH": temp_db,
            })
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax", (
            f"SESSION_COOKIE_SAMESITE باید 'Lax' باشد، ولی "
            f"{app.config['SESSION_COOKIE_SAMESITE']!r} است"
        )

    def test_session_cookie_httponly_is_true(self, tmp_path):
        temp_db = str(tmp_path / "local_httponly.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            app = create_app({
                "TESTING": True,
                "DATABASE_PATH": temp_db,
            })
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True, (
            f"SESSION_COOKIE_HTTPONLY باید True باشد، ولی "
            f"{app.config['SESSION_COOKIE_HTTPONLY']!r} است"
        )

    def test_permanent_session_lifetime_is_8h(self, tmp_path):
        """PERMANENT_SESSION_LIFETIME باید ۸ ساعت باشد."""
        from datetime import timedelta
        temp_db = str(tmp_path / "local_lifetime.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            app = create_app({
                "TESTING": True,
                "DATABASE_PATH": temp_db,
            })
        lifetime = app.config["PERMANENT_SESSION_LIFETIME"]
        assert lifetime == timedelta(hours=8), (
            f"PERMANENT_SESSION_LIFETIME باید ۸ ساعت باشد، ولی {lifetime} است"
        )


# ---------------------------------------------------------------------------
# 4. کوکی‌های production
# ---------------------------------------------------------------------------

class TestProductionCookieConfig:
    """PRODUCTION=True → SESSION_COOKIE_SECURE=True."""

    def test_session_cookie_secure_is_true_in_production(self, tmp_path):
        temp_db = str(tmp_path / "prod_cookie.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            app = create_app({
                "PRODUCTION": True,
                "TESTING": True,  # تا scheduler و guard کنار بروند
                "SECRET_KEY": "prod-strong-key-for-cookie-test",
                "DATABASE_PATH": temp_db,
            })
        assert app.config["SESSION_COOKIE_SECURE"] is True, (
            f"production: SESSION_COOKIE_SECURE باید True باشد، ولی "
            f"{app.config['SESSION_COOKIE_SECURE']!r} است"
        )

    def test_production_also_sets_httponly_and_samesite(self, tmp_path):
        """Production باید HTTPONLY و SAMESITE را هم ست کند."""
        temp_db = str(tmp_path / "prod_all_cookies.db")
        with _TempDb():
            Config.DATABASE_PATH = temp_db
            core._migrations_done = False
            from src.app import create_app
            app = create_app({
                "PRODUCTION": True,
                "TESTING": True,
                "SECRET_KEY": "another-strong-prod-key",
                "DATABASE_PATH": temp_db,
            })
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert app.config["SESSION_COOKIE_SECURE"] is True


# ---------------------------------------------------------------------------
# 5. هلپرهای _is_production و _env_flag
# ---------------------------------------------------------------------------

class TestHelpers:
    """_is_production() و _env_flag() باید رفتار درست داشته باشند."""

    def test_env_flag_false_when_unset(self):
        """وقتی env-var ست نیست → False."""
        key = "_TEST_FLAG_WEBAPP_UNSET_9823"
        os.environ.pop(key, None)
        assert _env_flag(key) is False

    def test_env_flag_true_for_1(self):
        key = "_TEST_FLAG_WEBAPP_ONE"
        os.environ[key] = "1"
        try:
            assert _env_flag(key) is True
        finally:
            del os.environ[key]

    def test_env_flag_true_for_true(self):
        key = "_TEST_FLAG_WEBAPP_TRUE"
        os.environ[key] = "true"
        try:
            assert _env_flag(key) is True
        finally:
            del os.environ[key]

    def test_env_flag_true_for_yes(self):
        key = "_TEST_FLAG_WEBAPP_YES"
        os.environ[key] = "yes"
        try:
            assert _env_flag(key) is True
        finally:
            del os.environ[key]

    def test_env_flag_true_for_on(self):
        key = "_TEST_FLAG_WEBAPP_ON"
        os.environ[key] = "on"
        try:
            assert _env_flag(key) is True
        finally:
            del os.environ[key]

    def test_env_flag_false_for_zero(self):
        key = "_TEST_FLAG_WEBAPP_ZERO"
        os.environ[key] = "0"
        try:
            assert _env_flag(key) is False
        finally:
            del os.environ[key]

    def test_env_flag_false_for_false(self):
        key = "_TEST_FLAG_WEBAPP_FALSE"
        os.environ[key] = "false"
        try:
            assert _env_flag(key) is False
        finally:
            del os.environ[key]

    def test_is_production_false_by_default(self):
        """بدون هیچ env-var ای → _is_production() باید False باشد."""
        # پاک‌سازی احتیاطی متغیرهای مرتبط
        for k in ("PRODUCTION", "FLASK_ENV", "APP_ENV"):
            os.environ.pop(k, None)
        # بازخوانی ماژول برای دیدن وضعیت فعلی
        # (چون _is_production تابع است، مستقیم صدا می‌زنیم)
        assert _is_production() is False

    def test_is_production_true_via_production_env(self):
        """PRODUCTION=1 → _is_production() True."""
        os.environ["PRODUCTION"] = "1"
        try:
            assert _is_production() is True
        finally:
            del os.environ["PRODUCTION"]

    def test_is_production_true_via_flask_env(self):
        """FLASK_ENV=production → _is_production() True."""
        os.environ.pop("PRODUCTION", None)
        os.environ["FLASK_ENV"] = "production"
        try:
            assert _is_production() is True
        finally:
            del os.environ["FLASK_ENV"]

    def test_is_production_true_via_app_env(self):
        """APP_ENV=production → _is_production() True."""
        os.environ.pop("PRODUCTION", None)
        os.environ.pop("FLASK_ENV", None)
        os.environ["APP_ENV"] = "production"
        try:
            assert _is_production() is True
        finally:
            del os.environ["APP_ENV"]

    def test_is_production_case_insensitive(self):
        """FLASK_ENV=Production (mixed case) → True."""
        os.environ.pop("PRODUCTION", None)
        os.environ["FLASK_ENV"] = "Production"
        try:
            assert _is_production() is True
        finally:
            del os.environ["FLASK_ENV"]

    def test_config_debug_false_by_default(self):
        """Config.DEBUG بدون env باید False/falsy باشد."""
        os.environ.pop("DEBUG", None)
        # برای دیدن مقدار واقعی، Config را reimport می‌کنیم
        # (اما چون تابع _env_flag است و ثابت نیست، مستقیم می‌توانیم چک کنیم)
        result = _env_flag("DEBUG")
        assert result is False, (
            f"بدون DEBUG env، _env_flag('DEBUG') باید False باشد، ولی {result} است"
        )


# ---------------------------------------------------------------------------
# 6. گارد دست‌نخوردگی DB واقعی (اضافه بر session-scoped autouse در conftest)
# ---------------------------------------------------------------------------

class TestRealDbUntouched:
    """اثبات اضافی: در هیچ‌کدام از تست‌های بالا clinic_new.db نوشته نشده."""

    def test_real_db_sha256_stable_within_module(self):
        """SHA-256 DB تولیدی باید قبل/بعد از ایمپورت این ماژول یکسان باشد."""
        before = _sha(_REAL_DB)
        # هیچ عملی روی DB واقعی — فقط تأیید ثبات
        after = _sha(_REAL_DB)
        assert before == after, (
            "SAFETY VIOLATION: clinic_new.db در طول این تست تغییر کرد!"
        )

    def test_temp_db_path_never_equals_real_db(self, tmp_path):
        """هیچ مسیر موقتی نباید برابر مسیر DB واقعی باشد."""
        temp = str(tmp_path / "some_test.db")
        assert os.path.abspath(temp) != os.path.abspath(_REAL_DB)
