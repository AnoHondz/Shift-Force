# 🏢 Employee Management System
> Full-stack Python + Flask + MySQL CRUD application

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![MySQL](https://img.shields.io/badge/MySQL-8.0-orange)

---

## ✨ Features
- ✅ **Create** — Add new employees with duplicate-ID protection
- ✅ **Read** — View all employees with live search (name, email, ID)
- ✅ **Update** — Edit any employee field via modal
- ✅ **Delete** — Remove employees with confirmation dialog
- ✅ **Dashboard** — Live stats: total count, avg/max/min salary
- ✅ **REST API** — Clean JSON endpoints (Flask + mysql-connector)
- ✅ **Responsive UI** — Works on mobile and desktop

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- MySQL 8.0+

### 2. Clone / download this project
```bash
git clone https://github.com/YOUR_USERNAME/employee-management-system
cd employee-management-system
```

### 3. Set up the database
Open MySQL and run:
```bash
mysql -u root -p < schema.sql
```
Or paste the contents of `schema.sql` into MySQL Workbench / phpMyAdmin.

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env and set your MySQL password
```

Your `.env` should look like:
```
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_actual_password
DB_NAME=company
```

### 5. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 6. Run the app
```bash
python app.py
```

Open **http://localhost:5000** in your browser 🎉

---

## 📁 Project Structure
```
employee_mgmt/
├── app.py              ← Flask backend + REST API
├── schema.sql          ← MySQL table definitions + seed data
├── requirements.txt    ← Python dependencies
├── .env.example        ← Environment variable template
├── README.md
└── templates/
    └── index.html      ← Full frontend (HTML + CSS + JS)
```

---

## 🔌 API Endpoints

| Method   | Endpoint                  | Description            |
|----------|---------------------------|------------------------|
| `GET`    | `/api/employees`          | List all employees     |
| `GET`    | `/api/employees?search=X` | Search employees       |
| `POST`   | `/api/employees`          | Add new employee       |
| `GET`    | `/api/employees/<id>`     | Get single employee    |
| `PUT`    | `/api/employees/<id>`     | Update employee        |
| `DELETE` | `/api/employees/<id>`     | Delete employee        |
| `GET`    | `/api/stats`              | Dashboard statistics   |

---

## 📝 Resume Description

> Built a full-stack Employee Management System using Python (Flask) and MySQL,
> featuring a RESTful API with complete CRUD operations, live search, dashboard
> analytics, and a responsive single-page frontend. Implemented duplicate-ID
> validation, parameterized SQL queries, and environment-based configuration.

---

## 🛠 Tech Stack
- **Backend**: Python 3, Flask, mysql-connector-python
- **Database**: MySQL 8 (raw SQL, no ORM)
- **Frontend**: Vanilla HTML/CSS/JavaScript (no frameworks)
- **API style**: REST JSON
