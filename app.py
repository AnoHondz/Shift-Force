from pathlib import Path
import os
import sqlite3

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
import mysql.connector
from mysql.connector import Error


load_dotenv()

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
SQLITE_DB_PATH = BASE_DIR / "employee_mgmt.db"

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "yourpassword"),
    "database": os.getenv("DB_NAME", "company"),
}

DEFAULT_DEPARTMENTS = [
    (1, "Human Resources"),
    (2, "Engineering"),
    (3, "Finance"),
    (4, "Operations"),
    (5, "Sales"),
]


def ok(data=None, message="Success"):
    return jsonify({"status": "success", "message": message, "data": data})


def err(message="Error", code=400):
    return jsonify({"status": "error", "message": message}), code


def seed_departments(cursor, backend):
    if backend == "mysql":
        cursor.executemany(
            "INSERT IGNORE INTO departments (dept_id, dept_name) VALUES (%s, %s)",
            DEFAULT_DEPARTMENTS,
        )
    else:
        cursor.executemany(
            "INSERT OR IGNORE INTO departments (dept_id, dept_name) VALUES (?, ?)",
            DEFAULT_DEPARTMENTS,
        )


def init_sqlite_db(db):
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            dept_id INTEGER PRIMARY KEY,
            dept_name TEXT NOT NULL UNIQUE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            salary REAL NOT NULL,
            email TEXT NOT NULL UNIQUE,
            dept_id INTEGER,
            FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
        )
        """
    )

    employee_columns = {
        row[1] for row in db.execute("PRAGMA table_info(employees)").fetchall()
    }
    if "dept_id" not in employee_columns:
        db.execute("ALTER TABLE employees ADD COLUMN dept_id INTEGER REFERENCES departments(dept_id)")

    seed_departments(db.cursor(), "sqlite")
    db.commit()


def init_mysql_db(db):
    cursor = db.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS departments (
                dept_id INT PRIMARY KEY,
                dept_name VARCHAR(50) NOT NULL UNIQUE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INT PRIMARY KEY,
                first_name VARCHAR(50) NOT NULL,
                last_name VARCHAR(50) NOT NULL,
                salary DECIMAL(10,2) NOT NULL,
                email VARCHAR(100) NOT NULL UNIQUE,
                dept_id INT NULL,
                CONSTRAINT fk_employees_departments
                    FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
            )
            """
        )

        cursor.execute("SHOW COLUMNS FROM employees LIKE 'dept_id'")
        if cursor.fetchone() is None:
            cursor.execute("ALTER TABLE employees ADD COLUMN dept_id INT NULL")

        cursor.execute(
            """
            SELECT CONSTRAINT_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'employees'
              AND COLUMN_NAME = 'dept_id'
              AND REFERENCED_TABLE_NAME = 'departments'
            """,
            (DB_CONFIG["database"],),
        )
        if cursor.fetchone() is None:
            try:
                cursor.execute(
                    """
                    ALTER TABLE employees
                    ADD CONSTRAINT fk_employees_departments
                    FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
                    """
                )
            except Error:
                pass

        seed_departments(cursor, "mysql")
        db.commit()
    finally:
        cursor.close()


def get_sqlite_db():
    db = sqlite3.connect(SQLITE_DB_PATH)
    db.row_factory = sqlite3.Row
    init_sqlite_db(db)
    return db, "sqlite"


def get_db():
    preferred_backend = os.getenv("DB_BACKEND", "mysql").strip().lower()
    if preferred_backend == "sqlite":
        return get_sqlite_db()

    try:
        db = mysql.connector.connect(**DB_CONFIG)
        init_mysql_db(db)
        return db, "mysql"
    except Error as exc:
        app.logger.warning("MySQL unavailable, falling back to SQLite: %s", exc)
        return get_sqlite_db()


def get_cursor(db, backend):
    return db.cursor(dictionary=True) if backend == "mysql" else db.cursor()


def sql(backend, mysql_query, sqlite_query=None):
    if backend == "mysql":
        return mysql_query
    return sqlite_query or mysql_query.replace("%s", "?").replace("CAST(e.id AS CHAR)", "CAST(e.id AS TEXT)")


def row_to_dict(row):
    if row is None:
        return None
    return dict(row) if isinstance(row, sqlite3.Row) else row


def rows_to_dicts(rows):
    return [row_to_dict(row) for row in rows]


def close_db(db, cursor=None):
    if cursor is not None:
        cursor.close()
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def get_department_by_id(cursor, backend, dept_id):
    cursor.execute(
        sql(backend, "SELECT dept_id, dept_name FROM departments WHERE dept_id = %s"),
        (dept_id,),
    )
    return row_to_dict(cursor.fetchone())


@app.route("/")
def index():
    return render_template("dashboard.html", active_page="dashboard")


@app.route("/employees")
def employees_page():
    return render_template("employees.html", active_page="employees")


@app.route("/add-employee")
def add_employee_page():
    return render_template("addemployees.html", active_page="add")


@app.route("/api/departments", methods=["GET"])
def departments():
    db = None
    cursor = None
    try:
        db, backend = get_db()
        cursor = get_cursor(db, backend)
        cursor.execute("SELECT dept_id, dept_name FROM departments ORDER BY dept_name")
        return ok(rows_to_dicts(cursor.fetchall()))
    except (Error, sqlite3.Error) as exc:
        return err(str(exc), 500)
    finally:
        close_db(db, cursor)


@app.route("/api/employees", methods=["GET", "POST"])
def employees():
    db = None
    cursor = None
    try:
        db, backend = get_db()
        cursor = get_cursor(db, backend)

        if request.method == "GET":
            search = request.args.get("search", "").strip()
            department = request.args.get("department", "").strip()
            where_clauses = []
            values = []

            if search:
                where_clauses.append(
                    "(e.first_name LIKE {0} OR e.last_name LIKE {0} OR e.email LIKE {0} OR d.dept_name LIKE {0} OR CAST(e.id AS CHAR) LIKE {0})".format(
                        "%s" if backend == "mysql" else "?"
                    )
                )
                values.extend([f"%{search}%"] * 5)

            if department:
                try:
                    department_id = int(department)
                except ValueError:
                    return err("Department filter must be a valid number.")
                where_clauses.append(f"e.dept_id = {'%s' if backend == 'mysql' else '?'}")
                values.append(department_id)

            query = """
                SELECT
                    e.id,
                    e.first_name,
                    e.last_name,
                    e.salary,
                    e.email,
                    e.dept_id,
                    d.dept_name
                FROM employees e
                LEFT JOIN departments d ON e.dept_id = d.dept_id
            """
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            query += " ORDER BY e.id"

            cursor.execute(sql(backend, query), tuple(values))
            return ok(rows_to_dicts(cursor.fetchall()))

        body = request.get_json(silent=True) or request.form.to_dict()
        required_fields = ("id", "first_name", "last_name", "salary", "email", "dept_id")
        missing_fields = [field for field in required_fields if not str(body.get(field, "")).strip()]
        if missing_fields:
            return err(f"Missing required fields: {', '.join(missing_fields)}")

        try:
            emp_id = int(body["id"])
            salary = float(body["salary"])
            dept_id = int(body["dept_id"])
        except (TypeError, ValueError):
            return err("Employee ID, salary, and department must be valid numbers.")

        first_name = body["first_name"].strip()
        last_name = body["last_name"].strip()
        email = body["email"].strip()

        cursor.execute(sql(backend, "SELECT id FROM employees WHERE id = %s"), (emp_id,))
        if cursor.fetchone():
            return err(f"Employee ID {emp_id} already exists.", 409)

        cursor.execute(sql(backend, "SELECT id FROM employees WHERE email = %s"), (email,))
        if cursor.fetchone():
            return err(f"Email {email} is already registered.", 409)

        department_row = get_department_by_id(cursor, backend, dept_id)
        if department_row is None:
            return err("Selected department does not exist.", 404)

        cursor.execute(
            sql(
                backend,
                """
                INSERT INTO employees (id, first_name, last_name, salary, email, dept_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
            ),
            (emp_id, first_name, last_name, salary, email, dept_id),
        )
        db.commit()
        return ok(message="Employee added successfully!"), 201

    except (Error, sqlite3.Error) as exc:
        return err(str(exc), 500)
    finally:
        close_db(db, cursor)


@app.route("/api/employees/<int:emp_id>", methods=["GET", "PUT", "DELETE"])
def employee(emp_id):
    db = None
    cursor = None
    try:
        db, backend = get_db()
        cursor = get_cursor(db, backend)

        if request.method == "GET":
            cursor.execute(
                sql(
                    backend,
                    """
                    SELECT
                        e.id,
                        e.first_name,
                        e.last_name,
                        e.salary,
                        e.email,
                        e.dept_id,
                        d.dept_name
                    FROM employees e
                    LEFT JOIN departments d ON e.dept_id = d.dept_id
                    WHERE e.id = %s
                    """,
                ),
                (emp_id,),
            )
            row = row_to_dict(cursor.fetchone())
            return ok(row) if row else err("Employee not found.", 404)

        if request.method == "PUT":
            body = request.get_json() or {}
            fields = []
            values = []

            if "email" in body and str(body["email"]).strip():
                cursor.execute(
                    sql(backend, "SELECT id FROM employees WHERE email = %s AND id <> %s"),
                    (str(body["email"]).strip(), emp_id),
                )
                if cursor.fetchone():
                    return err(f"Email {str(body['email']).strip()} is already registered.", 409)

            for col in ("first_name", "last_name", "salary", "email", "dept_id"):
                if col in body and str(body[col]).strip():
                    if col == "dept_id":
                        try:
                            dept_id = int(body[col])
                        except (TypeError, ValueError):
                            return err("Department must be a valid number.")
                        if get_department_by_id(cursor, backend, dept_id) is None:
                            return err("Selected department does not exist.", 404)
                        values.append(dept_id)
                    elif col == "salary":
                        try:
                            values.append(float(body[col]))
                        except (TypeError, ValueError):
                            return err("Salary must be a valid number.")
                    else:
                        values.append(str(body[col]).strip())
                    fields.append(f"{col} = {'%s' if backend == 'mysql' else '?'}")

            if not fields:
                return err("No fields to update.")

            values.append(emp_id)
            cursor.execute(
                f"UPDATE employees SET {', '.join(fields)} WHERE id = {'%s' if backend == 'mysql' else '?'}",
                values,
            )
            db.commit()
            if cursor.rowcount == 0:
                return err("Employee not found.", 404)
            return ok(message="Employee updated successfully!")

        cursor.execute(sql(backend, "DELETE FROM employees WHERE id = %s"), (emp_id,))
        db.commit()
        if cursor.rowcount == 0:
            return err("Employee not found.", 404)
        return ok(message="Employee deleted successfully!")

    except (Error, sqlite3.Error) as exc:
        return err(str(exc), 500)
    finally:
        close_db(db, cursor)


@app.route("/api/stats")
def stats():
    db = None
    cursor = None
    try:
        db, backend = get_db()
        cursor = get_cursor(db, backend)
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                IFNULL(AVG(salary), 0) AS avg_salary,
                IFNULL(MAX(salary), 0) AS max_salary,
                IFNULL(MIN(salary), 0) AS min_salary
            FROM employees
            """
        )
        return ok(row_to_dict(cursor.fetchone()))
    except (Error, sqlite3.Error) as exc:
        return err(str(exc), 500)
    finally:
        close_db(db, cursor)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
