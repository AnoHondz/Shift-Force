from datetime import date, datetime, timedelta

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class Role:
    EXECUTIVE = "executive"
    ADMIN = "admin"
    HR = "hr"
    PAYROLL = "payroll"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    CHOICES = (EXECUTIVE, ADMIN, HR, PAYROLL, MANAGER, EMPLOYEE)
    ADMIN_LIKE = (EXECUTIVE, ADMIN, HR, PAYROLL, MANAGER)

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
    hourly_rate = db.Column(db.Float, nullable=False, default=25.0)
    overtime_multiplier = db.Column(db.Float, nullable=False, default=1.5)
    date_hired = db.Column(db.Date, nullable=False, default=date.today)
    address = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    department = db.relationship("Department", back_populates="employees", lazy="joined")
    user = db.relationship("User", back_populates="employee", uselist=False)
    payroll_records = db.relationship(
        "PayrollRecord",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )
    leave_requests = db.relationship(
        "LeaveRequest",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )
    attendance_records = db.relationship(
        "Attendance",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __repr__(self):
        return f"<Employee {self.full_name()}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=Role.EMPLOYEE)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
        cascade="all, delete-orphan",
        lazy="select",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

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
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    hours_worked = db.Column(db.Float, nullable=False)
    regular_hours = db.Column(db.Float, nullable=False)
    overtime_hours = db.Column(db.Float, nullable=False)
    total_pay = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="Pending")
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", back_populates="payroll_records", lazy="joined")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id], lazy="joined")

    STATUS_CHOICES = ("Pending", "Reviewed", "Approved", "Rejected")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "work_date", name="uq_employee_work_date"),
    )

    @staticmethod
    def calculate(hours_worked: float, hourly_rate: float, overtime_multiplier: float):
        regular_hours = min(hours_worked, 40.0)
        overtime_hours = max(hours_worked - 40.0, 0.0)
        if overtime_hours > 0:
            total_pay = (40.0 * hourly_rate) + (overtime_hours * hourly_rate * overtime_multiplier)
        else:
            total_pay = hours_worked * hourly_rate
        return round(regular_hours, 2), round(overtime_hours, 2), round(total_pay, 2)

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
        )

    def __repr__(self):
        return f"<PayrollRecord {self.employee.full_name()} {self.work_date}>"


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    request_type = db.Column(db.String(60), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="Pending")
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee", back_populates="leave_requests", lazy="joined")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id], lazy="joined")

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", back_populates="attendance_records", lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.clock_out_at is None

    def complete_shift(self, clock_out_at):
        if clock_out_at <= self.clock_in_at:
            raise ValueError("Clock out time must come after clock in time.")
        duration = clock_out_at - self.clock_in_at
        self.clock_out_at = clock_out_at
        self.hours_worked = round(duration.total_seconds() / 3600, 2)
        self.status = "Completed"

    def __repr__(self):
        return f"<Attendance {self.employee.full_name()} {self.date} {self.status}>"


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    message = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(40), nullable=False, default="info")
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="notifications", lazy="joined")

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="audit_entries", lazy="joined")

    ACTION_TYPES = [
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


def seed_demo_data():
    if User.query.filter_by(username="manager").first():
        return

    departments = {dept.name: dept for dept in Department.query.all()}
    sample_entries = [
        {
            "username": "manager",
            "role": Role.MANAGER,
            "first_name": "Ava",
            "last_name": "Martinez",
            "email": "ava.martinez@example.com",
            "job_title": "HR Manager",
            "department": departments.get("Human Resources"),
            "hourly_rate": 45.0,
            "password": "Manager2026!",
        },
        {
            "username": "jane",
            "role": Role.EMPLOYEE,
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane.doe@example.com",
            "job_title": "Software Engineer",
            "department": departments.get("Engineering"),
            "hourly_rate": 52.0,
            "password": "Employee2026!",
        },
        {
            "username": "mark",
            "role": Role.EMPLOYEE,
            "first_name": "Mark",
            "last_name": "Rivera",
            "email": "mark.rivera@example.com",
            "job_title": "Operations Specialist",
            "department": departments.get("Operations"),
            "hourly_rate": 28.0,
            "password": "Employee2026!",
        },
    ]

    demo_users = []
    for entry in sample_entries:
        employee = Employee(
            first_name=entry["first_name"],
            last_name=entry["last_name"],
            email=entry["email"],
            job_title=entry["job_title"],
            department=entry["department"],
            hourly_rate=entry["hourly_rate"],
            overtime_multiplier=1.5,
            date_hired=date.today(),
            schedule="Mon-Fri 9:00 AM - 5:00 PM",
            address="Remote office",
        )
        db.session.add(employee)
        db.session.flush()

        user = User(username=entry["username"], role=entry["role"], employee_id=employee.id)
        user.set_password(entry["password"])
        db.session.add(user)
        demo_users.append((employee, user))

    db.session.flush()

    payroll_dates = [date.today() - timedelta(days=n * 7) for n in range(3)]
    for employee, _ in demo_users:
        for work_date in payroll_dates:
            hours_worked = 42.0 if employee.job_title == "Software Engineer" else 38.0
            record = PayrollRecord.build(employee, work_date, hours_worked, "Demo payroll entry")
            db.session.add(record)
            create_audit_log(
                None,
                "payroll_created",
                "PayrollRecord",
                str(employee.id),
                f"Sample payroll record created for {employee.full_name()}.",
            )

    Attendance.query.delete()
    attendance_samples = [
        {
            "employee": demo_users[1][0],
            "clock_in_at": datetime.utcnow() - timedelta(hours=8),
            "clock_out_at": datetime.utcnow() - timedelta(hours=1),
            "hours_worked": 7.0,
            "status": "Completed",
            "date": date.today() - timedelta(days=1),
        },
        {
            "employee": demo_users[2][0],
            "clock_in_at": datetime.utcnow() - timedelta(hours=2),
            "clock_out_at": None,
            "hours_worked": 0.0,
            "status": "In Progress",
            "date": date.today(),
        },
    ]
    for sample in attendance_samples:
        attendance = Attendance(
            employee_id=sample["employee"].id,
            clock_in_at=sample["clock_in_at"],
            clock_out_at=sample["clock_out_at"],
            hours_worked=sample["hours_worked"],
            status=sample["status"],
            date=sample["date"],
        )
        db.session.add(attendance)

    leave_samples = [
        {
            "employee": demo_users[1][0],
            "request_type": "Vacation",
            "start_date": date.today() + timedelta(days=7),
            "end_date": date.today() + timedelta(days=11),
            "reason": "Project break and planning",
            "status": "Pending",
        },
        {
            "employee": demo_users[2][0],
            "request_type": "Sick Leave",
            "start_date": date.today() - timedelta(days=10),
            "end_date": date.today() - timedelta(days=8),
            "reason": "Medical appointment",
            "status": "Approved",
        },
    ]
    for sample in leave_samples:
        request = LeaveRequest(
            employee_id=sample["employee"].id,
            request_type=sample["request_type"],
            start_date=sample["start_date"],
            end_date=sample["end_date"],
            reason=sample["reason"],
            status=sample["status"],
            submitted_at=datetime.utcnow(),
            reviewed_at=datetime.utcnow() if sample["status"] != "Pending" else None,
            reviewed_by_id=demo_users[0][1].id if sample["status"] != "Pending" else None,
        )
        db.session.add(request)

    create_notification(
        demo_users[1][1],
        "Welcome to the portal",
        "Your first payroll and attendance records are ready for review.",
        "/dashboard",
    )
    create_notification(
        demo_users[2][1],
        "Leave request submitted",
        "A manager will review your upcoming leave request soon.",
        "/leave",
    )

    db.session.commit()


def initialize_default_data(app):
    default_departments = [
        "Executive",
        "Human Resources",
        "Engineering",
        "Operations",
        "Finance",
        "Customer Success",
    ]
    for name in default_departments:
        if not Department.query.filter_by(name=name).first():
            db.session.add(Department(name=name))
    db.session.commit()

    admin_username = app.config.get("DEFAULT_ADMIN_USERNAME", "admin")
    admin_password = app.config.get("DEFAULT_ADMIN_PASSWORD", "Admin2026!")
    admin_email = app.config.get("DEFAULT_ADMIN_EMAIL", "admin@workforce.local")
    if not User.query.filter_by(username=admin_username).first():
        employee = Employee(
            first_name="System",
            last_name="Administrator",
            email=admin_email,
            job_title="Executive Administrator",
            department=Department.query.filter_by(name="Executive").first(),
            hourly_rate=60.0,
            overtime_multiplier=1.5,
            date_hired=date.today(),
            schedule="Mon-Fri 9:00 AM - 5:00 PM",
        )
        db.session.add(employee)
        db.session.flush()

        admin_user = User(username=admin_username, role=Role.ADMIN, employee_id=employee.id)
        admin_user.set_password(admin_password)
        db.session.add(admin_user)
        db.session.commit()

    seed_demo_data()
