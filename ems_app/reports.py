import csv
from datetime import date, timedelta
from io import StringIO

from flask import Blueprint, Response, render_template, request

from .auth import executive_required
from .db import get_db


bp = Blueprint("reports", __name__, url_prefix="/reports")


def get_period_bounds(period):
    today = date.today()
    if period == "monthly":
        start = today.replace(day=1)
        end = today
    else:
        start = today - timedelta(days=today.weekday())
        end = today
    return start.isoformat(), end.isoformat()


def fetch_report_rows(period):
    start_date, end_date = get_period_bounds(period)
    db = get_db()
    rows = db.execute(
        """
        SELECT
            e.id,
            e.first_name,
            e.last_name,
            e.job_title,
            COALESCE(d.dept_name, 'Unassigned') AS department_name,
            e.salary,
            e.overtime_rate,
            COALESCE(SUM(CASE WHEN h.hours_worked > 8 THEN 8 ELSE h.hours_worked END), 0) AS regular_hours,
            COALESCE(SUM(CASE WHEN h.hours_worked > 8 THEN h.hours_worked - 8 ELSE 0 END), 0) AS overtime_hours,
            COALESCE(
                SUM(
                    CASE
                        WHEN h.hours_worked > 8 THEN (8 * e.salary) + ((h.hours_worked - 8) * e.salary * e.overtime_rate)
                        ELSE h.hours_worked * e.salary
                    END
                ),
                0
            ) AS total_pay
        FROM employees e
        LEFT JOIN departments d ON d.dept_id = e.dept_id
        LEFT JOIN hours_logs h
            ON h.employee_id = e.id
           AND h.work_date BETWEEN ? AND ?
        GROUP BY e.id, e.first_name, e.last_name, e.job_title, d.dept_name, e.salary, e.overtime_rate
        ORDER BY e.last_name, e.first_name
        """,
        (start_date, end_date),
    ).fetchall()
    return rows, start_date, end_date


@bp.route("/")
@executive_required
def index():
    weekly_rows, weekly_start, weekly_end = fetch_report_rows("weekly")
    monthly_rows, monthly_start, monthly_end = fetch_report_rows("monthly")
    recent_salary_changes = get_db().execute(
        """
        SELECT
            sh.change_date,
            sh.old_salary,
            sh.new_salary,
            e.first_name,
            e.last_name
        FROM salary_history sh
        JOIN employees e ON e.id = sh.emp_id
        ORDER BY sh.change_date DESC
        LIMIT 10
        """
    ).fetchall()
    return render_template(
        "reports.html",
        active_page="reports",
        weekly_rows=weekly_rows,
        weekly_start=weekly_start,
        weekly_end=weekly_end,
        monthly_rows=monthly_rows,
        monthly_start=monthly_start,
        monthly_end=monthly_end,
        recent_salary_changes=recent_salary_changes,
    )


@bp.route("/export.csv")
@executive_required
def export_csv():
    period = request.args.get("period", "weekly")
    rows, start_date, end_date = fetch_report_rows(period)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Employee ID",
            "Name",
            "Position",
            "Department",
            "Hourly Pay",
            "Regular Hours",
            "Overtime Hours",
            "Total Pay",
            "Period Start",
            "Period End",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row["id"],
                f"{row['first_name']} {row['last_name']}",
                row["job_title"],
                row["department_name"],
                f"{float(row['salary']):.2f}",
                f"{float(row['regular_hours']):.2f}",
                f"{float(row['overtime_hours']):.2f}",
                f"{float(row['total_pay']):.2f}",
                start_date,
                end_date,
            ]
        )

    filename = f"{period}-report.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
