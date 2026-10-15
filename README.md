# Shiftspace

A full-stack workforce application built with Python and Flask. Today keeps the shift clock and upcoming schedule together. Team chat has a Discord-style layout with separate channels, coworker conversations, and picture sharing. Authorized staff use Manage workplace to correct punches, plan shifts, and manage employee records.

The researched planning upgrade adds private drafts, week copying, employee availability, approved shift coverage, planned labor estimates, personal calendar downloads and traceable clock-to-timesheet review. The [product research and decisions](docs/PRODUCT_RESEARCH.md) compare nine relevant products and explain the scope and engineering tradeoffs.

Built by **Anotida Hondonga** as a software engineering portfolio project. The existing Flask/Jinja architecture is preserved and strengthened with explicit migrations, tested business rules, and safer administration.

**Status:** verified local demo and portfolio build. Production use requires credential rotation, reconciliation of imported payroll, deployment review, and jurisdiction-specific payroll validation. No public deployment is included.

![Workforce dashboard with synthetic records](docs/screenshots/dashboard.png)

## The problem

Small teams often track employee details, hours, attendance, and leave in separate spreadsheets. This application connects those workflows, gives employees a personal workspace, and gives authorized staff review tools and an audit trail.

## Features

- Focused everyday navigation: Today, Schedule, and Team chat; staff tools under Manage workplace and personal tools under My account.
- Low-light charcoal theme and Shiftspace branding, with Today dedicated to the shift clock and schedule.
- Persistent chat channels with authenticated authors, CSRF-protected sending, escaped text, earlier-message loading, and automatic polling across signed-in coworker sessions. Administrators and executives can create channels.
- Protected picture sharing with previews, validated/normalized uploads, stripped metadata, bounded dimensions/file sizes, and storage covered by SQLite backups.
- Searchable emoji picker with categories, skin tones, keyboard navigation, and newer iPhone emoji; all 3,944 Unicode 17.0 fully qualified sequences are available.
- Owner-facing staff access controls for manager permissions, 30-minute single-use password recovery links, and removal of all staff sign-ins.
- Encrypted authenticator MFA with one-use recovery codes; management and owners must enroll in production. Password and role changes revoke existing sessions and remembered access.
- Production Blueprint with a durable volume, shared Key Value rate limits, two Gunicorn workers, health/readiness checks, encrypted coordinated backups and verified off-host restore drills.
- Optional seven-day remembered sign-in on this device; signing out removes its cookie, while unchecked sign-in uses a browser session.
- Separate store workspaces with location sign-in links, private staff/schedules/chats/pictures, and an owner account that can switch across its locations.
- Simple store search by business or street and seven-day staff invitations issued by administrators, executives or HR for the correct location.
- Simple schedule form: choose a person, tap one or more days, and set the hours once. Includes a weekly calendar, staff filtering, hour totals, overnight shifts, and a duration preview in the store's time zone.
- Private schedule drafts with atomic filtered publication, source/target snapshot checks and copy-previous-week tools that preserve local clock times across daylight-saving changes.
- Self-service recurring availability, conflicts flagged on existing shifts, and availability/leave/overlap validation before new assignments or coverage approval.
- Coworker coverage offers and requests with independent manager approval, stale-request protection, private notices and retained assignment/audit history.
- Planning-only Decimal labor estimates and weekly overtime indicators, plus authenticated personal iCalendar downloads.
- Clock-hours review compares planned, actual and payroll hours. Confirmed preparation creates pending daily timesheets, splits overnight punches at local midnight, retains source fingerprints and blocks approval after source changes.
- Approved-time-off and overlapping-shift checks, preserved invalid drafts, versioned edits, confirmed cancellation that retains history, and employee visibility limited to their own shifts. Schedule creation, edits, reassignment, and cancellation send private in-app notices, with a visible notification button.
- Manager punch corrections and entirely missed shifts, with required reasons, before/after audit entries, overlap checks, and stale-edit protection.
- Employee directory with search, department/role/status filters, profiles, editing, and archival that preserves records.
- Employee invitations, restricted owner registration, hashed passwords, CSRF-protected sessions, per-location login throttling, and explicit CLI administration.
- Daily timesheet totals, Monday–Sunday overtime allocation, decimal monetary calculations, submitted rate snapshots, and approval resets after corrections.
- Dashboard clock-in/out with personal shift status, UTC storage, configurable display timezone, and a database constraint preventing duplicate active shifts.
- Leave date validation, overlap prevention at both application and database levels, authorized reviews, and self-approval protection.
- Private notification inboxes, searchable audit logs, charts, and CSV/PDF exports with empty-dataset handling.
- Paginated, authenticated read-only JSON APIs for employees, payroll, and attendance.
- Responsive Bootstrap/Jinja screens with local frontend assets, accessible focus states, clear errors, and mobile directory cards.

## Stack and architecture

Python **3.12** is the verified runtime. Flask, Flask-Login, Flask-WTF, Flask-Limiter, SQLAlchemy, SQLite, Flask-Migrate/Alembic, Jinja2, Bootstrap, Chart.js, ReportLab, Pytest, Ruff, Bandit, and GitHub Actions.

```mermaid
flowchart LR
    Browser[Browser: Jinja + Bootstrap + Chart.js] --> Flask[Flask application factory]
    Client[Authenticated JSON client] --> Flask
    Flask --> Guards[Session, CSRF, role and ownership checks]
    Guards --> Routes[Blueprint routes]
    Routes --> Rules[Payroll rules and ORM models]
    Rules --> SQLite[(SQLite)]
    CLI[Trusted administration CLI] --> Rules
    Alembic[Versioned migrations] --> SQLite
    Routes --> Reports[CSV and PDF reporting]
```

`app/` contains the factory, domain models, payroll rules, shared validation, and blueprints. `app/templates/` and `app/static/` provide the frontend. `migrations/` owns database changes; startup never mutates the schema. `tests/` exercises behavior and security. `scripts/` contains browser verification, safe environment setup, backup, and tracked-file checks.

## Database design

Seventeen domain tables model departments, employees, users, payroll records, attendance records, scheduled shifts, recurring availability, shift coverage, chat channels, team messages, chat attachments, leave requests, notifications, audit logs, the hub's store registry, single-use account recovery links, and operator policy reviews. Each store has its own migrated SQLite database; the hub registry records its owner, display name, location, time zone, and generated storage key.

```mermaid
erDiagram
    DEPARTMENT ||--o{ EMPLOYEE : contains
    EMPLOYEE ||--o| USER : has_account
    EMPLOYEE ||--o{ PAYROLL_RECORD : submits
    EMPLOYEE ||--o{ ATTENDANCE : clocks
    EMPLOYEE ||--o{ SCHEDULED_SHIFT : scheduled_for
    EMPLOYEE ||--o| AVAILABILITY_PROFILE : supplies
    EMPLOYEE ||--o{ SHIFT_COVERAGE : offers_or_requests
    SCHEDULED_SHIFT ||--o{ SHIFT_COVERAGE : retains_history
    EMPLOYEE ||--o{ LEAVE_REQUEST : requests
    USER ||--o{ NOTIFICATION : receives
    USER ||--o{ TEAM_MESSAGE : sends
    TEAM_CHANNEL ||--o{ TEAM_MESSAGE : contains
    TEAM_MESSAGE ||--o| CHAT_ATTACHMENT : shares
    USER o|--o{ AUDIT_LOG : performs
    USER o|--o{ PAYROLL_RECORD : reviews
    USER o|--o{ LEAVE_REQUEST : reviews
```

Foreign keys are enforced on SQLite connections. Unique daily payroll entries and active shifts, monetary/date constraints, indexes, overlap triggers for leave and shifts, and optimistic versions for payroll, leave, attendance, and schedules protect data integrity. Employee archival disables associated login access and retains history. Payroll voiding retains the row and recalculates its workweek.

## Authentication and permissions

Workplace registration creates an employee role. Inside a store, an invitation is required and attaches the account to an existing employee profile. `/owner/signup` creates a restricted hub owner who can create/open only their own stores; it cannot grant access to the legacy hub workplace. Both forms ignore submitted role fields. Public registration is disabled by default in production; owner registration can be enabled with `PUBLIC_STORE_OWNER_REGISTRATION=true`. Trusted operators can assign the hub `owner` role with `flask set-role`. Store administrators and owners use **Manage workplace → Staff access** to assign employee, manager, HR, or payroll access. Platform administrator/executive/owner assignments remain trusted CLI actions.

| Action | Employee | Manager | HR | Payroll | Admin / Executive |
|---|---|---|---|---|---|
| Personal profile, leave, inbox | Own | Own | Own | Own | Own |
| Availability, coverage requests, calendar | Own | Own | Own | Own | Own |
| Draft/copy/publish schedule | — | Yes | Yes | — | Yes |
| Approve another person's coverage | — | Yes | Yes | — | Yes |
| Prepare pending clock-derived payroll | — | Yes | — | Yes | Yes |
| Workforce/report/API read access | Own operations; team directory | Organization | Organization | Organization | Organization |
| Create/edit employee records | — | — | Yes | — | Yes |
| Archive employee records | — | — | — | — | Yes |
| Review payroll / log another employee's hours | — | Yes | — | Yes | Yes |
| Review leave / attendance actions for others | — | Yes | Yes | — | Yes |
| Correct punches / create, edit, cancel shifts | — | Yes | Yes | — | Yes |
| Shared coworker chat | Yes, active profile | Yes, active profile | Yes, active profile | Yes, active profile | Yes, active profile |
| Create chat channels | — | — | — | — | Yes |

Every inbox remains private, including administrator inboxes. Reviewers cannot approve their own payroll or leave. Management read access is workplace-wide within the selected store; department-level manager scoping is a future improvement.

## Everyday use and management

Owners sign in to **My stores**, enter a business name and location, and choose **Create store**. Store codes are generated automatically; a collapsed option allows a custom code or time zone. Each card has **Open location** and a sign-in link such as `/s/mcdonalds-broad-street/login`. The owner can return to **My stores** from each location. New owner accounts see only store management in the hub.

Staff use their location's link or **Find your store** to search the business and street. In that store, authorized people managers add a staff record, open its profile, and choose **Invite to this store**. The invitation lasts seven days and can be accepted once; it creates employee access for that profile. Staff, schedules, chats, images, punches, payroll, notifications, and reports remain in that location's database. Channels within a store are visible to all its active coworkers.

Routing uses independent Flask apps under `/s/<store-code>` with generated database filenames, derived signing keys, and separate cookie names/paths. Owner entry requires a CSRF-protected POST and ownership check; invitations are signed for one location. Unknown store URLs fail closed. This follows Flask's documented [application dispatch pattern](https://flask.palletsprojects.com/en/stable/patterns/appdispatch/). Production operators must persist and back up both the hub and all store files; see [MIGRATIONS.md](docs/MIGRATIONS.md).

After signing in, **Today** shows your own Clock in / Clock out button and upcoming dated shifts. **Schedule** opens this week's shifts with previous/next week navigation. Employees see their own schedule; management roles can read the workplace schedule. No shifts are inferred from a profile's typical hours.

Open **Manage workplace → Punches** to select **Correct**, fix times, enter a reason, and save. **Add missed punches** creates a shift that was never clocked. Clock out may be left blank for someone still working. Corrected punches retain their record ID and an audit of old/new values; review any affected payroll entry separately.

Open **Manage workplace → Schedule** for a Monday–Sunday board. Filter by employee or view the full team; daily and weekly totals count only hours inside the displayed dates. Overnight shifts appear on both dates with continuation labels. On mobile, the board becomes a day agenda.

The form sits above the calendar: choose an **Employee**, tap the **Days**, set **Start time** and **End time**, then **Save shift** (or **Save 3 shifts**, for example). Notes are tucked under **Add a note**. An earlier end time automatically means the next morning. Choose **Add shift** on a calendar day to select just that day while keeping your chosen person and hours. The whole group saves only if every shift passes validation. Existing shifts have Edit and Cancel shift controls; edits change one dated shift, and cancellation asks for confirmation and retains history. Saved shifts are visible to staff immediately and send an in-app notice; there is no draft/publish stage. The form also works without JavaScript.

Approved time off blocks new or changed shifts touching that local date; a shift ending exactly at midnight does not occupy the next day. If time off is approved after a shift was posted, the board flags it for review while retaining the shift. Invalid submissions keep the entered values and edit identity. Overlap checks and database triggers, version guards, and a ten-year planning range protect changes. Times use the selected store's zone; ambiguous or nonexistent clock-change inputs are rejected, and repeated shifts preserve wall-clock hours while actual duration can change with daylight saving. Planned hours do not create payroll or punches.

**Team chat** opens a channel list, conversation, and coworker list in a Discord-style layout. Administrators and executives use **Create channel** to add separate conversations. All channels are visible to active employee-linked accounts in this installation; channels are not private membership groups. Existing conversations remain in **#general**. Messages persist, update about every eight seconds while the page is visible, and support loading earlier messages. Enter sends; Shift+Enter adds a line break.

The smile button opens **Choose emoji**: search across categories, choose a skin tone, or browse **New on iPhone**. **All variations** includes mixed-tone combinations. Emoji insert at the message cursor and respect the message length limit. Escape closes the picker; arrow keys navigate its emoji choices. Artwork uses the device's native emoji font: Apple artwork on compatible iPhones/Macs, Windows artwork on Windows. The locally served catalog uses [Unicode 17.0 data](https://www.unicode.org/Public/17.0.0/emoji/emoji-test.txt), including the additions described in [Apple's iOS update notes](https://support.apple.com/en-au/123075). Its [Unicode license](docs/licenses/UNICODE.txt) is included. Apple fonts/artwork are not redistributed; appearance and newer-character support depend on the installed OS.

Use **Add picture** to preview and send one JPG, PNG, WebP, or GIF per message, up to 5 MB and 12 million source pixels. Pictures are resized to at most 2,048 pixels on the longest edge, normalized to JPEG/PNG, and stripped of original metadata. GIF uploads become a still picture. Attachments require an active authenticated coworker account and remain in SQLite with their messages, so backups include pictures. Direct messages, private channels, presence, push alerts, moderation, and deletion controls are not implemented. Other tools, including payroll, reports, and audit history, are available from the management page.

## Install and run a fresh local demo

These steps apply to a **fresh clone**. For an existing installation, follow [migration and backup instructions](docs/MIGRATIONS.md) first.

```powershell
git clone https://github.com/AnoHondz/employee-Data-base.git
cd employee-Data-base
git checkout codex/workforce-production-readiness
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m scripts.configure_environment
python -m flask --app run db upgrade
python -m flask --app run seed-demo
python run.py
```

Open **http://127.0.0.1:5000**. `seed-demo` creates synthetic employees and generates a fresh password for each of `demo.admin`, `demo.employee`, and `demo.manager`, displayed once in your terminal. There is no shared demo password. Seeding refuses a populated database and is disabled in production.

On macOS/Linux, use `python3.12 -m venv .venv` and `source .venv/bin/activate`; the remaining Python commands are identical. Install `requirements.txt` if you only need the application; `requirements-dev.txt` adds verification tools.

To create your own local administrator instead of demo data:

```text
python -m flask --app run create-admin
```

The CLI prompts for username, email, and a hidden, confirmed password of 12–128 characters. It never runs at startup. Staff roles and recovery links are available through **Manage workplace ? Staff access**. Each change requires the owner's password and recent authenticator verification when enabled. Owners can change their own password under **Account security**. Trusted bootstrap and emergency operations remain explicit:

```text
python -m flask --app run reset-password USERNAME
python -m flask --app run set-role USERNAME manager
```

## Configuration

`.env.example` contains placeholders only. `scripts.configure_environment` creates `.env` with a fresh random secret and refuses to overwrite an existing file. Environment variables override `.env` values.

| Variable | Purpose |
|---|---|
| `FLASK_SECRET_KEY` | Required random secret, at least 32 characters; use 64 hex characters |
| `APP_ENV` | `development` or `production`; production enables secure cookies and disables public registration |
| `DATABASE_URL` | Absolute SQLite URL; defaults to `instance/workforce.db` |
| `WORKFORCE_TIMEZONE` | IANA timezone used for attendance day/display; defaults to `UTC` |
| `RATELIMIT_STORAGE_URI` | `memory://` locally; production requires shared storage such as Redis |

The production setup and its required operator inputs are documented in [deployment and recovery](docs/DEPLOYMENT.md). The Blueprint is prepared for a Virginia pilot of 1-5 stores and 100 staff on one durable instance with two Gunicorn workers and shared Key Value rate limits. Startup rejects insecure cookies, disabled MFA/payroll review, missing encryption keys, invalid HTTPS origins, and unsuitable storage. A coordinated migration phase and verified off-host backup precede web startup. Public deployment has not been performed. The bundled local demo still binds to localhost with debug disabled.

## Payroll and attendance rules

An entry represents the **total of all shifts on one work date**, with 0–24 hours and at most two decimal places. Saving the same employee/date replaces that daily total, rather than creating duplicates. Dates cannot be in the future.

The workweek runs **Monday through Sunday**. The first 40 hours are regular hours; later hours use the employee's submitted overtime multiplier. For five 9-hour days at $30/hour and 1.5× overtime: 40 regular hours + 5 overtime hours = **$1,425.00**. Corrections recalculate the whole affected week and reset changed approvals. A selected reporting pay period never resets a workweek's overtime threshold.

Money uses `Decimal` with half-up cent rounding and submitted compensation snapshots. Reports exclude rejected/voided entries; pending/reviewed entries remain estimates. Payroll review/void controls and leave updates require the displayed optimistic version, preventing old forms from approving or overwriting newer corrections. Approval and imported-record status are included in payroll exports.

Attendance is stored in UTC and displayed in the configured timezone. Shifts may last at most 24 hours. Attendance does not automatically create payroll entries. Authorized payroll reviewers can open **Review clock hours**, compare a selected week, resolve open punches or manual discrepancies, confirm payable work/breaks, then prepare pending timesheets. Overnight punches split at local midnight; daily hours round after summing intervals. Approved/imported/voided records are protected, and later punch corrections flag sourced entries and block approval until reconciled. Manual totals remain available for a deliberate correction. Managers, HR, administrators, and executives can reconcile punches through Manage workplace. Trusted server operators also have `correct-attendance` with offset-aware timestamps; see [business rules](docs/BUSINESS_RULES.md).

In production, wage approval requires an owner-recorded **Payroll readiness** review with an evidence reference for the supported Virginia policy: 2026-2028 work dates, adult hourly nonexempt staff working at one location, one rate, Monday-Sunday weekly overtime, and no tips, bonuses, deductions, taxes, or payment processing. Imported and mixed-rate workweeks remain blocked. Cross-location combined overtime is handled by the employer's external payroll process; separate store databases do not calculate it. Eligibility is an owner attestation that must remain current; the app does not classify staff, detect cross-location employment or expire the review automatically. These controls record human review and do not certify legal compliance.

## Testing and quality

```text
python -m pytest -q
python -m ruff check app tests scripts migrations config.py run.py
python -m ruff format --check app tests scripts migrations config.py run.py
python -m bandit -r app config.py run.py
python -m scripts.security_check
python -m pip_audit -r requirements.txt
python -m playwright install chromium
python -m scripts.browser_smoke
python -m scripts.validate_blueprint
python -m scripts.production_workload --report artifacts/workload-local.json
```

Tests use disposable databases and freshly generated credentials. The GitHub Actions workflow runs these checks on pushes and pull requests. Local verification results and scope are recorded in [verification notes](docs/VERIFICATION.md); a successful local run does not imply that GitHub CI or a production deployment has passed.

On the sign-in page, select **Remember me on this device** to return without signing in again for up to seven days. Leave it unchecked for a browser session. **Sign out** removes the remembered sign-in from that browser. Session expiry still applies; the signed remember cookie restores access while it remains valid. Archived accounts cannot use it. Cookies are HttpOnly and SameSite=Lax, with Secure required in production. This behavior uses [Flask-Login's remember-cookie support](https://flask-login.readthedocs.io/en/latest/#remember-me).

## Screenshots

Captured from the running application with synthetic records; the researched planning upgrade was captured on October 15, 2026:

| Screen | Preview |
|---|---|
| Login | [Screenshot](docs/screenshots/login.png) |
| Employee workspace | [Screenshot](docs/screenshots/employee-workspace.png) |
| Management and missed punches | [Manage workplace](docs/screenshots/manage-workplace.png) · [Punches](docs/screenshots/missed-punches.png) |
| Dated schedule and coworker chat | [Schedule](docs/screenshots/schedule.png) · [Team chat](docs/screenshots/team-chat.png) |
| General channel | [Screenshot](docs/screenshots/chat-general.png) |
| Emoji picker | [Desktop](docs/screenshots/emoji-picker.png) · [Mobile](docs/screenshots/emoji-picker-mobile.png) |
| Directory and employee profile | [Directory](docs/screenshots/employees.png) · [Profile](docs/screenshots/employee-profile.png) |
| Payroll | [Screenshot](docs/screenshots/payroll.png) |
| Attendance and leave | [Attendance](docs/screenshots/attendance.png) · [Leave](docs/screenshots/leave.png) |
| Notifications, reporting, audit | [Inbox](docs/screenshots/notifications.png) · [Reports](docs/screenshots/reports.png) · [Audit](docs/screenshots/audit.png) |
| Mobile | [Dashboard](docs/screenshots/dashboard-mobile.png) · [Directory](docs/screenshots/employees-mobile.png) · [Payroll](docs/screenshots/payroll-mobile.png) |
| Staff access and security | [Staff access](docs/screenshots/staff-access.png) · [Account security](docs/screenshots/account-security.png) · [Mobile access](docs/screenshots/staff-access-mobile.png) · [Mobile security](docs/screenshots/account-security-mobile.png) |
| Drafts and week tools | [Private drafts](docs/screenshots/schedule-drafts.png) · [Week tools](docs/screenshots/schedule-week-tools.png) · [Mobile](docs/screenshots/schedule-week-tools-mobile.png) |
| Availability and coverage | [Availability](docs/screenshots/availability.png) · [Coverage requests](docs/screenshots/coverage-requests.png) · [Mobile availability](docs/screenshots/availability-mobile.png) |
| Clock-hours review | [Desktop](docs/screenshots/clock-hours-review.png) · [Mobile](docs/screenshots/clock-hours-review-mobile.png) |
| Mobile workplace tools | [Schedule](docs/screenshots/schedule-mobile.png) · [Chat](docs/screenshots/team-chat-mobile.png) · [Manage workplace](docs/screenshots/manage-workplace-mobile.png) |

Regenerate only from a successful run using `python -m scripts.browser_smoke --screenshots`.

## Security, migrations, and limitations

Earlier Git history contains `.env`, SQLite databases, and hardcoded credentials. Current tracking excludes them, but history is unchanged. **Rotate all exposed secrets and account passwords before using a migrated installation.** See [security and rotation instructions](docs/SECURITY.md), [audit findings](docs/AUDIT.md), and [migration procedures](docs/MIGRATIONS.md).

The migration preserves imported payroll totals and marks them for reconciliation because the old app mixed daily and weekly hours. Imported entries are read-only in the browser. No existing database is reset, and destructive downgrades are refused; rollback uses verified backups and the matching previous code.

Known limitations: payroll estimates exclude taxes, deductions, benefits, holiday rules, and automatic legal compliance; manager read access covers the selected store; notifications are in-app only; SSO, email verification and automated email password recovery are absent. Privileged production MFA and owner-issued staff recovery are implemented. SQLite and report exports suit modest datasets. A finite five-store/100-staff Linux workload passed with 24 concurrent clients, two Gunicorn workers, HTTPS and shared Redis; actual public-host capacity, long soaks, external backups and alert delivery still need operator verification. Inline template scripts currently require an `unsafe-inline` CSP allowance.

## Portfolio description and verified résumé bullets

**Shiftspace:** a Flask/SQLAlchemy application connecting shift clocks, schedules, channel-based coworker chat, employee management, payroll, and approval workflows, with a responsive dark interface and automated security/business-rule verification.

- Strengthened a Flask employee operations platform with role and ownership checks, CSRF protection, login throttling, private notifications, and explicit administrator provisioning.
- Implemented workweek-aware decimal payroll calculations, rate snapshots, correction-driven approval resets, and record-preserving SQLite migrations with integrity checks.
- Built automated workflow and security regression tests, a Chromium browser verification script, GitHub Actions quality checks, and recruiter-facing documentation with real application screenshots.

## Future improvements

Department-scoped manager permissions; SSO and email verification; reviewed pay-period locking; provider-specific payroll integrations; imported weekly-payroll reconciliation; PostgreSQL migration and hosted/soak testing; nonce-based CSP; broader payroll policies and cross-location overtime; staffing requirements and explained schedule suggestions; private/threaded chat. The [research roadmap](docs/PRODUCT_RESEARCH.md#further-expansion-in-dependency-order) sets out dependencies and boundaries.

## Troubleshooting

- **Secret validation error:** regenerate a random secret and set `FLASK_SECRET_KEY` in `.env`; previously exported environment variables take precedence.
- **No such table:** run `python -m flask --app run db upgrade` against the intended `DATABASE_URL`.
- **Environment setup refuses to run:** `.env` already exists; preserve it and update its configuration manually.
- **Demo seed refuses to run:** the database already contains records. Create an administrator explicitly or use a separate new demo database; never reset existing data.
- **CSRF error:** refresh the form and retry; cookies must be enabled. Logout is a POST action through the Sign out button.
- **Imported payroll blocks a correction:** reconcile the historical entry's daily/weekly meaning using original timesheets, as described in the migration guide.
- **PowerShell activation blocked:** invoke `.\.venv\Scripts\python.exe` directly in place of `python`; activation is optional.
- **Browser verification cannot launch Chromium:** run `python -m playwright install chromium`; on Linux CI use `--with-deps`.
