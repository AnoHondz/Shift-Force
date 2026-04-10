import csv
from datetime import date, timedelta
from io import StringIO

from flask import Blueprint, Response, render_template, request

from ..decorators import roles_required
from ..models import Department, Employee, PayrollRecord, Role


reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def get_period_bounds(period: str):
    today = date.today()
    if period == "monthly":
        return today.replace(day=1), today
    return today - timedelta(days=today.weekday()), today


def fetch_report_rows(period: str):
    start_date, end_date = get_period_bounds(period)
    rows = (
        PayrollRecord.query.join(Employee)
        .join(Department, isouter=True)
        .filter(PayrollRecord.work_date.between(start_date, end_date))
        .order_by(Employee.last_name, Employee.first_name)
        .all()
    )
    return rows, start_date, end_date


@reports_bp.route("/")
@roles_required(Role.ADMIN, Role.MANAGER)
def index():
    weekly_rows, weekly_start, weekly_end = fetch_report_rows("weekly")
    monthly_rows, monthly_start, monthly_end = fetch_report_rows("monthly")
    weekly_total_pay = sum(row.total_pay for row in weekly_rows)
    weekly_total_hours = sum(row.hours_worked for row in weekly_rows)
    monthly_total_pay = sum(row.total_pay for row in monthly_rows)
    monthly_total_hours = sum(row.hours_worked for row in monthly_rows)
    return render_template(
        "reports.html",
        active_page="reports",
        weekly_rows=weekly_rows,
        weekly_start=weekly_start,
        weekly_end=weekly_end,
        monthly_rows=monthly_rows,
        monthly_start=monthly_start,
        monthly_end=monthly_end,
        weekly_total_pay=weekly_total_pay,
        weekly_total_hours=weekly_total_hours,
        monthly_total_pay=monthly_total_pay,
        monthly_total_hours=monthly_total_hours,
        recent_salary_changes=[],
    )


@reports_bp.route("/export.csv")
@roles_required(Role.ADMIN, Role.MANAGER)
def export_csv():
    period = request.args.get("period", "weekly")
    rows, start_date, end_date = fetch_report_rows(period)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Employee",
            "Position",
            "Department",
            "Week Start",
            "Week End",
            "Hours Worked",
            "Regular Hours",
            "Overtime Hours",
            "Total Pay",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.employee.full_name(),
                row.employee.job_title,
                row.employee.department.name if row.employee.department else "Unassigned",
                start_date.isoformat(),
                end_date.isoformat(),
                f"{row.hours_worked:.2f}",
                f"{row.regular_hours:.2f}",
                f"{row.overtime_hours:.2f}",
                f"{row.total_pay:.2f}",
            ]
        )
    filename = f"{period}-payroll-report.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
