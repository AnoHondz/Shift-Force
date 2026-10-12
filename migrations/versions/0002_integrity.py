"""Preserve records, snapshot legacy rates, add decimal types and integrity checks."""

import sqlalchemy as sa
from alembic import op

revision = "0002_integrity"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Refuse invalid legacy data instead of deleting or silently repairing it.
    checks = [
        "SELECT id FROM employees WHERE hourly_rate < 0 OR hourly_rate > 10000 OR overtime_multiplier < 1 OR overtime_multiplier > 5",
        "SELECT id FROM payroll_records WHERE hours_worked < 0 OR hours_worked > 168 OR regular_hours < 0 OR overtime_hours < 0 OR total_pay < 0 OR status NOT IN ('Pending','Reviewed','Approved','Rejected')",
        "SELECT id FROM leave_requests WHERE end_date < start_date OR status NOT IN ('Pending','Approved','Rejected')",
        "SELECT id FROM attendance_records WHERE (clock_out_at IS NOT NULL AND clock_out_at <= clock_in_at) OR hours_worked < 0 OR hours_worked > 24",
        "SELECT employee_id FROM attendance_records WHERE clock_out_at IS NULL GROUP BY employee_id HAVING COUNT(*) > 1",
        "SELECT employee_id FROM payroll_records GROUP BY employee_id, work_date HAVING COUNT(*) > 1",
        "SELECT a.id FROM leave_requests a JOIN leave_requests b ON a.employee_id=b.employee_id AND a.id<b.id WHERE a.status IN ('Pending','Approved') AND b.status IN ('Pending','Approved') AND a.start_date<=b.end_date AND a.end_date>=b.start_date",
    ]
    if connection.execute(sa.text("PRAGMA foreign_key_check")).fetchall():
        raise RuntimeError(
            "Legacy database has orphan references. Reconcile using a backup before migration."
        )
    for query in checks:
        if connection.execute(sa.text(query)).first():
            raise RuntimeError(
                "Legacy data fails integrity checks; migration stopped without removing records. Reconcile using a backup."
            )

    with op.batch_alter_table("employees") as batch:
        batch.add_column(
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.alter_column("hourly_rate", existing_type=sa.Float(), type_=sa.Numeric(12, 2))
        batch.alter_column("overtime_multiplier", existing_type=sa.Float(), type_=sa.Numeric(4, 2))
        batch.create_check_constraint(
            "ck_employee_rate", "hourly_rate >= 0 AND hourly_rate <= 10000"
        )
        batch.create_check_constraint(
            "ck_employee_multiplier", "overtime_multiplier >= 1 AND overtime_multiplier <= 5"
        )
        batch.create_index("ix_employee_department", ["department_id"])

    # The old schema.sql role check had no name; reflection assigns one for removal.
    naming = {"ck": "ck_legacy_%(table_name)s"}
    checks = sa.inspect(connection).get_check_constraints("users")
    with op.batch_alter_table("users", naming_convention=naming) as batch:
        for check in checks:
            batch.drop_constraint(check["name"] or "ck_legacy_users", type_="check")
        batch.create_check_constraint(
            "ck_user_role", "role IN ('executive','admin','hr','payroll','manager','employee')"
        )

    with op.batch_alter_table("payroll_records") as batch:
        batch.add_column(sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"))
        if not any(
            "reviewed_by_id" in foreign_key["constrained_columns"]
            for foreign_key in sa.inspect(connection).get_foreign_keys("payroll_records")
        ):
            batch.create_foreign_key("fk_payroll_reviewer", "users", ["reviewed_by_id"], ["id"])
        for field in ("hours_worked", "regular_hours", "overtime_hours"):
            batch.alter_column(field, existing_type=sa.Float(), type_=sa.Numeric(6, 2))
        batch.alter_column("total_pay", existing_type=sa.Float(), type_=sa.Numeric(14, 2))
        batch.add_column(
            sa.Column("rate_snapshot", sa.Numeric(12, 2), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("multiplier_snapshot", sa.Numeric(4, 2), nullable=False, server_default="1.5")
        )
        batch.add_column(
            sa.Column("legacy_import", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.create_check_constraint(
            "ck_payroll_hours",
            "hours_worked >= 0 AND hours_worked <= 168 AND (legacy_import OR hours_worked <= 24)",
        )
        batch.create_check_constraint(
            "ck_payroll_status", "status IN ('Pending','Reviewed','Approved','Rejected','Voided')"
        )
        batch.create_check_constraint(
            "ck_payroll_totals", "regular_hours >= 0 AND overtime_hours >= 0 AND total_pay >= 0"
        )
        batch.create_index("ix_payroll_date", ["work_date"])
    connection.execute(
        sa.text(
            "UPDATE payroll_records SET rate_snapshot=(SELECT hourly_rate FROM employees WHERE employees.id=payroll_records.employee_id), multiplier_snapshot=(SELECT overtime_multiplier FROM employees WHERE employees.id=payroll_records.employee_id)"
        )
    )
    with op.batch_alter_table("payroll_records") as batch:
        batch.alter_column("legacy_import", server_default=sa.false(), existing_type=sa.Boolean())
        batch.alter_column("rate_snapshot", server_default=None, existing_type=sa.Numeric(12, 2))
        batch.alter_column(
            "multiplier_snapshot", server_default=None, existing_type=sa.Numeric(4, 2)
        )

    with op.batch_alter_table("leave_requests") as batch:
        batch.add_column(sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("ck_leave_dates", "end_date >= start_date")
        batch.create_check_constraint(
            "ck_leave_status", "status IN ('Pending','Approved','Rejected')"
        )
        batch.create_index("ix_leave_employee_dates", ["employee_id", "start_date", "end_date"])
    with op.batch_alter_table("attendance_records") as batch:
        batch.create_check_constraint(
            "ck_attendance_times", "clock_out_at IS NULL OR clock_out_at > clock_in_at"
        )
        batch.create_check_constraint(
            "ck_attendance_hours", "hours_worked >= 0 AND hours_worked <= 24"
        )
        batch.create_index(
            "uq_attendance_active",
            ["employee_id"],
            unique=True,
            sqlite_where=sa.text("clock_out_at IS NULL"),
        )
        batch.create_index("ix_attendance_date", ["date"])
    op.create_index("ix_notification_inbox", "notifications", ["user_id", "is_read", "created_at"])
    op.create_index("ix_audit_created", "audit_logs", ["created_at"])
    for operation in ("INSERT", "UPDATE"):
        op.execute(f"""CREATE TRIGGER leave_no_overlap_{operation.lower()}
            BEFORE {operation} ON leave_requests
            WHEN NEW.status IN ('Pending', 'Approved')
            BEGIN
                SELECT RAISE(ABORT, 'Leave dates overlap an existing request')
                WHERE EXISTS (SELECT 1 FROM leave_requests existing
                    WHERE existing.employee_id = NEW.employee_id
                      AND existing.id != NEW.id
                      AND existing.status IN ('Pending', 'Approved')
                      AND existing.start_date <= NEW.end_date
                      AND existing.end_date >= NEW.start_date);
            END""")


def downgrade():
    raise RuntimeError(
        "This migration retains financial history. Roll back by restoring a verified backup and the previous code version."
    )
