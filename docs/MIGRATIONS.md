# Database migrations, backups, and rollback

Application startup does not create tables, alter columns, or seed data. Use the versioned Alembic migrations through Flask-Migrate.

## Fresh installation

Configure a generated `FLASK_SECRET_KEY`, then run:

```text
python -m flask --app run db upgrade
python -m flask --app run db current
```

The default database is `instance/workforce.db`. `schema.sql` is historical test input, not the current initialization mechanism. Do not run it against a new deployment.

## Existing installation

1. Stop application writers and keep the current source revision available for rollback. Confirm the exact database path rather than relying on an old working directory.
2. Create a **new** backup file using SQLite's backup API:

   ```text
   python -m scripts.backup_database employee_mgmt.db backups/pre-readiness.db
   ```

   The script opens the source read-only, refuses to overwrite a destination, includes committed WAL data, and runs `PRAGMA integrity_check` on the backup. Protect backups as sensitive data; the directory is ignored by Git. Store an additional verified copy outside the application host.

3. Test migration on a disposable copy of that backup before touching the live database. Set `DATABASE_URL` to an **absolute** SQLite URL. For Windows, for example `sqlite:///C:/path/to/copy.db`; for Linux `sqlite:////absolute/path/copy.db`.
4. With writers stopped and the intended `DATABASE_URL` set, run `python -m flask --app run db upgrade`. Inspect `db current`, row counts, representative historical records, and foreign-key integrity.
5. Rotate credentials as described in [SECURITY.md](SECURITY.md), then start the matching application revision. Verify login and core workflows before restoring access.

Do not stamp an unverified database as current and do not call `db.create_all()` as an upgrade mechanism.

## Migration behavior

- `0001_baseline` creates missing baseline tables and adds the old schedule/payroll workflow columns where absent. It supports the repository's historical `schema.sql` and its pre-readiness ORM schema.
- `0002_integrity` uses SQLite batch migrations for numeric types, preserved legacy payroll flags/rate snapshots, employee archival, optimistic versions, checks, indexes, missing reviewer foreign keys, and leave overlap triggers.
- Existing payroll rows retain their original hours, totals, notes, statuses, IDs, and timestamps. Their new snapshot rates come from the employee's current stored rate, since historical rate changes were never recorded; these snapshots must not be assumed to recover historical compensation.
- Existing payroll is marked `legacy_import=True`. The original application mixed per-date entries with weekly totals. Imported rows are read-only in the browser, and a workweek containing a non-voided/non-rejected imported row cannot be recalculated automatically.
- Preflight checks refuse negative/out-of-range data, invalid dates, overlapping active leave, duplicate payroll dates, duplicate active shifts, and orphan references. Errors require deliberate reconciliation on a backed-up copy; no row is automatically removed.
- Migrations run under an explicit SQLite write transaction. Foreign keys are temporarily disabled on the migration connection for batch table copies, checked before commit, and restored afterward. This follows SQLite batch-migration requirements described in the [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/batch.html).

Migration tests cover fresh initialization, repeat upgrades, the historical schema, refusal of invalid legacy data, and refusal of destructive downgrade. A disposable copy of the actual existing eight-table database was also upgraded; all original column values and rows were preserved, and the original file remained byte-for-byte unchanged.

## Imported payroll reconciliation

Reconcile each legacy record against original timesheets before using it for new payroll. Determine whether its hours describe one date or an aggregate workweek; recover actual work dates and historically agreed rates rather than inferring them from today's employee record. Retain the imported source as evidence and record every adjustment.

This project does not automate that process or guess how weekly hours should be distributed. Perform and validate reconciliation on an isolated copy with a deliberate, reviewed data migration before production adoption. Do not bypass the imported-record guard merely to make a form succeed.

## Rollback

Downgrade commands intentionally refuse destructive operations. Rollback means restoring a **verified backup together with the matching previous source revision**, not dropping tables.

Stop writers, preserve a separate backup of the failed/new database, restore the verified pre-migration backup to the configured path, and run the old source. Account for any writes made since the backup before resuming service. Test integrity and representative workflows. Keep rotated secrets and passwords secure; never restore exposed credentials as part of rollback.

Never delete/reset production data, force a failed migration through, or overwrite the only backup. SQLite-specific migrations are verified here; another database engine requires a separate migration and concurrency review.
