"""Add revocable authentication, encrypted MFA, recovery links and operator sign-offs."""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "0006_operations"
down_revision = "0005_stores"
branch_labels = None
depends_on = None


def upgrade():
    # Additive migration preserves all existing account/profile/payroll values.
    op.add_column("users", sa.Column("auth_token", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("mfa_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_pending_secret", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("mfa_last_step", sa.BigInteger(), nullable=False, server_default="-1")
    )
    op.add_column("users", sa.Column("mfa_recovery_hashes", sa.Text(), nullable=True))
    connection = op.get_bind()
    for user_id in connection.execute(sa.text("SELECT id FROM users")).scalars().all():
        connection.execute(
            sa.text("UPDATE users SET auth_token=:token WHERE id=:id"),
            {"id": user_id, "token": secrets.token_urlsafe(32)},
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("auth_token", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint("uq_users_auth_token", ["auth_token"])
    op.create_table(
        "account_resets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("auth_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "operational_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_type", sa.String(40), nullable=False),
        sa.Column("reference", sa.String(255), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_operational_review_type", "operational_reviews", ["review_type"])


def downgrade():
    raise RuntimeError(
        "Restore verified backups and matching source; authentication history is retained."
    )
