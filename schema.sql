CREATE DATABASE IF NOT EXISTS company;
USE company;

CREATE TABLE IF NOT EXISTS departments (
    dept_id INT PRIMARY KEY,
    dept_name VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS employees (
    id INT PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    salary DECIMAL(10,2) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    dept_id INT,
    CONSTRAINT fk_employees_departments
        FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
);

INSERT IGNORE INTO departments (dept_id, dept_name) VALUES
  (1, 'Human Resources'),
  (2, 'Engineering'),
  (3, 'Finance'),
  (4, 'Operations'),
  (5, 'Sales');

INSERT IGNORE INTO employees (id, first_name, last_name, salary, email, dept_id) VALUES
  (1, 'Alice', 'Johnson', 95000.00, 'alice@company.com', 2),
  (2, 'Bob', 'Martinez', 82000.00, 'bob@company.com', 4),
  (3, 'Carol', 'Williams', 110000.00, 'carol@company.com', 3),
  (4, 'David', 'Brown', 76000.00, 'david@company.com', 1),
  (5, 'Eva', 'Garcia', 98500.00, 'eva@company.com', 5);
