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
- `0003_workplace` adds empty dated-schedule and team-chat tables, attendance edit versions, and overlap triggers for new or changed attendance/schedule records. Existing punches, employee typical-hours text, users, and passwords are retained. Typical hours are not converted into dated shifts. Existing overlapping historical punches remain stored; reconcile them before attempting changes that conflict with another punch.
- `0004_channels` creates #general and assigns every existing message to it without changing authors, text, timestamps, or IDs. It adds separate channels and protected picture attachments. No existing conversations or employee records are removed. SQLite backups include picture data.
- `0005_stores` adds an empty store registry and permits the restricted hub `owner` role. Existing users, password hashes, workplace rows, and references are retained; existing records remain in the original hub workplace and are not guessed into new locations. Store creation explicitly migrates a fresh database and creates only its owner profile/account and #general. Starting existing stores never migrates or seeds them.
- `0006_operations` adds random revocable account identities, encrypted MFA fields, single-use recovery links, and policy review evidence. Existing account passwords and workforce/financial values are preserved; old numeric session cookies become invalid and users must sign in again. The app does not automatically enroll an authenticator or invent a payroll sign-off.
- `0007_planning` adds recurring availability and versioned shift-coverage requests, a published flag defaulting to true for all existing shifts, and nullable clock-source fingerprints on payroll. Existing shifts remain visible and existing payroll retains its manual/imported origin; accounts, passwords, times, notes, amounts, statuses and versions are preserved. No shift is automatically hidden, copied, reassigned, paid or converted to payroll. Upgrade the hub and every registered store before starting the matching source; readiness now requires this revision.
- Existing payroll rows retain their original hours, totals, notes, statuses, IDs, and timestamps. Their new snapshot rates come from the employee's current stored rate, since historical rate changes were never recorded; these snapshots must not be assumed to recover historical compensation.
- Existing payroll is marked `legacy_import=True`. The original application mixed per-date entries with weekly totals. Imported rows are read-only in the browser, and a workweek containing a non-voided/non-rejected imported row cannot be recalculated automatically.
- Preflight checks refuse negative/out-of-range data, invalid dates, overlapping active leave, duplicate payroll dates, duplicate active shifts, and orphan references. Errors require deliberate reconciliation on a backed-up copy; no row is automatically removed.
- Migrations run under an explicit SQLite write transaction. Foreign keys are temporarily disabled on the migration connection for batch table copies, checked before commit, and restored afterward. This follows SQLite batch-migration requirements described in the [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/batch.html).

Migration tests cover fresh initialization, repeat upgrades, the historical schema, refusal of invalid legacy data, and refusal of destructive downgrade. A disposable copy of the actual existing eight-table database was also upgraded; all original column values and rows were preserved, and the original file remained byte-for-byte unchanged.

## Store databases

Persist the hub database and the full private `instance/stores` directory, or the absolute directory configured by `STORE_DATABASE_ROOT`. Each file uses the registry's generated `database_key`, not a business name supplied as a filesystem path. Staff records, pictures, and chat histories are in those files; a hub-only backup cannot recover them. Preserve the hub secret securely so derived store keys continue to validate sessions. Rotating that secret invalidates store cookies and pending invitations as well as hub cookies.

For an existing deployment with locations, stop all writers, use `scripts.backup_database` to create a new verified backup of the hub and **each** registered store file, and test the matching migration on disposable copies first. With `DATABASE_URL` set to one absolute database path at a time, run the existing `flask --app run db upgrade` command for the hub and each store, then verify the migration revision, integrity, foreign keys, and retained data before restarting all processes. The CLI operates on the configured database; do not assume migrating the hub also upgrades its locations. Protect the registry and files as one coordinated backup set and retain the matching source revision.

SQLite and the in-process app cache are appropriate for the verified local deployment. Deployments with many stores or multiple workers need an explicit resource/concurrency and shared-storage review. The implementation provides path-based store links, not purchased custom domains or brand authentication.

## Imported payroll reconciliation

Reconcile each legacy record against original timesheets before using it for new payroll. Determine whether its hours describe one date or an aggregate workweek; recover actual work dates and historically agreed rates rather than inferring them from today's employee record. Retain the imported source as evidence and record every adjustment.

This project does not automate that process or guess how weekly hours should be distributed. Perform and validate reconciliation on an isolated copy with a deliberate, reviewed data migration before production adoption. Do not bypass the imported-record guard merely to make a form succeed.

## Rollback

Downgrade commands intentionally refuse destructive operations. Rollback means restoring a **verified backup together with the matching previous source revision**, not dropping tables.

Stop writers, preserve a separate backup of the failed/new database, restore the verified pre-migration backup to the configured path, and run the old source. Account for any writes made since the backup before resuming service. Test integrity and representative workflows. Keep rotated secrets and passwords secure; never restore exposed credentials as part of rollback.

Never delete/reset production data, force a failed migration through, or overwrite the only backup. SQLite-specific migrations are verified here; another database engine requires a separate migration and concurrency review.

The operations upgrade was tested first on copies of all three synthetic demo databases, then applied with writers stopped and backups retained. Fingerprints of every preexisting table column matched after migration; account passwords were preserved. Independent MFA encryption keys are retained in the demo's private state.
