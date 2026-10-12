from datetime import date, datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .integrity import register_leave_constraints, register_shift_constraints
from .payroll import Workweek, calculate_pay


def utcnow():
    """SQLite stores UTC without an offset; normalize at the boundary."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role:
    EXECUTIVE = "executive"
    ADMIN = "admin"
    HR = "hr"
    PAYROLL = "payroll"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    CHOICES = (EXECUTIVE, ADMIN, HR, PAYROLL, MANAGER, EMPLOYEE)
    ADMIN_LIKE = (EXECUTIVE, ADMIN, HR, PAYROLL, MANAGER)
    PEOPLE_EDIT = (EXECUTIVE, ADMIN, HR)
    PAYROLL_REVIEW = (EXECUTIVE, ADMIN, PAYROLL, MANAGER)
    LEAVE_REVIEW = (EXECUTIVE, ADMIN, HR, MANAGER)

    @classmethod
    def label(cls, role: str) -> str:
        mapping = {
            cls.EXECUTIVE: "Executive",
            cls.ADMIN: "Administrator",
            cls.HR: "HR",
            cls.PAYROLL: "Payroll Admin",
            cls.MANAGER: "Manager",
            cls.EMPLOYEE: "Employee",
        }
        if not role:
            return "Unknown"
        return mapping.get(role, role.title())


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)

    employees = db.relationship("Employee", back_populates="department", lazy="select")

    def __repr__(self):
        return f"<Department {self.name}>"


class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    job_title = db.Column(db.String(120), nullable=False, default="Employee")
    schedule = db.Column(db.String(64), nullable=False, default="Mon-Fri 9:00 AM - 5:00 PM")
    hourly_rate = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("25"))
    overtime_multiplier = db.Column(db.Numeric(4, 2), nullable=False, default=Decimal("1.5"))
    archived = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    date_hired = db.Column(db.Date, nullable=False, default=date.today)
    address = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    department = db.relationship("Department", back_populates="employees", lazy="joined")
    user = db.relationship("User", back_populates="employee", uselist=False)
    payroll_records = db.relationship(
        "PayrollRecord",
        back_populates="employee",
        lazy="select",
    )
    leave_requests = db.relationship(
        "LeaveRequest",
        back_populates="employee",
        lazy="select",
    )
    attendance_records = db.relationship(
        "Attendance",
        back_populates="employee",
        lazy="select",
    )

    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def workforce_status(self, today):
        if self.archived:
            return "archived"
        if any(
            item.status == "Approved" and item.start_date <= today <= item.end_date
            for item in self.leave_requests
        ):
            return "on_leave"
        return "probation" if (today - self.date_hired).days <= 90 else "active"

    __table_args__ = (
        db.CheckConstraint("hourly_rate >= 0 AND hourly_rate <= 10000", name="ck_employee_rate"),
        db.CheckConstraint(
            "overtime_multiplier >= 1 AND overtime_multiplier <= 5", name="ck_employee_multiplier"
        ),
        db.Index("ix_employee_department", "department_id"),
    )

    def __repr__(self):
        return f"<Employee {self.full_name()}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=Role.EMPLOYEE)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    employee = db.relationship("Employee", back_populates="user", lazy="joined")
    notifications = db.relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    audit_entries = db.relationship(
        "AuditLog",
        back_populates="user",
        lazy="select",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    @property
    def is_active(self):
        return not self.employee or not self.employee.archived

    __table_args__ = (
        db.CheckConstraint(
            "role IN ('executive','admin','hr','payroll','manager','employee')", name="ck_user_role"
        ),
    )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    def is_executive(self) -> bool:
        return self.role == Role.EXECUTIVE

    def is_hr(self) -> bool:
        return self.role == Role.HR

    def is_payroll_admin(self) -> bool:
        return self.role == Role.PAYROLL

    def is_manager(self) -> bool:
        return self.role == Role.MANAGER

    def is_employee(self) -> bool:
        return self.role == Role.EMPLOYEE

    def has_admin_access(self) -> bool:
        return self.role in Role.ADMIN_LIKE

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


class PayrollRecord(db.Model):
    __tablename__ = "payroll_records"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version_id}
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    hours_worked = db.Column(db.Numeric(6, 2), nullable=False)
    regular_hours = db.Column(db.Numeric(6, 2), nullable=False)
    overtime_hours = db.Column(db.Numeric(6, 2), nullable=False)
    total_pay = db.Column(db.Numeric(14, 2), nullable=False)
    rate_snapshot = db.Column(db.Numeric(12, 2), nullable=False)
    multiplier_snapshot = db.Column(db.Numeric(4, 2), nullable=False)
    legacy_import = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="Pending")
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    employee = db.relationship("Employee", back_populates="payroll_records", lazy="joined")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id], lazy="joined")

    STATUS_CHOICES = ("Pending", "Reviewed", "Approved", "Rejected", "Voided")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "work_date", name="uq_employee_work_date"),
        db.CheckConstraint(
            "hours_worked >= 0 AND hours_worked <= 168 AND (legacy_import OR hours_worked <= 24)",
            name="ck_payroll_hours",
        ),
        db.CheckConstraint(
            "status IN ('Pending','Reviewed','Approved','Rejected','Voided')",
            name="ck_payroll_status",
        ),
        db.CheckConstraint(
            "regular_hours >= 0 AND overtime_hours >= 0 AND total_pay >= 0",
            name="ck_payroll_totals",
        ),
        db.Index("ix_payroll_date", "work_date"),
    )

    @staticmethod
    def calculate(hours_worked, hourly_rate, overtime_multiplier, prior_hours=0):
        return calculate_pay(hours_worked, hourly_rate, overtime_multiplier, prior_hours)

    @classmethod
    def build(cls, employee: "Employee", work_date: date, hours_worked: float, notes: str = ""):
        regular_hours, overtime_hours, total_pay = cls.calculate(
            hours_worked, employee.hourly_rate, employee.overtime_multiplier
        )
        return cls(
            employee=employee,
            work_date=work_date,
            hours_worked=hours_worked,
            regular_hours=regular_hours,
            overtime_hours=overtime_hours,
            total_pay=total_pay,
            notes=notes,
            rate_snapshot=employee.hourly_rate,
            multiplier_snapshot=employee.overtime_multiplier,
        )

    @classmethod
    def recalculate_week(cls, employee_id, work_date):
        week = Workweek.containing(work_date)
        records = (
            cls.query.filter(
                cls.employee_id == employee_id,
                cls.work_date.between(week.start, week.end),
                cls.status.notin_(["Rejected", "Voided"]),
            )
            .order_by(cls.work_date, cls.id)
            .all()
        )
        if any(record.legacy_import for record in records):
            raise ValueError(
                "This week contains imported records with ambiguous daily/weekly hours. Reconcile them before editing this week."
            )
        prior = Decimal("0")
        for record in records:
            amounts = cls.calculate(
                record.hours_worked, record.rate_snapshot, record.multiplier_snapshot, prior
            )
            if (record.regular_hours, record.overtime_hours, record.total_pay) != amounts:
                record.regular_hours, record.overtime_hours, record.total_pay = amounts
                record.status = "Pending"
                record.reviewed_by_id = None
                record.reviewed_at = None
            prior += Decimal(str(record.hours_worked))

    def __repr__(self):
        return f"<PayrollRecord {self.employee.full_name()} {self.work_date}>"


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version_id}
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    request_type = db.Column(db.String(60), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="Pending")
    submitted_at = db.Column(db.DateTime, default=utcnow)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee", back_populates="leave_requests", lazy="joined")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id], lazy="joined")

    __table_args__ = (
        db.CheckConstraint("end_date >= start_date", name="ck_leave_dates"),
        db.CheckConstraint("status IN ('Pending','Approved','Rejected')", name="ck_leave_status"),
        db.Index("ix_leave_employee_dates", "employee_id", "start_date", "end_date"),
    )

    def __repr__(self):
        return f"<LeaveRequest {self.employee.full_name()} {self.status}>"


class Attendance(db.Model):
    __tablename__ = "attendance_records"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    clock_in_at = db.Column(db.DateTime, nullable=False)
    clock_out_at = db.Column(db.DateTime, nullable=True)
    hours_worked = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(32), nullable=False, default="In Progress")
    date = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.DateTime, default=utcnow)
    version_id = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version_id}

    employee = db.relationship("Employee", back_populates="attendance_records", lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.clock_out_at is None

    def complete_shift(self, clock_out_at):
        if self.clock_out_at is not None:
            raise ValueError("This shift is already completed.")
        if clock_out_at.tzinfo:
            clock_out_at = clock_out_at.astimezone(timezone.utc).replace(tzinfo=None)
        clock_in = self.clock_in_at
        if clock_in.tzinfo:
            clock_in = clock_in.astimezone(timezone.utc).replace(tzinfo=None)
        if clock_out_at <= clock_in:
            raise ValueError("Clock out time must come after clock in time.")
        duration = clock_out_at - clock_in
        if duration.total_seconds() > 24 * 3600:
            raise ValueError(
                "Shifts cannot exceed 24 hours. Contact an administrator to reconcile this shift."
            )
        self.clock_out_at = clock_out_at
        self.hours_worked = round(duration.total_seconds() / 3600, 2)
        self.status = "Completed"

    __table_args__ = (
        db.CheckConstraint(
            "clock_out_at IS NULL OR clock_out_at > clock_in_at", name="ck_attendance_times"
        ),
        db.CheckConstraint("hours_worked >= 0 AND hours_worked <= 24", name="ck_attendance_hours"),
        db.Index(
            "uq_attendance_active",
            "employee_id",
            unique=True,
            sqlite_where=db.text("clock_out_at IS NULL"),
        ),
        db.Index("ix_attendance_date", "date"),
    )

    def __repr__(self):
        return f"<Attendance {self.employee.full_name()} {self.date} {self.status}>"


class ScheduledShift(db.Model):
    __tablename__ = "scheduled_shifts"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.String(255), nullable=False, default="")
    cancelled = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    version_id = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version_id}
    employee = db.relationship("Employee", lazy="joined")
    __table_args__ = (
        db.CheckConstraint("ends_at > starts_at", name="ck_schedule_times"),
        db.Index("ix_schedule_employee_start", "employee_id", "starts_at"),
    )


class TeamChannel(db.Model):
    __tablename__ = "team_channels"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False, unique=True)
    topic = db.Column(db.String(160), nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class TeamMessage(db.Model):
    __tablename__ = "team_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    channel_id = db.Column(
        db.Integer, db.ForeignKey("team_channels.id"), nullable=False, default=1, server_default="1"
    )
    body = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    user = db.relationship("User", lazy="joined")
    channel = db.relationship("TeamChannel", lazy="joined")
    attachment = db.relationship(
        "ChatAttachment", uselist=False, back_populates="message", lazy="joined"
    )
    __table_args__ = (
        db.CheckConstraint("length(body) BETWEEN 0 AND 2000", name="ck_message_length"),
        db.Index("ix_message_channel_id", "channel_id", "id"),
    )


class ChatAttachment(db.Model):
    __tablename__ = "chat_attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("team_messages.id"), nullable=False, unique=True
    )
    filename = db.Column(db.String(160), nullable=False)
    mime_type = db.Column(db.String(40), nullable=False)
    data = db.deferred(db.Column(db.LargeBinary, nullable=False))
    message = db.relationship("TeamMessage", back_populates="attachment")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    message = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(40), nullable=False, default="info")
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", back_populates="notifications", lazy="joined")
    __table_args__ = (db.Index("ix_notification_inbox", "user_id", "is_read", "created_at"),)

    def __repr__(self):
        return f"<Notification {self.title} for {self.user.username}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action_type = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(80), nullable=True)
    target_id = db.Column(db.String(80), nullable=True)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", back_populates="audit_entries", lazy="joined")
    __table_args__ = (db.Index("ix_audit_created", "created_at"),)

    ACTION_TYPES = [
        "admin_created",
        "role_updated",
        "password_reset",
        "attendance_corrected",
        "schedule_created",
        "schedule_updated",
        "schedule_cancelled",
        "chat_channel_created",
        "employee_created",
        "employee_updated",
        "employee_deleted",
        "payroll_created",
        "payroll_reviewed",
        "payroll_deleted",
        "leave_submitted",
        "leave_reviewed",
        "profile_updated",
        "attendance_clocked_in",
        "attendance_clocked_out",
        "login_failed",
        "login_success",
        "logout",
    ]

    def __repr__(self):
        return f"<AuditLog {self.action_type} by {self.user.username if self.user else 'System'}>"


def create_notification(user, title, message, url=None, category="info"):
    notification = Notification(
        user_id=user.id,
        title=title,
        message=message,
        url=url,
        category=category,
    )
    db.session.add(notification)
    return notification


def create_audit_log(user, action_type, target_type, target_id, description):
    audit_entry = AuditLog(
        user_id=user.id if user else None,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        description=description,
    )
    db.session.add(audit_entry)
    return audit_entry


register_leave_constraints(LeaveRequest.__table__)
register_shift_constraints(Attendance.__table__)
register_shift_constraints(ScheduledShift.__table__)
