# Ano Hondz Workforce Ltd Employee Management System

A modular Flask web application for managing employees, authentication, worked hours, pay calculations, and executive reporting.

## Business Summary

### Problem It Solves

Many organizations rely on manual processes or scattered spreadsheets to track employee records, worked hours, and payroll information. This can lead to errors, weak access control, and slow reporting.

### Solution

This system provides a centralized platform where:

- Executives can manage employees, assign positions and departments, review worked hours, and export reports.
- Employees can log in, update their profile, record hours worked, and view their pay totals.

### Detailed Business Description

1. **Problem Statement**  
Organizations often struggle to manage employee information, worked hours, and pay records efficiently when data is stored manually or across multiple disconnected tools. This creates risks such as inaccurate records, poor access control, delayed reporting, and difficulty tracking payroll-related data. The system addresses this challenge by providing a centralized employee management platform with secure access and role-based controls.

2. **Solution Overview**  
The proposed solution is a web-based employee management system that enables secure user authentication and separates access according to user roles. Executives are granted administrative capabilities such as adding and editing employee records, reviewing hours worked, assigning positions and departments, and generating reports. Employees are provided with controlled access to their personal profile, work-hour entry, and pay calculations. This creates a structured, secure, and efficient workflow for workforce management.

3. **Tech Stack**  
- **Backend Language and Framework:** Python 3.14.0 with Flask 3.1.3  
- **Database:** SQLite 3.50.4  
- **Frontend Technologies:** HTML5, CSS3, Jinja2 3.1.6 templates  
- **Key Libraries/Packages:** Werkzeug 3.1.8, python-dotenv 1.2.2, Flask session management, Python CSV module

4. **System Architecture**  
The application is built using a modular Flask architecture. The frontend interacts with Flask routes, which are separated into functional modules for authentication, employee management, and reporting. Authentication handles account creation, login, hashed passwords, and session-based access control. Employee management handles profile data, departments, positions, schedules, and work-hour logging. Reporting processes hours and pay data into executive summaries and exportable CSV files. All persistent data is stored in a SQLite relational database.

5. **Key Features**  
- Secure signup and login  
- Role-based access for executives and employees  
- Executive-only employee creation and editing  
- Employee directory with search and department filtering  
- Work-hours logging with validation  
- Overtime and total pay calculations  
- Personal profile update functionality  
- Department and position assignment  
- Salary history tracking  
- Weekly and monthly reporting  
- CSV export for payroll and workforce summaries

6. **Target Users**  
The system is intended for businesses, HR departments, administrators, executives, and employees who require a secure and organized way to manage employee information, hours worked, and payroll-related records.

## Overview

This project is a role-based employee management system built for local testing and demonstration. It supports two user roles:

- `executive` users can add and edit employees, review all logged hours, assign positions and pay rates, and export reports.
- `employee` users can sign up, update their profile, log worked hours, and review their pay totals.

The application uses SQLite by default, stores hashed passwords, and protects executive-only pages with role checks.

## Features

- User signup and login with hashed passwords
- Two user roles: executive and employee
- Executive-only employee creation and editing
- Employee directory with search and department filtering
- Hours logging with validation
- Overtime pay calculation
- Employee profile updates for contact information and schedule
- Salary history tracking when pay rates change
- Weekly and monthly executive reports
- CSV export for payroll-style reports
- Modular Flask structure using blueprints

## Tech Stack

- Backend: Flask
- Database: SQLite
- Authentication: Werkzeug password hashing + Flask sessions
- Frontend: HTML + CSS (server-rendered templates)
- Reporting: CSV export

## Project Structure

```text
employee_mgmt/
|-- app.py
|-- ems_app/
|   |-- __init__.py
|   |-- auth.py
|   |-- db.py
|   |-- employees.py
|   `-- reports.py
|-- templates/
|   |-- base.html
|   |-- landing.html
|   |-- login.html
|   |-- signup.html
|   |-- dashboard.html
|   |-- employees.html
|   |-- employee_form.html
|   |-- profile.html
|   |-- hours.html
|   `-- reports.html
|-- schema.sql
|-- requirements.txt
|-- .env.example
`-- README.md
```

## Database Design

The SQLite database stores:

- `users` for login credentials, roles, and employee links
- `employees` for employee profile details, department, schedule, and pay rates
- `departments` for department names
- `hours_logs` for employee work-hour entries
- `salary_history` for pay-rate changes

## Quick Start

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and update values if needed.

Example:

```env
FLASK_SECRET_KEY=ano-hondz-workforce-secret
EXEC_USERNAME=executive
EXEC_PASSWORD=AnoHondz123
EXECUTIVE_SIGNUP_CODE=AHWL-EXEC-2026
```

### 4. Run the app

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Default Local Test Credentials

The app seeds one executive account automatically on startup:

- Username: `executive`
- Password: `AnoHondz123`

Executive signup also requires:

- Invite code: `AHWL-EXEC-2026`

## Main Workflows

### Executive

- Log in
- Add a new employee
- Edit employee position, department, pay, and schedule
- Review all hours and payroll totals
- Export weekly or monthly reports to CSV

### Employee

- Sign up as an employee
- Log in
- View directory and dashboard
- Update personal details
- Log worked hours
- View pay totals including overtime

## Role-Based Access Rules

Executives can:

- Add employees
- Edit employees
- View all hours logs
- View reports
- Export CSV reports

Employees can:

- View their own profile
- Update personal information
- Log hours worked
- Review their own pay totals

Protected routes redirect unauthorized users away from executive-only pages.

## Reporting

The reports section includes:

- Weekly payroll summary
- Monthly payroll summary
- Overtime totals
- Recent salary changes
- CSV export for weekly and monthly reports

## Validation and Security

- Passwords are hashed before storage
- Negative hours and pay rates are rejected
- Executive-only routes are protected with role checks
- Session-based authentication is used for login state
- Employee email addresses must be unique

## Resume Description

Built a modular employee management web application using Flask and SQLite with role-based authentication, employee records, work-hours logging, overtime pay calculation, salary history tracking, and executive reporting with CSV export.

## Notes

- The database file is created automatically for local use.
- Existing SQLite data is upgraded in place when the app starts.
- `schema.sql` is included as a reference snapshot of the current data model.
