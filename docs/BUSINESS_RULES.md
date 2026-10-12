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
# Workplace schedule, corrections, and chat

The Today page presents only the signed-in employee's clock and upcoming dated shifts. Coworker conversations live on Team chat. Management controls are under Manage workplace. Managers, HR, administrators, and executives may add or correct punches and create, edit, or cancel schedules; payroll has read access. Employees read only their own dated shifts.

Browser punch corrections require a reason and retain the same record ID, with audited old/new times and hours. Entire missed shifts can also be added. Actual punches cannot be future timestamps or overlap another attendance record, and completed shifts must last more than zero and no more than 24 hours. An empty clock-out keeps the shift active. Corrections do not update payroll automatically. Optimistic versions reject stale forms and SQLite triggers prevent overlapping shift writes.

Schedules use actual dates and UTC storage with configured local-time inputs/display. They may cross midnight but must last no more than 24 hours. Cancellation retains the record. Cancelled shifts disappear from employee schedules, and the cancellation is audited. Profile typical-hours text is separate from the dated schedule. Local times that are ambiguous or nonexistent during daylight-saving changes are rejected.

Team chat uses separate channels shared by active employee-linked accounts within this installation, including all staff roles. Only administrators and executives create channels; all coworkers can read and post in each channel. Channel names are unique and use 1–40 lowercase letters, digits, or hyphens. Authors are always the authenticated user. Bodies are plain text of up to 2,000 characters, safely escaped in server-rendered HTML and inserted with textContent during polling. A message needs text or a valid picture. Sending requires CSRF and is limited to 30 messages per minute per user. Views show the most recent 25 messages initially, support loading earlier messages, and retrieve up to 100 per polling request. Channel filters apply to current, older, and new messages.

One picture per message is supported, with a 5 MB upload limit and 12 million source-pixel limit. JPG, PNG, WebP, and GIF are decoded and verified, resized to a longest edge of 2,048 pixels, stripped of original metadata, and re-encoded as JPEG or PNG. GIF is a still frame. Invalid pictures reject the entire message. Pictures and messages commit together and remain in SQLite, so verified database backups cover both. Serving pictures requires an authenticated active coworker profile, uses a fixed image MIME type, and sends no-store/nosniff headers. Private groups, arbitrary file attachments, direct messages, moderation, and retention controls are not implemented.
