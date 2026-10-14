# Security and credential rotation

## Historical exposure

The audit found `.env` and SQLite data in all five original commits:
`31d0e61f5a9b`, `773a4478ed04`, `db85608eaa6a`, `f4429d51f9ce`, and `da6e99ebde73`.
The latter two also contain a database backup. Original configuration and demo seeding contained shared credentials and fallback signup codes.

This branch removes these files from **tracking**, preserves their local copies, removes fallback/bootstrap credentials, and adds ignore rules. It does **not** remove earlier objects from Git. Do not assume exposure is resolved by deleting the current files. No history rewrite or force-push was performed.

## Rotate before using an existing installation

1. Inventory every secret in old environment files and any service account used by that installation. Revoke/rotate each through its provider; do not paste values into issues or pull requests.
2. Generate a new Flask secret locally with `python -c "import secrets; print(secrets.token_hex(32))"`. Replace `FLASK_SECRET_KEY` in the protected environment and restart every application process. This invalidates old signed sessions and remember cookies.
3. Reset every account whose password or password hash appeared in a committed database, including formerly seeded demo accounts. Use `python -m flask --app run reset-password USERNAME`; it prompts without echoing and revokes that account's sessions and recovery links. Rotate the global signing key for historical key exposure, and repeat account recovery for each relevant store database.
4. Review privileged roles using trusted server access. Assign only intentional roles with `python -m flask --app run set-role USERNAME ROLE`. Archive unused demo accounts through the authorized UI after resolving active shifts. Retain financial and audit records.
5. Remove obsolete administrator/manager signup-code settings from the live environment. Browser registration ignores role/code fields and is disabled by default in production.
6. Treat committed employee information as exposed. Assess appropriate access/privacy actions for the actual deployment; this repository does not contain a completed incident response.
7. Any history cleanup requires separate explicit authorization and coordination with existing clones/forks. Rotation remains necessary even if history is later rewritten.

The workspace signing key was explicitly rotated as part of the requested remediation; obsolete bootstrap controls and an unused legacy database-password setting were removed. A private pre-change copy was retained. The inventory found no remaining local environment values matching historical secrets. External credentials and affected account/employee-information response still require operator confirmation; no production provider credentials were changed. See [historical exposure response](HISTORY_CLEANUP.md).

## Implemented controls

- Required environment secret; no default account creation or startup DDL.
- Werkzeug password hashing, 12–128 character password validation, explicit administrator/password/role CLI operations, workplace registration restricted to employee accounts, and separate restricted owner registration.
- Flask-WTF CSRF protection on browser forms and session-authenticated JSON POST requests. A JSON client uses the rendered `csrf-token` meta value in `X-CSRFToken`, as described in the [Flask-WTF documentation](https://flask-wtf.readthedocs.io/en/latest/csrf/).
- POST-only logout, safe local redirects, bounded input sizes, finite decimal validation, department/reference checks, and generic conflict/server errors.
- Ten login/MFA attempts per account/address per minute and ten registrations per account/address per hour, with separate 100-attempt address ceilings for each respective interval. Local memory storage is for development; production requires shared storage. Reverse-proxy address handling must be configured for the actual trusted proxy topology before deployment.
- HttpOnly/SameSite session and remember cookies; secure cookies, HSTS, and debug-disabled validation in production.
- Remembered sign-in is opt-in for seven days. Unchecked sign-in uses a browser-session cookie; POST sign-out removes the remember cookie as well as authentication state. Cookie restoration still passes the active-account check. Password resets rotate a per-account random authentication token, immediately revoking existing session/remember cookies and outstanding recovery links. Role changes and explicit sign-in removal also rotate that token. Enabled MFA is retained; unfinished MFA enrollment is discarded.
- Route-level roles, employee ownership checks, private inbox reads/mutations, archived-account denial, and payroll/leave self-review denial.
- SQLite foreign keys, daily payroll uniqueness, active-shift uniqueness, leave overlap triggers, and optimistic payroll/leave versions to reject stale writes.
- Escaped Jinja output, local frontend assets, frame denial, MIME-sniffing protection, no-store authenticated responses, and a CSP restricting external resources and form destinations.
- Spreadsheet formula neutralization in CSV text fields, export filename allowlists, authorized CSV/PDF access, and no arbitrary filesystem export paths.

## Limits and deployment prerequisites

Store URLs dispatch to separate databases and Flask apps. Signing secrets derive from the protected hub secret and a random storage key; session/remember cookie names and paths differ per location. Ownership is checked before a CSRF-protected hub POST issues a store-owner session. A hub `owner` cannot read the legacy hub directory, conversations, APIs, payroll, or reports and can open only its own locations. Existing hub administrators/executives remain trusted platform operators.

Staff join through a seven-day store-signed invitation tied to an active, unclaimed employee profile. A token from another location, tampered/expired token, archived profile, or already accepted invitation is rejected. Submitted role/name/email cannot replace that invited profile or grant elevated roles. Login/registration policies bind to a separate limiter per app, with store-specific storage prefixes. Rate limits remain address-based and require a correctly configured trusted proxy in production.

Public store search reveals only business name, location, and sign-in URL. It never exposes staff or conversations. Store links and local sample names do not establish real brand ownership. Registration does not verify email or business ownership. Do not deploy open owner registration without the appropriate operational controls. Back up every store database with the hub; neither root accounts nor files should be public.

The CSP permits inline scripts/styles used by the existing templates; extracting scripts and using nonces is planned. Privileged production accounts must enroll an authenticator. Secrets are encrypted with an independently retained key; TOTP and recovery codes are consumed with conditional database writes to reject replay across workers. MFA challenges and sign-in/signup limits apply per account/IP plus an IP ceiling so staff sharing store Wi-Fi can sign in. There is no SSO, email verification, automated email password recovery, external audit storage, or demonstrated legal payroll compliance. Management roles have organization-wide read access. The browser assistant is a deterministic navigation helper, not an AI service.

Validate HTTPS, a production WSGI server, shared Redis rate limiting, proxy trust, private database storage, backups/restore procedures, logging retention, and employee-data access policies before deployment. Current tests and scans are evidence of checked behavior, not a guarantee against every vulnerability.

Run `python -m scripts.security_check --history`, `python -m bandit -r app config.py run.py`, and `python -m pip_audit -r requirements.txt`. The tracked-file checker reports paths and suspicious assignment locations without printing secret values. Python dependency auditing does not audit vendored JavaScript libraries.
# Chat channels and pictures

Channels are visible to all active employee-linked accounts in this installation; separate channels are not private membership groups. Only administrators and executives can create channels. Text sending and image uploads require authentication, an active employee profile, CSRF, and the per-user message rate limit. Image URLs also require an active coworker account and return no-store/nosniff headers.

Pictures are validated using Pillow and re-encoded as JPEG/PNG, with original metadata removed. Upload size, pixel count, output dimensions, and request size are bounded; arbitrary files and SVG are rejected. Picture data stays in SQLite and is included in database backups. Protect those backups as employee communication data. Chat has no moderation or retention controls; apply an appropriate workplace policy before production adoption.

# Account recovery and production operations

Owners assign staff roles, revoke sign-ins, and create single-use, 30-minute password recovery links. A link is stored only as a SHA-256 hash, bound to that account's current authentication token and store database, and invalidated when a newer link is issued or credentials change. Staff identity must be verified before private handoff. Password recovery does not disable enabled MFA. Owner security checks use the hub credential and current hub token rather than the child's non-login bootstrap password; changing the hub credential or archiving its owner invalidates issued owner sessions in every location.

Changing roles cannot grant platform/owner access, alter the actor's own role, or modify reserved administrator/owner accounts. Sensitive changes require a current password and recent MFA. Lost owner authenticator/recovery codes require trusted CLI recovery with a private identity-verification reference and retyped username; `reset-mfa` revokes all sign-ins and production access requires new enrollment. Keep that server access restricted and audited.

Production telemetry omits passwords, MFA/recovery codes, reset links, query strings, bodies, cookies, IPs and employee names. AES-GCM backup authentication succeeds before restored files are used. Coordinated hub/store backups, off-host download verification, read-only inventory/health checks, restore drills and guarded maintenance are described in [DEPLOYMENT.md](DEPLOYMENT.md). Production paid resources, actual alerts, provider credential rotation and incident review are still operator setup; passing local tests does not establish those external outcomes.
