import csv
from datetime import date, timedelta
from io import BytesIO, StringIO

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from sqlalchemy import asc, desc, func

from ..decorators import roles_required
from ..models import Department, Employee, PayrollRecord, Role
from ..utils import parse_date, parse_positive_int


reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def get_period_bounds(period: str):
    today = date.today()
    if period == "last30":
        return today - timedelta(days=30), today
    if period == "monthly":
        return today.replace(day=1), today
    return today - timedelta(days=today.weekday()), today


def fetch_report_rows(period: str):
    if period not in {"weekly", "monthly", "last30"}:
        period = "weekly"
    start_date, end_date = get_period_bounds(period)
    query = (
        PayrollRecord.query.join(Employee)
        .join(Department, isouter=True)
        .filter(PayrollRecord.work_date.between(start_date, end_date))
    )
    return query, start_date, end_date


@reports_bp.route("/")
@roles_required(*Role.ADMIN_LIKE)
def index():
    preset = request.args.get("preset", "weekly")
    if preset not in {"weekly", "monthly", "last30"}:
        preset = "weekly"
    requested_start = parse_date(request.args.get("start_date", ""))
    requested_end = parse_date(request.args.get("end_date", ""))
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=20, maximum=100)
    sort = request.args.get("sort", "date")
    direction = request.args.get("direction", "desc")

    if requested_start and requested_end and requested_start <= requested_end:
        rows_query = (
            PayrollRecord.query.join(Employee)
            .join(Department, isouter=True)
            .filter(PayrollRecord.work_date.between(requested_start, requested_end))
        )
        start_date, end_date = requested_start, requested_end
    else:
        if requested_start or requested_end:
            flash("Invalid date range supplied. Using preset period instead.", "error")
        rows_query, start_date, end_date = fetch_report_rows(preset)

    sort_map = {
        "date": PayrollRecord.work_date,
        "employee": Employee.last_name,
        "hours": PayrollRecord.hours_worked,
        "overtime": PayrollRecord.overtime_hours,
        "pay": PayrollRecord.total_pay,
    }
    sort_column = sort_map.get(sort, PayrollRecord.work_date)
    order_clause = desc(sort_column) if direction == "desc" else asc(sort_column)
    pagination = rows_query.order_by(order_clause, Employee.last_name, Employee.first_name).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    rows = pagination.items

    chart_rows = (
        PayrollRecord.query.with_entities(
            PayrollRecord.work_date,
            func.sum(PayrollRecord.total_pay).label("total_pay"),
            func.sum(PayrollRecord.overtime_hours).label("overtime_hours"),
        )
        .filter(PayrollRecord.work_date.between(start_date, end_date))
        .group_by(PayrollRecord.work_date)
        .order_by(PayrollRecord.work_date)
        .all()
    )

    totals = rows_query.with_entities(
        func.coalesce(func.sum(PayrollRecord.total_pay), 0.0),
        func.coalesce(func.sum(PayrollRecord.hours_worked), 0.0),
        func.coalesce(func.sum(PayrollRecord.overtime_hours), 0.0),
        func.count(func.distinct(PayrollRecord.employee_id)),
    ).first()

    summary = {
        "total_pay": float(totals[0]),
        "total_hours": float(totals[1]),
        "overtime_hours": float(totals[2]),
        "headcount": int(totals[3]),
    }

    by_department = (
        db_row
        for db_row in (
            PayrollRecord.query.with_entities(Department.name, func.sum(PayrollRecord.total_pay))
            .join(Employee, Employee.id == PayrollRecord.employee_id)
            .join(Department, Department.id == Employee.department_id, isouter=True)
            .filter(PayrollRecord.work_date.between(start_date, end_date))
            .group_by(Department.name)
            .order_by(func.sum(PayrollRecord.total_pay).desc())
            .all()
        )
    )

    return render_template(
        "reports.html",
        active_page="reports",
        rows=rows,
        pagination=pagination,
        per_page=per_page,
        sort=sort,
        direction=direction,
        start_date=start_date,
        end_date=end_date,
        period_start=start_date,
        period_end=end_date,
        summary=summary,
        chart_labels=[row.work_date.strftime("%b %d") for row in chart_rows],
        chart_pay=[float(row.total_pay) for row in chart_rows],
        chart_ot=[float(row.overtime_hours) for row in chart_rows],
        by_department=list(by_department),
        selected_preset=preset,
        recent_salary_changes=[],
    )


@reports_bp.route("/export.csv")
@roles_required(*Role.ADMIN_LIKE)
def export_csv():
    period = request.args.get("period", "weekly")
    start_date = parse_date(request.args.get("start_date", ""))
    end_date = parse_date(request.args.get("end_date", ""))
    if start_date and end_date and start_date <= end_date:
        query = (
            PayrollRecord.query.join(Employee)
            .join(Department, isouter=True)
            .filter(PayrollRecord.work_date.between(start_date, end_date))
            .order_by(Employee.last_name, Employee.first_name)
        )
    else:
        query, start_date, end_date = fetch_report_rows(period)
        query = query.order_by(Employee.last_name, Employee.first_name)
    rows = query.all()
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


@reports_bp.route("/export.pdf")
@roles_required(*Role.ADMIN_LIKE)
def export_pdf():
    period = request.args.get("period", "weekly")
    start_date = parse_date(request.args.get("start_date", ""))
    end_date = parse_date(request.args.get("end_date", ""))
    if start_date and end_date and start_date <= end_date:
        query = (
            PayrollRecord.query.join(Employee)
            .join(Department, isouter=True)
            .filter(PayrollRecord.work_date.between(start_date, end_date))
            .order_by(PayrollRecord.work_date.desc(), Employee.last_name, Employee.first_name)
        )
    else:
        query, start_date, end_date = fetch_report_rows(period)
        query = query.order_by(PayrollRecord.work_date.desc(), Employee.last_name, Employee.first_name)

    rows = query.limit(500).all()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 0.75 * inch

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(0.75 * inch, y, "Payroll and Workforce Report")
    y -= 0.25 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(0.75 * inch, y, f"Period: {start_date} to {end_date}")
    y -= 0.35 * inch

    headers = "Date       Employee                Department           Hours   OT    Total"
    pdf.setFont("Courier-Bold", 9)
    pdf.drawString(0.75 * inch, y, headers)
    y -= 0.2 * inch
    pdf.setFont("Courier", 8)

    if not rows:
        pdf.drawString(0.75 * inch, y, "No records for the selected period.")
    else:
        for row in rows:
            line = (
                f"{row.work_date:%Y-%m-%d}  "
                f"{row.employee.full_name()[:22]:22}  "
                f"{(row.employee.department.name if row.employee.department else 'Unassigned')[:18]:18}  "
                f"{row.hours_worked:5.2f}  {row.overtime_hours:4.2f}  ${row.total_pay:8.2f}"
            )
            pdf.drawString(0.75 * inch, y, line)
            y -= 0.18 * inch
            if y < 0.75 * inch:
                pdf.showPage()
                y = height - 0.75 * inch
                pdf.setFont("Courier", 8)

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    filename = f"{period}-payroll-report.pdf"
    return Response(
        buffer.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
