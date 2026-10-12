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
