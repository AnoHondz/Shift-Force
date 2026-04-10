import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "employee_mgmt.db"


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "ano-hondz-workforce-secret")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin2026!")
    MANAGER_SIGNUP_CODE = os.getenv("MANAGER_SIGNUP_CODE", "MANAGER-2026")
    ADMIN_SIGNUP_CODE = os.getenv("ADMIN_SIGNUP_CODE", "ADMIN-2026")
