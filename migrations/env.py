"""SQLite migrations use batch copies with FK verification, never startup DDL."""

from logging.config import fileConfig

from alembic import context
from flask import current_app
from sqlalchemy import text

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
db = current_app.extensions["migrate"].db
target_metadata = db.metadata

if context.is_offline_mode():
    raise RuntimeError(
        "Offline migrations are not supported; run against a backed-up SQLite database."
    )

with db.engine.connect() as connection:
    if connection.dialect.name != "sqlite":
        raise RuntimeError("These migrations are verified for SQLite only.")
    connection.execute(text("PRAGMA foreign_keys=OFF"))
    connection.commit()
    try:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()
            if connection.execute(text("PRAGMA foreign_key_check")).fetchall():
                raise RuntimeError(
                    "Foreign key validation failed. Restore backup and reconcile orphan records."
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.commit()
