# Security and credential rotation

## Historical exposure

The audit found `.env` and SQLite data in all five original commits:
`31d0e61f5a9b`, `773a4478ed04`, `db85608eaa6a`, `f4429d51f9ce`, and `da6e99ebde73`.
The latter two also contain a database backup. Original configuration and demo seeding contained shared credentials and fallback signup codes.

This branch removes these files from **tracking**, preserves their local copies, removes fallback/bootstrap credentials, and adds ignore rules. It does **not** remove earlier objects from Git. Do not assume exposure is resolved by deleting the current files. No history rewrite or force-push was performed.

## Rotate before using an existing installation

1. Inventory every secret in old environment files and any service account used by that installation. Revoke/rotate each through its provider; do not paste values into issues or pull requests.
2. Generate a new Flask secret locally with `python -c "import secrets; print(secrets.token_hex(32))"`. Replace `FLASK_SECRET_KEY` in the protected environment and restart every application process. This invalidates old signed sessions and remember cookies.
3. Reset every account whose password or password hash appeared in a committed database, including formerly seeded demo accounts. Use `python -m flask --app run reset-password USERNAME`; it prompts without echoing the password. Rotate the Flask secret after the resets to invalidate existing sessions.
4. Review privileged roles using trusted server access. Assign only intentional roles with `python -m flask --app run set-role USERNAME ROLE`. Archive unused demo accounts through the authorized UI after resolving active shifts. Retain financial and audit records.
5. Remove obsolete administrator/manager signup-code settings from the live environment. Browser registration ignores role/code fields and is disabled by default in production.
6. Treat committed employee information as exposed. Assess appropriate access/privacy actions for the actual deployment; this repository does not contain a completed incident response.
7. Any history cleanup requires separate explicit authorization and coordination with existing clones/forks. Rotation remains necessary even if history is later rewritten.

The current local `.env` is intentionally preserved and may still contain exposed values. Updating live credentials is an operator action; this implementation does not silently change the original database or a deployed environment.

## Implemented controls

- Required environment secret; no default account creation or startup DDL.
- Werkzeug password hashing, 12–128 character password validation, explicit administrator/password/role CLI operations, and public registration restricted to new employee accounts.
- Flask-WTF CSRF protection on browser forms and session-authenticated JSON POST requests. A JSON client uses the rendered `csrf-token` meta value in `X-CSRFToken`, as described in the [Flask-WTF documentation](https://flask-wtf.readthedocs.io/en/latest/csrf/).
- POST-only logout, safe local redirects, bounded input sizes, finite decimal validation, department/reference checks, and generic conflict/server errors.
- Ten login attempts per minute and ten registrations per hour per remote address. Local memory storage is for development; production requires shared storage. Reverse-proxy address handling must be configured for the actual trusted proxy topology before deployment.
- HttpOnly/SameSite session and remember cookies; secure cookies, HSTS, and debug-disabled validation in production.
- Remembered sign-in is opt-in for seven days. Unchecked sign-in uses a browser-session cookie; POST sign-out removes the remember cookie as well as authentication state. Cookie restoration still passes the active-account check. Password resets require the documented secret rotation to invalidate existing signed cookies.
- Route-level roles, employee ownership checks, private inbox reads/mutations, archived-account denial, and payroll/leave self-review denial.
- SQLite foreign keys, daily payroll uniqueness, active-shift uniqueness, leave overlap triggers, and optimistic payroll/leave versions to reject stale writes.
- Escaped Jinja output, local frontend assets, frame denial, MIME-sniffing protection, no-store authenticated responses, and a CSP restricting external resources and form destinations.
- Spreadsheet formula neutralization in CSV text fields, export filename allowlists, authorized CSV/PDF access, and no arbitrary filesystem export paths.

## Limits and deployment prerequisites

The CSP permits inline scripts/styles used by the existing templates; extracting scripts and using nonces is planned. There is no MFA, SSO, email verification, email password recovery, external audit storage, or demonstrated legal payroll compliance. Management roles have organization-wide read access. The browser assistant is a deterministic navigation helper, not an AI service.

Validate HTTPS, a production WSGI server, shared Redis rate limiting, proxy trust, private database storage, backups/restore procedures, logging retention, and employee-data access policies before deployment. Current tests and scans are evidence of checked behavior, not a guarantee against every vulnerability.

Run `python -m scripts.security_check --history`, `python -m bandit -r app config.py run.py`, and `python -m pip_audit -r requirements.txt`. The tracked-file checker reports paths and suspicious assignment locations without printing secret values. Python dependency auditing does not audit vendored JavaScript libraries.
# Chat channels and pictures

Channels are visible to all active employee-linked accounts in this installation; separate channels are not private membership groups. Only administrators and executives can create channels. Text sending and image uploads require authentication, an active employee profile, CSRF, and the per-user message rate limit. Image URLs also require an active coworker account and return no-store/nosniff headers.

Pictures are validated using Pillow and re-encoded as JPEG/PNG, with original metadata removed. Upload size, pixel count, output dimensions, and request size are bounded; arbitrary files and SVG are rejected. Picture data stays in SQLite and is included in database backups. Protect those backups as employee communication data. Chat has no moderation or retention controls; apply an appropriate workplace policy before production adoption.
