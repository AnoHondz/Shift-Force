import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import click
from flask import current_app

from .extensions import db
from .models import Attendance, Department, Employee, PayrollRecord, Role, User, create_audit_log


def register_commands(app):
    @app.cli.command("correct-attendance")
    @click.argument("record_id", type=int)
    @click.option("--clock-in", required=True, help="ISO timestamp with an explicit UTC offset.")
    @click.option("--clock-out", required=True, help="ISO timestamp with an explicit UTC offset.")
    def correct_attendance(record_id, clock_in, clock_out):
        """Reconcile a shift using trusted server access, retaining an audit entry."""
        record = db.session.get(Attendance, record_id)
        if not record:
            raise click.ClickException("Attendance record not found.")
        try:
            start = datetime.fromisoformat(clock_in)
            end = datetime.fromisoformat(clock_out)
            if not start.tzinfo or not end.tzinfo:
                raise ValueError("Both timestamps need explicit UTC offsets.")
            previous = f"{record.clock_in_at} to {record.clock_out_at}"
            record.clock_in_at = start.astimezone(timezone.utc).replace(tzinfo=None)
            record.clock_out_at = None
            record.complete_shift(end)
            record.date = start.astimezone(
                ZoneInfo(current_app.config.get("WORKFORCE_TIMEZONE", "UTC"))
            ).date()
        except ValueError as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from None
        create_audit_log(
            None,
            "attendance_corrected",
            "Attendance",
            str(record.id),
            f"Shift corrected from {previous} to {record.clock_in_at} to {record.clock_out_at} UTC using CLI.",
        )
        db.session.commit()
        click.echo("Attendance corrected. Timesheet totals remain a separate reviewed workflow.")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    def create_admin(username, email, password):
        """Create an administrator explicitly; never runs during startup."""
        from .utils import valid_email, valid_password

        if not valid_password(password) or not valid_email(email) or not 3 <= len(username) <= 120:
            raise click.ClickException(
                "Use a valid email, 3–120 character username, and a 12–128 character password."
            )
        if (
            User.query.filter_by(username=username).first()
            or Employee.query.filter_by(email=email.lower()).first()
        ):
            raise click.ClickException("Username or email already exists.")
        employee = Employee(
            first_name="Local",
            last_name="Administrator",
            email=email.lower(),
            job_title="Administrator",
        )
        user = User(username=username, role=Role.ADMIN, employee=employee)
        user.set_password(password)
        db.session.add(user)
        create_audit_log(
            None, "admin_created", "User", None, "Administrator created using the CLI."
        )
        db.session.commit()
        click.echo("Administrator created.")

    @app.cli.command("set-role")
    @click.argument("username")
    @click.argument("role", type=click.Choice(Role.CHOICES))
    def set_role(username, role):
        """Assign roles using trusted server access; never public registration."""
        user = User.query.filter_by(username=username).first()
        if not user:
            raise click.ClickException("User not found.")
        create_audit_log(
            None,
            "role_updated",
            "User",
            str(user.id),
            f"Role changed from {user.role} to {role} using CLI.",
        )
        user.role = role
        db.session.commit()
        click.echo("Role updated.")

    @app.cli.command("reset-password")
    @click.argument("username")
    @click.password_option()
    def reset_password(username, password):
        """Rotate an account password through trusted server access."""
        from .utils import valid_password

        user = User.query.filter_by(username=username).first()
        if not user or not valid_password(password):
            raise click.ClickException("User must exist and password must be 12–128 characters.")
        user.set_password(password)
        create_audit_log(None, "password_reset", "User", str(user.id), "Password reset using CLI.")
        db.session.commit()
        click.echo("Password reset. Rotate FLASK_SECRET_KEY to invalidate existing sessions.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Populate an empty local database with synthetic records."""
        if current_app.config.get("APP_ENV") == "production":
            raise click.ClickException("Demo seeding is disabled in production.")
        if Employee.query.count() or User.query.count():
            raise click.ClickException(
                "Demo seeding requires an empty database; existing data is preserved."
            )
        monday = date.today() - timedelta(days=date.today().weekday() + 7)
        for username, role, first, last, department_name in [
            ("demo.admin", Role.ADMIN, "Ava", "Martinez", "Human Resources"),
            ("demo.employee", Role.EMPLOYEE, "Taylor", "Jordan", "Engineering"),
            ("demo.manager", Role.MANAGER, "Morgan", "Lee", "Operations"),
        ]:
            department = Department(name=department_name)
            employee = Employee(
                first_name=first,
                last_name=last,
                email=f"{username}@example.com",
                department=department,
                job_title=Role.label(role),
                hourly_rate=30,
            )
            user = User(username=username, role=role, employee=employee)
            password = secrets.token_urlsafe(20)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            for offset in range(5):
                db.session.add(
                    PayrollRecord.build(
                        employee, monday + timedelta(days=offset), 9, "Synthetic demo shift"
                    )
                )
            db.session.flush()
            PayrollRecord.recalculate_week(employee.id, monday)
            db.session.add(
                PayrollRecord.build(employee, date.today(), 8, "Synthetic current-day shift")
            )
            db.session.flush()
            PayrollRecord.recalculate_week(employee.id, date.today())
            click.echo(f"{username}: {password}")
        db.session.commit()
        click.echo("Synthetic demo created. Passwords were generated for this local database only.")
