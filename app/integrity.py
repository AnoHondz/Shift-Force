"""SQLite overlap constraints supplement application validation under concurrent writes."""

from sqlalchemy import DDL, event


def leave_overlap_sql(operation):
    if operation not in {"INSERT", "UPDATE"}:
        raise ValueError("Unsupported trigger operation.")
    insert_sql = """CREATE TRIGGER leave_no_overlap_insert
    BEFORE INSERT ON leave_requests
    WHEN NEW.status IN ('Pending', 'Approved')
    BEGIN
        SELECT RAISE(ABORT, 'Leave dates overlap an existing request')
        WHERE EXISTS (
            SELECT 1 FROM leave_requests existing
            WHERE existing.employee_id = NEW.employee_id
              AND existing.id != NEW.id
              AND existing.status IN ('Pending', 'Approved')
              AND existing.start_date <= NEW.end_date
              AND existing.end_date >= NEW.start_date
        );
    END"""
    return (
        insert_sql
        if operation == "INSERT"
        else insert_sql.replace("leave_no_overlap_insert", "leave_no_overlap_update").replace(
            "BEFORE INSERT", "BEFORE UPDATE"
        )
    )


def register_leave_constraints(table):
    for operation in ("INSERT", "UPDATE"):
        event.listen(
            table, "after_create", DDL(leave_overlap_sql(operation)).execute_if(dialect="sqlite")
        )


def shift_overlap_sql(table_name, operation):
    if table_name not in {"scheduled_shifts", "attendance_records"}:
        raise ValueError("Unsupported shift table.")
    if operation not in {"INSERT", "UPDATE"}:
        raise ValueError("Unsupported trigger operation.")
    if table_name == "scheduled_shifts":
        insert_sql = """CREATE TRIGGER scheduled_shifts_no_overlap_insert
        BEFORE INSERT ON scheduled_shifts WHEN NEW.cancelled = 0
        BEGIN
            SELECT RAISE(ABORT, 'Shift overlaps an existing record')
            WHERE EXISTS (
                SELECT 1 FROM scheduled_shifts existing
                WHERE existing.employee_id = NEW.employee_id AND existing.id != NEW.id
                AND existing.cancelled = 0
                AND existing.starts_at < NEW.ends_at
                AND existing.ends_at > NEW.starts_at
            );
        END"""
    else:
        insert_sql = """CREATE TRIGGER attendance_records_no_overlap_insert
        BEFORE INSERT ON attendance_records
        BEGIN
            SELECT RAISE(ABORT, 'Shift overlaps an existing record')
            WHERE EXISTS (
                SELECT 1 FROM attendance_records existing
                WHERE existing.employee_id = NEW.employee_id AND existing.id != NEW.id
                AND (NEW.clock_out_at IS NULL OR existing.clock_in_at < NEW.clock_out_at)
                AND (existing.clock_out_at IS NULL OR existing.clock_out_at > NEW.clock_in_at)
            );
        END"""
    return (
        insert_sql
        if operation == "INSERT"
        else insert_sql.replace("_no_overlap_insert", "_no_overlap_update").replace(
            "BEFORE INSERT", "BEFORE UPDATE"
        )
    )


def register_shift_constraints(table):
    for operation in ("INSERT", "UPDATE"):
        event.listen(
            table,
            "after_create",
            DDL(shift_overlap_sql(table.name, operation)).execute_if(dialect="sqlite"),
        )
