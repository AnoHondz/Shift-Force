Employee Management System

A role-based employee management web application built with Flask for tracking employee data, work hours, and payroll reporting.

Overview

This system provides a centralized platform for managing employees, logging worked hours, and generating payroll reports. It replaces manual tracking methods with a structured and secure solution.

Features
User authentication with role-based access (executive & employee)
Employee record management (create, edit, assign roles/departments)
Work-hours logging with validation
Overtime and pay calculation
Salary history tracking
Weekly and monthly reporting
CSV export for payroll data
Tech Stack
Python, Flask
SQLite
HTML, CSS, Jinja2
Werkzeug (authentication)
Setup
git clone <repo-url>
cd employee_mgmt
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app.py

Open: http://127.0.0.1:5000

Resume Line

Developed a role-based employee management system using Flask and SQLite with authentication, work-hour tracking, payroll calculations, and reporting features.

Notes
Includes executive and employee roles
Designed for local development and demonstration
Data stored using SQLite