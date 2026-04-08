import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash


DEFAULT_DEPARTMENTS = [
    "Executive",
    "Engineering",
    "Finance",
    "Human Resources",
    "Operations",
    "Sales",
]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_columns(db, table_name):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}


def ensure_employee_columns(db):
    columns = get_columns(db, "employees")
    additions = {
        "job_title": "TEXT NOT NULL DEFAULT 'Employee'",
        "dept_id": "INTEGER REFERENCES departments(dept_id)",
        "overtime_rate": "REAL NOT NULL DEFAULT 1.5",
        "schedule": "TEXT NOT NULL DEFAULT 'Mon-Fri 9:00 AM - 5:00 PM'",
        "address": "TEXT NOT NULL DEFAULT ''",
        "phone": "TEXT NOT NULL DEFAULT ''",
    }

    for column_name, definition in additions.items():
        if column_name not in columns:
            db.execute(f"ALTER TABLE employees ADD COLUMN {column_name} {definition}")

    db.execute(
        """
        UPDATE employees
        SET job_title = COALESCE(NULLIF(job_title, ''), 'Employee'),
            overtime_rate = COALESCE(overtime_rate, 1.5),
            schedule = COALESCE(NULLIF(schedule, ''), 'Mon-Fri 9:00 AM - 5:00 PM'),
            address = COALESCE(address, ''),
            phone = COALESCE(phone, '')
        """
    )


def ensure_salary_history_columns(db):
    columns = get_columns(db, "salary_history")
    if "changed_by" not in columns:
        db.execute("ALTER TABLE salary_history ADD COLUMN changed_by INTEGER")


def init_db():
    db = get_db()

    # Core reference data.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            dept_id INTEGER PRIMARY KEY AUTOINCREMENT,
            dept_name TEXT NOT NULL UNIQUE
        )
        """
    )

    # Keep the existing employees table compatible with new fields for the upgraded app.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            job_title TEXT NOT NULL DEFAULT 'Employee',
            salary REAL NOT NULL DEFAULT 25.0,
            dept_id INTEGER,
            overtime_rate REAL NOT NULL DEFAULT 1.5,
            schedule TEXT NOT NULL DEFAULT 'Mon-Fri 9:00 AM - 5:00 PM',
            address TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
        )
        """
    )
    ensure_employee_columns(db)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('executive', 'employee')),
            employee_id INTEGER UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS hours_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            work_date TEXT NOT NULL,
            hours_worked REAL NOT NULL CHECK(hours_worked >= 0),
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(employee_id, work_date),
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS salary_history (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id INTEGER NOT NULL,
            old_salary REAL NOT NULL,
            new_salary REAL NOT NULL,
            change_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed_by INTEGER,
            FOREIGN KEY (emp_id) REFERENCES employees(id),
            FOREIGN KEY (changed_by) REFERENCES users(id)
        )
        """
    )
    ensure_salary_history_columns(db)

    db.executemany(
        "INSERT OR IGNORE INTO departments (dept_name) VALUES (?)",
        [(department,) for department in DEFAULT_DEPARTMENTS],
    )

    # Seed one executive account so the app is immediately usable locally.
    db.execute(
        """
        INSERT OR IGNORE INTO users (username, password_hash, role)
        VALUES (?, ?, 'executive')
        """,
        (
            current_app.config["DEFAULT_EXEC_USERNAME"],
            generate_password_hash(current_app.config["DEFAULT_EXEC_PASSWORD"]),
        ),
    )

    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Initialized the database.")

    with app.app_context():
        init_db()
