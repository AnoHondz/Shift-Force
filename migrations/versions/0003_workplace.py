"""Add dated schedules, shared team chat, and attendance edit versions."""

import sqlalchemy as sa
from alembic import op

from app.integrity import shift_overlap_sql

revision = "0003_workplace"
down_revision = "0002_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attendance_records",
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "scheduled_shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("notes", sa.String(255), nullable=False),
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_schedule_times"),
    )
    op.create_index("ix_schedule_employee_start", "scheduled_shifts", ["employee_id", "starts_at"])
    op.create_table(
        "team_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 2000", name="ck_message_length"),
    )
    for table_name in ("attendance_records", "scheduled_shifts"):
        for operation in ("INSERT", "UPDATE"):
            op.execute(shift_overlap_sql(table_name, operation))


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting workplace records.")
