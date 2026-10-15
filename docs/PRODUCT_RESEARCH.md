# Shiftspace product research and upgrade decisions

Reviewed October 15, 2026. Scope: a Virginia workforce app for 1-5 stores and up to 100 staff, keeping Today focused on the clock and schedule. The requested priority is balanced scheduling, shift requests and payroll review.

This research uses public first-party product documentation, not hands-on trials of paid competitor plans. A documented capability is evidence of that workflow, not independent proof of vendor performance, legal compliance, value or reliability. Plans, account settings and regional availability can affect access. No competitor prices or benchmark rankings are inferred.

## Comparison

| Product | Documented workflow | Decision for Shiftspace |
| --- | --- | --- |
| Homebase | Reuse schedules, account for availability, expose labor estimates and post open shifts. | Copy a previous week into private drafts, show a planning-only wage estimate, and validate employee availability. [Scheduling](https://www.joinhomebase.com/employee-scheduling/auto-scheduling), [open shifts](https://support.joinhomebase.com/s/article/Open-Shifts-in-Homebase). |
| 7shifts | Employees cannot see unpublished schedules; managers review shift-pool requests. | Give draft publication an explicit action. Coverage needs a separate manager decision before assignment changes. [Build/publish](https://kb.7shifts.com/hc/en-us/articles/33382453099923-Build-and-publish-a-schedule-Getting-Started-for-Managers), [request approval](https://kb.7shifts.com/hc/en-us/articles/4417505005459-Approve-or-Deny-Shift-Pool-Requests). |
| When I Work | Employees state availability and request coverage; offering a shift does not immediately remove responsibility. | Staff own recurring availability. An offer stays assigned until another coworker requests it and a manager approves. [Availability](https://help.wheniwork.com/articles/setting-your-availablity/), [coverage](https://help.wheniwork.com/articles/getting-your-shifts-covered/). |
| Deputy | Scheduling distinguishes planned work from actual timesheets, supports week copying, publication, labor projections and calendar access. | Keep planned cost separate from pay; preserve local wall-clock hours when copying across daylight-saving changes; provide a personal calendar download. [Schedule overview](https://help.deputy.com/hc/en-au/articles/4688713423759-Schedule-overview), [timesheet states](https://developer.deputy.com/docs/timesheets-overview). |
| Connecteam | Recurring unavailability informs planning; payroll preparation separates issue resolution, approval and export, with protected periods. | Flag existing availability conflicts, stop invalid new assignments, surface open punches/manual discrepancies, and preserve approved payroll. Formal period closing remains a later feature. [Unavailability](https://help.connecteam.com/en/articles/5839157-recurring-unavailability), [payroll preparation](https://help.connecteam.com/en/articles/4301600-preparing-for-payroll-from-a-to-z-with-connecteam), [approval/locking](https://help.connecteam.com/en/articles/5439640-approving-employee-timesheets-for-payroll). |
| Gusto | Reviewed hours flow into payroll; its integration API syncs approved timesheets with an asynchronous status. Time tracking and scheduling can support exception review. | First implement traceable pending timesheets from actual clock intervals and block stale-source approvals. A real payroll integration needs credentials, provider contract, idempotent delivery and reconciliation. [Time tracking](https://support.gusto.com/article/112700542100000/set-up-and-manage-gusto-time-tracking-for-admins), [payroll sync API](https://docs.gusto.com/app-integrations/reference/post-companies-company_uuid-time_tracking-payroll_syncs). |
| ADP | Time, schedule, employee self-service and payroll exchange data to reduce re-entry and highlight exceptions. | Connect existing clocks to a review step instead of asking managers to retype hours. Keep unresolved exceptions visible. [Time and attendance](https://www.adp.com/what-we-offer/time-and-attendance.aspx). |
| Slack | Threads organize replies around their original message. | Consider threaded discussions and message search after the balanced operations upgrade; adding channels alone does not solve busy conversations. [Threads](https://slack.com/help/articles/115000769927-Use-threads-to-organize-discussions--Use-threads-to-organize-discussions--). |
| Discord | Private channels depend on role/channel permissions and removal of default visibility. | Visual similarity is separate from access control. Existing store channels remain shared; future private groups require a membership model enforced on reads, polling, search, attachments and exports. [Roles and permissions](https://support.discord.com/hc/en-us/articles/214836687-Discord-Roles-and-Permissions). |

## Selected upgrade

### 1. Prepare and publish schedules

Managers can save a new shift as a private draft or share it immediately. Existing published shifts retain their visibility when edited. A published shift cannot silently become a hidden draft; editing it still notifies the affected staff. Drafts are absent from staff schedules, Today and calendar downloads.

The week banner publishes the currently displayed draft set, including the selected employee filter. It checks the exact displayed snapshot and revalidates active employees, overlaps, approved time off and recurring availability. One invalid item prevents the whole publication. Each affected person receives one private in-app notice for the batch. This keeps review deliberate without adding a separate dashboard.

### 2. Reuse a week

The collapsed Week tools section copies shifts that start in the previous week into the displayed week. Copying respects the employee filter. Both source and target snapshots must still match. Every employee/date is validated before anything is saved, the batch is atomic, and the result is private drafts without notifications. A repeat submission cannot silently duplicate the roster.

Copying preserves local clock times instead of adding a fixed 168 UTC hours. A destination that falls into an ambiguous/nonexistent local clock time is rejected for manual review. Copying accepts at most 1,000 source shifts and reports the bound explicitly; it does not silently truncate a large schedule. Existing cross-week continuations are not copied as new starts.

### 3. Recurring employee availability

Staff set Any time, Specific hours or Unavailable for each weekday, in the store's time zone. One contiguous window per day keeps the form understandable. Overnight availability is split across both dates. An end of midnight means the end of that day. Submission versions reject stale edits, and supplied employee IDs cannot change another person's availability.

No submission means unknown availability, so existing installations do not receive fabricated restrictions. Submitted restrictions are checked during scheduling, copying, publication, coverage requests and coverage approval. A later availability change preserves existing shifts, flags conflicts and notifies managers to review them. It does not silently cancel work. Effective-date exceptions, manager approval of availability changes and multiple daily windows remain later extensions.

### 4. Approved shift coverage

An employee can offer their own future published shift. Another active coworker can request it only if their current shifts, approved leave and availability permit it. There is one pending claimant at a time. A manager who is not involved in the transfer makes the final decision and rechecks eligibility. Approval changes the actual assignment and notifies both people in the same transaction.

Version checks and a database constraint protect active offers. Edited/cancelled shifts close their outstanding requests. Expired, archived, stale or already resolved requests cannot change assignments. Declining reopens the offer; withdrawal preserves the original shift. These are one-way coverage transfers, not two-way trades or unassigned manager-created open shifts. Only offered shifts become visible to coworkers through the coverage board; personal rosters remain private.

### 5. Planned labor and personal calendars

Managers can expand Week tools to see estimated cost and people above 40 planned hours in the displayed local week. The calculation includes drafts, clips cross-week intervals, uses Decimal and current profile rates, and labels the result as a projection. It does not represent actual wages, taxes, tips or work at another store.

Personal calendar downloads contain only the signed-in employee's published shifts for the selected week. UTC event times avoid calendar-zone ambiguity, stable event IDs and sequence values support updates, and escaped/folded UTF-8 content follows [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545). Downloads require authentication and are not cached. They are snapshots, not a live calendar subscription; changed/cancelled events require the employee to update their calendar.

### 6. Review actual hours before payroll

The Clock hours screen compares published plans with actual completed punches and existing payroll. It splits overnight work at each local midnight, measures elapsed UTC duration through clock changes, sums exact intervals before rounding daily totals, and deducts no breaks automatically. Actual payable work and compensable breaks still need human confirmation. Scheduled hours never replace actual work automatically.

An authorized reviewer can prepare the displayed week as pending daily timesheets. Open punches, protected/imported/voided entries, future work and mismatched manual totals block the operation. Existing notes and compensation snapshots are retained. Source/target snapshots reject stale previews and repeated writes, and the SQLite writer is acquired before command validation across workers. Daily records retain their clock-source fingerprint. Later punch corrections are flagged and block approval until reconciled. Preparation never approves wages, runs taxes or initiates payment; employees can inspect only their own clock review.

CSV and PDF exports also disclose manual/imported/clock origins and changed clock sources, including changes after an earlier approval. Source validation batches punch reads across employees/workweeks rather than issuing a separate database query for every timesheet. These disclosures remain estimates and reconciliation aids, not a provider-certified payroll export or payment receipt.

The separation follows the [DOL's recordkeeping guidance](https://www.dol.gov/general/topic/workhours/hoursrecordkeeping), which requires accurate actual hours and workweek records. The existing reviewed Virginia date/rate gate remains bounded by the [official state statute](https://law.lis.virginia.gov/vacode/title40.1/chapter3/section40.1-28.10/). These references do not establish automatic compliance or resolve employee classification, tips, paid/unpaid-break policy, imported data or cross-location overtime.

## Engineering quality

“FAANG level” is not a certification that a feature count or visual design can establish. For this application it means reviewable rules, protected data, understandable operations and measured behavior. The implementation retains the existing modular Flask app and adds focused planning/time-review services; a distributed rewrite would add deployment complexity without solving the pilot's current needs.

| Quality concern | Concrete acceptance condition |
| --- | --- |
| Authorization | Ordinary staff cannot publish/copy/approve coverage/prepare wages, access another employee's clock review, or use another store's IDs/cookies. |
| Money and time | Decimal totals, local-midnight splits, actual DST elapsed time, explicit workweeks, retained rate snapshots, no invented break deductions. |
| Atomic commands | Batch validation before insertion, writer serialization, version/snapshot checks, database uniqueness, changes/audits/notices committed together. |
| Retries | Replays cannot duplicate shifts, active requests or daily payroll; stale snapshots return conflicts for review. This is not a claim of exactly-once external payroll delivery. |
| Migration | Additive schema upgrade preserves original values and makes existing shifts published; fresh and existing installations are tested. |
| Accessibility | Native buttons/forms, labels, keyboard operation, visible feedback, responsive layouts and usable targets; a full accessibility conformance audit remains separate. |
| Operations | Existing private storage, MFA, shared rate limits, backup/readiness/monitoring controls remain; hosted deployment and real operator drills are still required. |

These choices are informed by [Amazon's retry/idempotency guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/), [Google SRE's user-facing service objectives](https://sre.google/workbook/implementing-slos/) and [W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum). Product examples inform workflow decisions; these engineering sources inform correctness and verification.

Suggested pilot objectives are at least 99.9% successful interactive requests outside announced maintenance and p95 below one second for ordinary interactive writes. They are proposed measurements, not achieved production claims or contractual promises. Bulk scheduling, exports and backup maintenance need separate budgets. Measure user-visible failures, latency, stale conflicts, missed punches, failed notifications, backup age and actual recovery time before making stronger reliability claims.

## Further expansion, in dependency order

1. **Payroll operations:** reviewed pay-period closure/reopening, employee change requests, break policies, provider-specific exports, then a sandbox payroll integration with delivery receipts, signed webhooks and an outbox/retry queue. Never label a generic CSV or a prepared record as paid.
2. **Staffing intelligence:** role/qualification coverage requirements, target headcount, staffing gaps and reviewed availability exceptions; only then add constraint-based suggested schedules with explanations. Current data does not support trustworthy sales-demand or AI forecasting.
3. **Team communication:** searchable/threaded messages, pinned announcements, receipt preferences, membership-enforced private groups and a retention/moderation policy. Real-time delivery must include reconnect/history recovery and maintain store isolation.
4. **Multi-location payroll:** owner-verified employment identities and a reviewed employer-level workweek, including work across locations, before any combined overtime. Matching email/name alone is not a safe identity merge.
5. **Larger deployments:** explicit PostgreSQL migration with tenant constraints, pooled connections, distributed job processing, contract/load tests, data-retention controls and recovery exercises. Keep the current single-instance SQLite design within its verified pilot scope.
6. **Enterprise access:** department permissions, SSO, business/email verification, operator onboarding and security/incident procedures. Changes affecting regulated employment/payroll policy require qualified review.

This ordering follows the requested balanced upgrade and keeps everyday screens small. The verification record, current PR checks and browser captures establish what was actually exercised; this document does not declare an unperformed integration, hosted SLO, legal approval or enterprise-scale deployment complete.
