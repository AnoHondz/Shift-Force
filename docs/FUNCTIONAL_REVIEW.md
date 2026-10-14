# Functions and logic review — October 14, 2026

The review covered the current workforce workflows and the production-readiness changes for a Virginia pilot of 1–5 stores and up to 100 staff. The local app runs at `http://127.0.0.1:5000`. Public hosting and real payroll/incident sign-offs have not been performed.

## Findings corrected

| Severity | Problem and effect | Correction |
| --- | --- | --- |
| High | Stale payroll pages could approve or void hours changed after the page loaded. | Review/void actions require the displayed version; finalized entries cannot be downgraded through review controls. |
| High | A payroll employee ID above 500 could silently target employee 500. | Record IDs are strictly validated and loaded without pagination-style clamping. |
| High | Wage approval assumed rates outside the researched date range and allowed unsupported mixed-rate workweeks. | The reviewed Virginia policy is bounded to 2026–2028, single-location adult hourly nonexempt work, one rate, and no tips/bonuses/deductions. Unknown dates, imported or mixed-rate workweeks fail the approval gate. |
| High | Missing store storage could leave the database readiness flag true. | A missing registered database reports unavailable and is never recreated by a probe. |
| High | Shutdown during offline migration/backup could leave the maintenance marker behind. | Graceful shutdown unwinds owned maintenance contexts; abrupt host failure still needs a deliberate stale-marker check. |
| Medium | The initial 512 MB hosting proposal left little room above the measured 430.5 MB web-process peak, before backup overhead. | The proposed web plan now has 2 GB RAM; actual hosted capacity remains a pilot check. |
| Medium | Host/UTC calendar dates could advance Virginia payroll/report dates early. | The store's local calendar drives dates, report periods and the “Use Today” control. |
| Medium | Ten IP-only login/MFA/signup attempts could block coworkers sharing store Wi-Fi. | Limits distinguish account/IP attempts and retain a separate IP ceiling. |
| Medium | Password recovery or revocation could retain a previously exposed unfinished authenticator enrollment. | Revocation discards unfinished enrollment while preserving enabled MFA. |
| Medium | Payroll audit targets, export status and inactive dashboard/payroll-summary totals could mislead reviewers. | Audits target payroll records; exports disclose approval/import state; rejected/voided hours and pay are excluded from totals while rejected entries remain visible for correction. |
| Medium | Reassignment/cancellation notices could open the wrong schedule week. | Private notices link to each affected shift's week and notify both people on reassignment. |
| Low | Store-owner security redirects could lose a mounted application prefix. | Hub links preserve the deployment prefix. |

## Function assessment

| Function | Verified behavior | Practical boundary |
| --- | --- | --- |
| Clock in/out | Uses the signed-in employee, server time and UTC storage; concurrent duplicate starts create one active punch. | Clocking does not create or approve payroll automatically. |
| Punch corrections | Manager access, required reason, before/after audit, overlap and stale-version checks. | Corrected punches still need review against the submitted daily timesheet. |
| Schedule | Person/day/time form, atomic multi-day saves, approved-time-off and overlap checks, overnight/DST handling, edits/cancellation, private notifications. | Saved shifts appear immediately; notifications are in-app, without push/SMS/email delivery. |
| Staff accounts | Owner password confirmation, bounded staff roles, session removal, store-bound single-use 30-minute recovery links. | Lost owner credentials/authenticator recovery require trusted server access and identity verification. |
| MFA | Encrypted secrets, authenticator enrollment, one-use recovery codes, atomic replay rejection; privileged production enrollment required. | Actual staff must enroll their own authenticator and retain recovery codes. |
| Payroll | Decimal snapshots, weekly allocation, versioned approvals, disclosed estimate/import status and owner policy evidence. | Eligibility is an owner attestation that must remain current; no automatic classification, cross-location detection, review expiry, tips, bonuses, deductions, taxes, payment processing or legal certification. Staff working across locations need external payroll. |
| Leave | Employee ownership, review permissions, self-review denial, date/overlap guards and approved leave visibility. | Later approval can flag an existing schedule conflict; it does not automatically remove the shift. |
| Chat/pictures | Authenticated authors, store isolation, escaped text, polling/history, normalized protected image uploads and emoji controls. | Channels are shared by all coworkers in the store; there are no private memberships or moderation/retention tools. |
| Store access | Separate databases, signing keys, cookies and limits; owner access restricted to owned locations. | Business labels are not proof of brand ownership; production public owner signup is disabled. |
| Reports | Store-calendar periods, decimal amounts, approval/import disclosure, authorized CSV/PDF and formula-neutralized text. | Estimates and imported records must be distinguished from an actual completed payroll. |
| Recovery | Coherent hub/store snapshot, authenticated encryption, checksums, integrity/reference checks and fresh-directory restoration. | Real off-host credentials, replacement-host recovery and alert delivery still need operator setup. |
| Hosting/monitoring | Reviewable Render configuration, production WSGI workers, shared limiter configuration, liveness/readiness and privacy-safe logs. | Paid resources, public TLS/domain and external alerts have not been activated. |

## Evidence and rollout boundary

The complete verification record is in [VERIFICATION.md](VERIFICATION.md). Real Windows and Linux workloads exercised five stores, 100 synthetic staff and 24 concurrent clients through 1,000 base HTTP requests, concurrent clock-write checks and exact restoration of all six databases. The observed Linux CI run also passed Gunicorn, HTTPS and actual shared Redis checks across two workers. This does not claim 100 simultaneous active users or hosted-network/outage evidence. Follow the current PR checks for the final patch's result.

The app is suitable for a portfolio/demo review. A supervised real-store pilot requires the hosting/secret/backup/alert checklist, actual staff MFA enrollment, source-timesheet reconciliation and a documented payroll policy review. A commercial multi-location payroll rollout remains outside the current supported scope. See [DEPLOYMENT.md](DEPLOYMENT.md) and [HISTORY_CLEANUP.md](HISTORY_CLEANUP.md) for the concrete remaining operator actions.
