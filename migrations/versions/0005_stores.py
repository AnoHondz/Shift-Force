"""Add the owner registry without changing existing workplace records."""

import sqlalchemy as sa
from alembic import op

revision = "0005_stores"
down_revision = "0004_channels"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", recreate="always") as batch:
        batch.drop_constraint("ck_user_role", type_="check")
        batch.create_check_constraint(
            "ck_user_role",
            "role IN ('executive','admin','hr','payroll','manager','employee','owner')",
        )
    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("location", sa.String(160), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("database_key", sa.String(32), nullable=False, unique=True),
        sa.Column("owner_account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_store_owner", "stores", ["owner_id"])


def downgrade():
    raise RuntimeError("Restore verified hub and store backups instead of deleting locations.")
