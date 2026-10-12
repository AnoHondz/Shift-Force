# Workforce business rules

## Timesheets, workweeks, and reporting periods

`PayrollRecord` is one daily total per employee/work date, representing all shifts on that date. Hours are decimal values with two places, between 0 and 24. Saving a repeated employee/date is an explicit correction of that total. Notes are bounded to 2,000 characters. Future work dates are rejected.

`Workweek` is Monday–Sunday. In date order, the first 40 hours use the saved regular rate; subsequent hours use the saved overtime multiplier. `PayPeriod` is an inclusive reporting interval with a start on/before its end. Reporting periods can cover a partial week without changing the workweek allocation. Weekly reports are week-to-date, monthly reports are month-to-date, and last-30-day reports include today plus the preceding 29 days.

Daily money is rounded to cents using `Decimal` and `ROUND_HALF_UP`. Rates/multipliers are saved on submission, so later employee compensation changes do not retroactively change prior timesheets. APIs serialize monetary values/rates as strings to preserve decimal representation; hour values remain JSON numbers.

The lifecycle is Pending, Reviewed, Approved, Rejected, or Voided. Authorized reviewers may review/approve/reject another employee's active entry. Employees cannot correct an approved entry. Authorized payroll staff can correct it, which resets the corrected entry and any changed weekly allocations to Pending and clears review metadata. Rejected and voided entries are excluded from active workweek allocation and reports. Voiding preserves the source row. Resubmitting a rejected date makes it Pending again.

Pending and reviewed entries shown in reports are **estimates**, not completed disbursements. Taxes, deductions, benefits, statutory daily overtime, holiday premiums, leave pay, jurisdiction-specific workweek rules, rounding policies required by law, and bank payments are outside the implemented model. Do not claim legal payroll compliance without jurisdiction-specific review.

## Attendance

Every linked account can use **Your shift** on its dashboard to clock in and out of its own shift. The status changes from Off shift to On shift and shows the start time in the configured timezone. The personal clock always uses the signed-in employee identity, including for management roles; submitted employee IDs cannot target someone else. Management attendance permissions remain separate.

Clock events use server time in UTC. The database stores naive UTC datetimes; the UI shows the configured IANA timezone and records the clock-in date in that timezone. API timestamps include an explicit UTC offset. Offset-aware corrections are normalized to UTC. Duplicate active shifts are prohibited by a partial unique index, including under concurrent insert attempts.

Clock-out must follow clock-in, a completed shift cannot be completed again, and a shift cannot exceed 24 hours. Overnight shifts are allowed within that limit. Attendance does not write payroll automatically; users submit reviewed daily timesheet totals separately and allocate overnight work to the applicable dates.

Trusted server operators can correct a stale/incorrect shift without deleting its history:

```text
python -m flask --app run correct-attendance RECORD_ID --clock-in 2026-10-05T10:00:00-04:00 --clock-out 2026-10-05T18:00:00-04:00
```

Both timestamps require explicit offsets. The CLI logs old/new times. Payroll totals remain unchanged until their separate correction/review workflow runs.

## Leave, privacy, and record retention

Leave requires a recognized request type and a start on/before its end. Pending or approved requests for the same employee cannot overlap, including touching dates. Application validation explains the error; SQLite triggers enforce it under concurrent writes. Rejected dates may be requested again. Only Pending leave may be reviewed, and no user can review their own request. Optimistic version checks reject stale reviews.

Notifications are stored/delivered to the intended in-app user, and read/mark-all actions are scoped to that same owner for every role. Roles do not grant access to another user's private inbox.

Employee archival preserves user, timesheet, attendance, leave, and audit records and disables the user's login/session access. Self-archival is refused, and an active shift must be completed first. Financial records are voided rather than deleted. Audit logs include workflow/security events and payroll correction hours; they are not an external, immutable audit service.

Management read access is intentionally organization-wide in this version. Department-level manager permissions, automated shift allocation, and configurable legal payroll policies require additional design and validation.
