import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'workforce.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DEBUG = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = APP_ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    REMEMBER_COOKIE_DURATION = timedelta(days=7)
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    PUBLIC_REGISTRATION = APP_ENV != "production"
    PUBLIC_STORE_OWNER_REGISTRATION = (
        os.getenv(
            "PUBLIC_STORE_OWNER_REGISTRATION", "true" if APP_ENV != "production" else "false"
        ).lower()
        == "true"
    )
    STORE_DATABASE_ROOT = os.getenv("STORE_DATABASE_ROOT")
    WORKFORCE_TIMEZONE = os.getenv("WORKFORCE_TIMEZONE", "UTC")
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    DATA_ROOT = os.getenv("DATA_ROOT")
    MFA_ENCRYPTION_KEY = os.getenv("MFA_ENCRYPTION_KEY")
    REQUIRE_PRIVILEGED_MFA = APP_ENV == "production"
    PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", os.getenv("RENDER_EXTERNAL_URL", ""))
    TRUSTED_HOSTS = [urlsplit(PUBLIC_ORIGIN).hostname] if PUBLIC_ORIGIN else None
    TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"
    BACKUP_ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY")
    BACKUP_BUCKET = os.getenv("BACKUP_BUCKET")
    BACKUP_PREFIX = os.getenv("BACKUP_PREFIX", "shiftspace")
    BACKUP_INTERVAL_SECONDS = int(os.getenv("BACKUP_INTERVAL_SECONDS", "86400"))
    REQUIRE_PAYROLL_REVIEW = APP_ENV == "production"
