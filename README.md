# Flask Employee Management System

A polished employee management portal built with Flask, designed to showcase authentication, role-based access, employee CRUD, payroll tracking, leave management, attendance logging, notifications, audit logs, and executive reporting.

## Project overview

This application provides an HR-style workforce management experience through a clean Bootstrap interface and modular Flask architecture. It includes separate access levels for administrators, managers, and employees, with workflows for attendance, payroll, leave requests, analytics, and in-app assistance.

## Features

- Role-based authentication and secure login
- Admin and manager access controls with employee-level permissions
- Employee directory with search, filtering, and profile editing
- Payroll tracker with regular/overtime calculations
- Leave request submission, approval, and status management
- Attendance clock-in/clock-out tracking with history and live counts
- Notifications center with read/unread tracking
- Audit log view for admin activity review
- Dashboard charts for payroll trends, department distribution, and attendance summary
- Exportable payroll reports as CSV files
- Built-in assistant widget for quick navigation and workflow help

## Tech stack

- Python 3
- Flask
- Flask-Login
- Flask-SQLAlchemy
- SQLite
- Bootstrap 5
- Jinja2 templates

## Project structure

- `app/` — main application package
  - `__init__.py` — app factory, extensions, Jinja filters, and startup code
  - `models.py` — SQLAlchemy models for users, employees, payroll, leave, attendance, notifications, and audit logs
  - `routes/` — blueprint-based route modules for auth, employees, attendance, leave, reports, notifications, audit, and assistant
  - `templates/` — reusable page templates and layout
  - `static/css/site.css` — visual polish and component styling
  - `utils.py` — shared helper utilities for date parsing and current employee resolution
- `config.py` — environment-based configuration values
- `run.py` — application entry point
- `schema.sql` — reference database schema
- `requirements.txt` — Python dependencies

## Setup instructions

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd employee-Data-base
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the application:
   ```bash
   python run.py
   ```
5. Open the app at `http://127.0.0.1:5000`

## Default credentials and configuration

The app initializes with a default admin user if no admin exists:

- Username: `admin`
- Password: `Admin2026!`

You can override these values using environment variables:

- `DEFAULT_ADMIN_USERNAME`
- `DEFAULT_ADMIN_PASSWORD`
- `MANAGER_SIGNUP_CODE`
- `ADMIN_SIGNUP_CODE`
- `FLASK_SECRET_KEY`

## Future improvements

- Add tests for authentication, route permissions, and business rules
- Integrate pagination for large employee and attendance tables
- Add a complete role management dashboard for HR workflows
- Replace the simple assistant with an AI-powered conversational backend
- Add export and visualization support for leave balances and payroll forecasts

## Resume-ready summary

Built a portfolio-ready enterprise-facing employee management system with Flask, secure role-based authentication, payroll and attendance workflows, audit logging, dynamic executive dashboards, and a consistent Bootstrap UI. The project demonstrates full-stack web design, data modeling, access control, and developer-friendly architecture.
