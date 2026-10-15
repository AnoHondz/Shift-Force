"""Preserve the shared conversation in general; add channels and protected pictures."""

import sqlalchemy as sa
from alembic import op

revision = "0004_channels"
down_revision = "0003_workplace"
branch_labels = None
depends_on = None


def upgrade():
    channels = op.create_table(
        "team_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("topic", sa.String(160), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.get_bind().execute(
        channels.insert().values(
            id=1,
            name="general",
            topic="The place for everyone to catch up.",
            created_at=sa.func.current_timestamp(),
        )
    )
    with op.batch_alter_table("team_messages") as batch:
        batch.add_column(sa.Column("channel_id", sa.Integer(), nullable=False, server_default="1"))
        batch.create_foreign_key("fk_message_channel", "team_channels", ["channel_id"], ["id"])
        batch.drop_constraint("ck_message_length", type_="check")
        batch.create_check_constraint("ck_message_length", "length(body) BETWEEN 0 AND 2000")
        batch.create_index("ix_message_channel_id", ["channel_id", "id"])
    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("team_messages.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("filename", sa.String(160), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting chats or pictures.")
