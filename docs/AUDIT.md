# Repository audit and implementation decisions

The reviewed project comprised configuration/startup, the ORM and all route blueprints, shared utilities/decorators, Jinja templates/CSS, original tests, schema SQL, tracked artifacts, and five original Git commits. Existing Flask, SQLite, Jinja, Bootstrap, reporting, and role workflows were retained.

The October 15 planning upgrade adds researched private publication, week reuse, employee availability/coverage, planned costs, calendars and sourced clock-hour preparation. See [PRODUCT_RESEARCH.md](PRODUCT_RESEARCH.md) for primary comparisons and tradeoffs, and [VERIFICATION.md](VERIFICATION.md) for the 381-test/31-browser-workflow execution record. This extends the existing architecture rather than replacing it.

| Finding | Change / remaining implication |
|---|---|
| Hardcoded fallback Flask/admin secrets and signup codes | Removed; required environment secret and explicit CLI account provisioning |
| `.env`, live database, backup, and bytecode tracked | Removed from current tracking, local copies preserved, ignore rules added; previous history remains exposed |
| Startup schema changes and automatic demo users | Replaced by explicit migrations and opt-in empty-database demo seeding |
| Browser mutations lacked CSRF; logout used GET | CSRF on every POST form/session JSON request; POST-only logout |
| Redirect accepted `//host` paths | Validated decoded local paths and rejected scheme/netloc/backslash/control-character variants |
| Existing employee email could cause duplicate signup failure | Registration rejects every existing employee email; no profile claiming |
| Admin notification views/read-all crossed ownership boundaries | Every inbox/read action restricted to its owner |
| Most mutations shared all elevated roles | Defined employee-edit, payroll-review, and leave/attendance-review groups; self-review blocked |
| Payroll overtime applied to each individual date; floats used for money | Whole-workweek decimal allocation, rate snapshots, daily limits, correction tests, and approval resets |
| Employee/payroll deletion removed historical data | Employee archival and payroll voiding with retained records |
| Duplicate clock-ins could race; mixed timestamp assumptions | Partial unique active-shift index, UTC normalization, timezone display, and correction CLI |
| Leave ranges lacked overlap protection; repeated review possible | Application/database overlap checks, explicit Pending-only reviews, optimistic versions |
| CSV formula injection and untrusted period filenames | Neutralized spreadsheet formulas; allowlisted export periods |
| PDF silently truncated after 500 rows | Removed row truncation; documented memory/performance limits for large exports |
| Dashboard's weekly number summed latest eight entries; false low-hours alerts | Date-bounded weekly total and removal of a daily-vs-weekly misleading alert |
| Wrong directory profile link and placeholder settings/metrics | Employee detail route, real policy display, removal of fake login metrics and inactive configuration controls |
| Broken CDN integrity attributes and runtime network reliance | Pinned local frontend assets and retained licenses |
| Missing CI/lint/format/security checks and narrow tests | Pytest regressions, migrations/browser verification, Ruff, Bandit, dependency audit, tracked-source check, and CI |

## Compatibility changes

- Logout is POST-only; scripts/forms must provide a CSRF token. Session-authenticated JSON mutations use `X-CSRFToken`.
- New timesheets are daily 0–24-hour totals, not 168-hour/weekly entries. Correcting one date can reset later approvals in its workweek.
- Imported payroll is preserved and read-only until a reviewed reconciliation migration establishes its meaning.
- Startup no longer initializes/seeds the database. Run `flask db upgrade` explicitly, and create accounts or seed demo data deliberately.
- Employee archival disables account access, including existing sessions, and never cascades historical deletion. Payroll removal now voids a row.
- API monetary values/rates are strings, and payroll API rows include status. Attendance timestamps identify UTC explicitly.
- Management mutation permissions are narrower; settings are an honest read-only policy view. Owners administer staff roles, session revocation and password recovery through the app. Platform roles and emergency owner recovery require trusted CLI access.

## Remaining boundaries

The work verifies a portfolio/demo application and a bounded synthetic Linux workload with real Gunicorn, Redis and HTTPS. Production privileged MFA, owner staff controls, in-app schedule notifications and encrypted coordinated recovery are implemented. The detailed current findings are in [FUNCTIONAL_REVIEW.md](FUNCTIONAL_REVIEW.md). Historical exposure still needs external-provider rotation and privacy review, and imported payroll requires source reconciliation. Department-level manager scoping, SSO, email verification/delivery, external audit retention, public hosting/backup/alert activation, hosted capacity and real payroll sign-off remain outside the verified scope.

No public deployment, history rewrite, force-push, merge, or deletion/reset of the original database was performed.
