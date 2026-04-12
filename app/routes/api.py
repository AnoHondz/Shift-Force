from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from ..models import Attendance, Employee, PayrollRecord
from ..utils import parse_positive_int

api_bp = Blueprint("api", __name__, url_prefix="/api")


def serialize_employee(employee):
    return {
        "id": employee.id,
        "name": employee.full_name(),
        "email": employee.email,
        "job_title": employee.job_title,
        "department": employee.department.name if employee.department else None,
        "hourly_rate": float(employee.hourly_rate),
        "overtime_multiplier": float(employee.overtime_multiplier),
        "schedule": employee.schedule,
    }


@api_bp.route("/employees")
@login_required
def employee_list():
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=25, maximum=100)
    query = Employee.query.options(joinedload(Employee.department))
    if current_user.is_employee():
        query = query.filter_by(id=current_user.employee_id)

    pagination = query.order_by(Employee.last_name, Employee.first_name).paginate(page=page, per_page=per_page, error_out=False)
    employees = [serialize_employee(emp) for emp in pagination.items]
    return jsonify({"employees": employees, "page": pagination.page, "pages": pagination.pages, "total": pagination.total})


@api_bp.route("/payroll")
@login_required
def payroll_list():
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=25, maximum=100)
    query = PayrollRecord.query.options(joinedload(PayrollRecord.employee).joinedload(Employee.department))
    if current_user.is_employee():
        query = query.filter_by(employee_id=current_user.employee_id)

    pagination = query.order_by(PayrollRecord.work_date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    records = [
        {
            "id": record.id,
            "employee": record.employee.full_name(),
            "department": record.employee.department.name if record.employee.department else None,
            "work_date": record.work_date.isoformat(),
            "hours_worked": float(record.hours_worked),
            "regular_hours": float(record.regular_hours),
            "overtime_hours": float(record.overtime_hours),
            "total_pay": float(record.total_pay),
            "notes": record.notes,
        }
        for record in pagination.items
    ]
    return jsonify({"payroll_records": records, "page": pagination.page, "pages": pagination.pages, "total": pagination.total})


@api_bp.route("/attendance")
@login_required
def attendance_list():
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=25, maximum=100)
    query = Attendance.query.options(joinedload(Attendance.employee).joinedload(Employee.department))
    if current_user.is_employee():
        query = query.filter_by(employee_id=current_user.employee_id)

    pagination = query.order_by(Attendance.date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    records = [
        {
            "id": record.id,
            "employee": record.employee.full_name(),
            "department": record.employee.department.name if record.employee.department else None,
            "date": record.date.isoformat(),
            "clock_in_at": record.clock_in_at.isoformat(),
            "clock_out_at": record.clock_out_at.isoformat() if record.clock_out_at else None,
            "hours_worked": float(record.hours_worked),
            "status": record.status,
        }
        for record in pagination.items
    ]
    return jsonify({"attendance_records": records, "page": pagination.page, "pages": pagination.pages, "total": pagination.total})
