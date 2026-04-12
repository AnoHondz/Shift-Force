# Workforce Platform (Flask)

A production-minded employee operations platform built with Flask, focused on realistic internal workflows: role-based access, approvals, payroll, leave, reporting, notifications, and auditability.

This project is designed to demo strong full-stack engineering fundamentals for internship and early-career software roles, with emphasis on product thinking, clean architecture, and polished UI execution.

## Why This Project

Many portfolio CRUD apps stop at basic create/read/update/delete flows. This platform models how internal business systems actually work:

1. Different roles need different access and dashboards.
2. Operational actions require review and approval states.
3. Teams need visibility through reporting, notifications, and audit logs.
4. UX quality matters even for internal enterprise software.

## Core Capabilities

1. Role-aware authentication and authorization
2. Distinct admin-like and employee experiences
3. Employee directory with filtering, sorting, and status context
4. Payroll and hours workflows with review and approval states
5. Leave request submission and manager/admin review flows
6. Attendance tracking and activity visibility
7. Notifications center for approvals and system updates
8. Audit trail for key operational and security events
9. Dashboard analytics with KPI cards and charts
10. CSV and PDF report exports for payroll/reporting use cases

## Role-Based Architecture

The system supports multiple roles (executive, admin, HR, payroll, manager, employee) and enforces permission boundaries at the route and UI levels.

1. Admin-like roles can access management dashboards, audit logs, workforce operations, and review actions.
2. Employee users are scoped to personal workflows (hours, leave, profile, notifications, team directory).
3. Public sign-up is restricted to employee accounts to avoid privilege escalation.

## Enterprise Workflow Coverage

1. Payroll lifecycle: submit/update hours, mark reviewed, approve/reject, notify stakeholders.
2. Leave lifecycle: submit request, approve/reject, communicate updates.
3. Notification lifecycle: unread/read states, filtered views, role-aware inbox behavior.
4. Audit lifecycle: log meaningful security and business actions for governance.

## Tech Stack

1. Python 3
2. Flask
3. Flask-Login
4. Flask-SQLAlchemy
5. SQLite
6. Jinja2 templates
7. Bootstrap 5 + custom CSS design system
8. Chart.js (dashboard/report visualizations)
9. ReportLab (PDF report export)
10. Pytest (smoke and behavior tests)

## Project Structure

1. app/: application package
2. app/routes/: blueprint-based routing modules (auth, employees, payroll, leave, reports, notifications, audit, attendance)
3. app/models.py: domain and workflow models
4. app/templates/: cohesive UI templates and reusable components
5. app/static/css/site.css: shared visual system and page-level polish
6. tests/: auth, RBAC, and reporting/payroll test coverage
7. run.py: local entry point

## Local Setup

1. Clone and enter the repo
   - git clone <repo-url>
   - cd employee-Data-base
2. Create and activate a virtual environment
   - python -m venv .venv
   - source .venv/bin/activate
3. Install dependencies
   - pip install -r requirements.txt
4. Start the app
   - python run.py
5. Open in browser
   - http://127.0.0.1:5000

## Test Suite

Run the test suite from repo root:

1. PYTHONPATH=. pytest -q

Current coverage includes:

1. Auth page rendering and signup flow smoke tests
2. Login/logout and RBAC behavior checks
3. Payroll review permission tests
4. Report export and notification scope tests

## Screenshots (Placeholders)

Add screenshots in this section for portfolio presentation:

1. Auth experience (sign-in/sign-up)
2. Admin dashboard
3. Employee workspace dashboard
4. Payroll review workflow
5. Reports and exports
6. Notifications + audit trail

## Recruiter-Friendly Highlights

1. Built a multi-role Flask platform with route-level access control and role-specific dashboard experiences.
2. Implemented realistic operational workflows for payroll and leave approvals with status transitions and user notifications.
3. Designed a cohesive template/component system with polished enterprise UI patterns across dashboards, forms, and data tables.
4. Added auditability and reporting features (CSV/PDF exports, activity logs, notification center) to reflect production system expectations.
5. Wrote and validated behavior-focused tests for authentication, RBAC, payroll review paths, and reporting endpoints.

## Future Enhancements

1. Add timezone-aware datetime handling and SQLAlchemy 2.x modernization cleanup.
2. Expand test coverage for edge-case business rules and route-level regressions.
3. Add richer analytics (trend comparisons and anomaly indicators).
4. Introduce configurable approval policies by department or role.
5. Add CI pipeline with linting, tests, and quality gates.
