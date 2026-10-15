"""Add private planning, self-service coverage and traceable punch-derived timesheets."""

import sqlalchemy as sa
from alembic import op

revision = "0007_planning"
down_revision = "0006_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "scheduled_shifts",
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "payroll_records", sa.Column("attendance_fingerprint", sa.String(64), nullable=True)
    )
    op.create_table(
        "availability_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False, unique=True
        ),
        sa.Column("rules", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "shift_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("scheduled_shifts.id"), nullable=False),
        sa.Column("offered_by_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("claimed_by_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("shift_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="Open"),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint(
            "status IN ('Open','Pending','Approved','Cancelled')", name="ck_coverage_status"
        ),
        sa.CheckConstraint(
            "claimed_by_id IS NULL OR claimed_by_id != offered_by_id", name="ck_coverage_people"
        ),
    )
    op.create_index(
        "uq_coverage_active_shift",
        "shift_coverage",
        ["shift_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('Open','Pending')"),
    )


def downgrade():
    raise RuntimeError(
        "Restore verified backups and matching source; planning and payroll provenance are retained."
    )
