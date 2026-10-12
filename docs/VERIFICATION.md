# Verification record

Final local verification was completed on October 12, 2026, on Windows with Python 3.12 and a newly created `.venv-verify` environment installed from `requirements-dev.txt`. The application was exercised with disposable databases and generated demo/test credentials. No live database was upgraded or reset.

| Check | Executed result |
|---|---|
| `python -m pytest -q` | **102 passed, 0 failed**, 30.09 seconds |
| Ruff lint | All checks passed |
| Ruff formatting | 37 Python files already formatted |
| Bandit, application/config/startup | 0 unsuppressed findings; two B105 annotations identify validation messages mistaken for password literals |
| `pip-audit -r requirements.txt` | 27 resolved runtime packages, 0 known vulnerabilities at the time of the audit |
| Dependency installation / `pip check` | Clean environment installation and dependency consistency passed |
| Tracked-source security check | Passed after staging all source; exposed `.env`, databases, backups, and bytecode removed from current tracking |
| Historical exposure review | Five original commits contain sensitive environment/database artifacts; history is unchanged |
| Documented fresh setup | Environment generation, migrations, demo seeding, localhost startup, and HTTP 200 login page passed in a clean temporary directory |
| Disposable original-database migration | All original values and rows in eight tables preserved; foreign-key check passed; original file hash unchanged |
| Chromium browser verification | Nine workflow checks passed, 0 browser errors; 14 real screenshots captured |

The browser script exercised employee login with CSRF, attendance clock-in/out, leave submission, POST logout, administrator login, employee creation/search/edit/profile, cross-day overtime and payroll approval, leave approval, inbox/audit pages, CSV/PDF downloads, and mobile dashboard/directory/payroll without horizontal page overflow. Screenshots use synthetic records only.

The automated tests additionally cover role and ownership restrictions, employee archival and retained history, login throttling, hostile redirect variants, invalid numerical input, partial workweeks, compensation snapshots, correction-driven approval resets, voiding, formula-safe CSV, UTC offsets, leave overlap triggers, duplicate active shifts, foreign keys, optimistic concurrency, trusted administration commands, fresh/legacy migrations, invalid-data migration refusal, and destructive downgrade refusal.

## Scope and limits

Local results are observed executions, not a claim that production operation or legal payroll compliance has been validated. Dependency and static-analysis tools are bounded checks and do not prove absence of every vulnerability or secret. Production Redis/TLS/proxy operation, Linux hosting, large datasets, load testing, and jurisdiction-specific payroll rules were not exercised locally.

GitHub Actions runs the same test, lint, format, security, dependency, and browser commands on Python 3.12/Ubuntu. Its result must be read from the pull request; local results alone do not establish a successful hosted CI run.

Before production adoption, rotate previously exposed credentials, complete imported-payroll reconciliation, review organization-wide management access, and verify production infrastructure and backups. See [SECURITY.md](SECURITY.md), [MIGRATIONS.md](MIGRATIONS.md), and [BUSINESS_RULES.md](BUSINESS_RULES.md).
