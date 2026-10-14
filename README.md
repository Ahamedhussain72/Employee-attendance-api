# Employee Attendance & Analytics API

A complete REST API built with **Python**, **FastAPI**, and **MongoDB** for managing employees, tracking daily attendance via punch-in/punch-out, applying manual corrections with a full audit trail, and generating analytics using MongoDB aggregation pipelines.

## Features

- **Employee Management** — Full CRUD with email uniqueness, department filtering, search, and pagination
- **Attendance Tracking** — Punch-in/punch-out with automatic work-hours calculation
- **Concurrency Protection** — Unique compound index prevents duplicate punch-ins on the same day (R4)
- **Manual Corrections** — Correct punch-in/punch-out times with mandatory reason and full audit trail (R7, R8)
- **Analytics** — Department-wise and per-employee analytics using MongoDB aggregations (R9, R10)
- **Query Plan Inspection** — `explain()` endpoint to inspect MongoDB execution plans and index usage
- **Pagination** — Consistent page/page_size/total/total_pages across all list endpoints
- **Error Handling** — Proper HTTP status codes (400, 404, 409, 422, 500) with descriptive messages
- **Indexes** — Strategic indexes on email, department, employee_id, punch_in_date, status, and audit fields

## Business Rules (R1–R10)

| Rule | Description |
|------|-------------|
| R1 | Employee email must be unique |
| R2 | Punch-in creates an attendance log; status = `present` |
| R3 | Punch-out updates the log; status = `completed`; work_hours calculated |
| R4 | Cannot punch in twice on the same day (concurrency-protected via unique index) |
| R5 | Cannot punch out without a punch-in on the same day |
| R6 | Punch-out time must be after punch-in time |
| R7 | Manual corrections allowed with reason; audit trail recorded |
| R8 | Corrected punch-out must still be after punch-in |
| R9 | Standard work day = 8 hours; overtime = max(0, work_hours − 8) |
| R10 | Attendance rate = present / total_employees per department |

## Project Structure

```
employee-attendance-api/
├── app/
│   ├── __init__.py
│   └── main.py              # All application code (endpoints, models, rules)
├── sample_data/
│   ├── employees.json       # 10 sample employees
│   └── attendance_logs.json # 10 sample attendance logs
├── tests/
│   ├── test_employees.py    # Employee CRUD tests
│   ├── test_attendance.py   # Punch-in/out and correction tests
│   ├── test_analytics.py    # Analytics and explain tests
│   └── test_concurrency.py  # Concurrency protection tests
├── requirements.txt
├── sample_seed.py           # Seeds MongoDB with sample data
├── README.md
├── REVIEW.md
├── DECISIONS.md
├── openapi.yaml             # Full OpenAPI 3.0 specification
├── DATA_MODEL.md            # Data model documentation
├── PROBLEM_STATEMENT.docx   # Original problem statement
├── .env.example
└── .gitignore
```

## Prerequisites

- **Python 3.10+** (tested on 3.11 and 3.12)
- **MongoDB 6.0+** running locally or accessible via connection string
- **pip** (Python package installer)
- **Git** (optional, for version control)

## Windows / VS Code Setup Instructions

### Step 1: Install Python

1. Download Python 3.11+ from [python.org](https://www.python.org/downloads/)
2. During installation, check **"Add Python to PATH"**
3. Verify: open Command Prompt and run `python --version`

### Step 2: Install MongoDB

**Option A — Local MongoDB:**
1. Download MongoDB Community Server from [mongodb.com](https://www.mongodb.com/try/download/community)
2. Run the installer with default settings (MongoDB runs as a Windows service)
3. MongoDB will be available at `mongodb://localhost:27017`

**Option B — MongoDB Atlas (cloud, free tier):**
1. Create a free account at [mongodb.com/atlas](https://www.mongodb.com/atlas)
2. Create a free M0 cluster
3. Get your connection string (looks like `mongodb+srv://user:pass@cluster.mongodb.net`)

### Step 3: Set Up the Project in VS Code

1. **Extract the project ZIP** to a folder, e.g., `C:\projects\employee-attendance-api`

2. **Open VS Code:**
   - Open VS Code
   - File → Open Folder → select `employee-attendance-api`

3. **Open a terminal in VS Code:**
   - Terminal → New Terminal (or press `` Ctrl+` ``)

4. **Create a virtual environment:**
   ```powershell
   python -m venv venv
   ```

5. **Activate the virtual environment:**
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
   > If you get a script execution error, run this first:
   > `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

6. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

7. **Install test dependencies (for in-memory testing without MongoDB):**
   ```powershell
   pip install mongomock-motor
   ```
   > This allows tests to run without a live MongoDB instance.

### Step 4: Configure Environment

1. Copy `.env.example` to `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```

2. Edit `.env` with your MongoDB connection details:
   ```
   MONGODB_URL=mongodb://localhost:27017
   MONGODB_DATABASE=attendance_db
   ```

### Step 5: Run the API

```powershell
uvicorn app.main:app --reload
```

The API will be available at:
- **API:** http://localhost:8000
- **Interactive docs (Swagger UI):** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **OpenAPI JSON:** http://localhost:8000/openapi.json

### Step 6: Seed Sample Data

With MongoDB running and the API configured:

```powershell
python sample_seed.py
```

This inserts 10 sample employees and 10 sample attendance logs.

### Step 7: Run Tests

```powershell
# Install test dependencies if not already installed
pip install pytest pytest-asyncio httpx mongomock-motor

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_employees.py -v

# Run a specific test
pytest tests/test_employees.py::test_create_employee -v
```

> **Note:** Tests use `mongomock-motor` for in-memory MongoDB simulation, so they
> run without a live MongoDB instance. If `mongomock-motor` is not installed,
> tests will fall back to a real MongoDB test database (requires MongoDB running).

## API Endpoints

### Employees

| Method | Path | Description |
|--------|------|-------------|
| POST | `/employees` | Create a new employee |
| GET | `/employees` | List employees (pagination, filter, search) |
| GET | `/employees/{id}` | Get a single employee |
| PUT | `/employees/{id}` | Update an employee |
| DELETE | `/employees/{id}` | Delete an employee (cascade) |

### Attendance

| Method | Path | Description |
|--------|------|-------------|
| POST | `/attendance/punch-in` | Punch in for the day (R2, R4) |
| POST | `/attendance/punch-out` | Punch out for the day (R3, R5, R6) |
| GET | `/attendance/logs` | List attendance logs (filters, pagination) |
| GET | `/attendance/logs/{id}` | Get a single attendance log |
| PATCH | `/attendance/correction` | Manual correction with audit trail (R7, R8) |
| GET | `/attendance/audit/{log_id}` | Get audit trail for a log |

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/department` | Department-wise analytics (R9, R10) |
| GET | `/analytics/employee/{id}` | Per-employee attendance summary |
| GET | `/analytics/summary` | Company-wide summary |
| GET | `/analytics/explain` | MongoDB query execution plan inspection |

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | API root information |
| GET | `/health` | Health check |

## Quick API Examples

### Create an Employee
```bash
curl -X POST http://localhost:8000/employees \
  -H "Content-Type: application/json" \
  -d '{"name":"Jane Doe","email":"jane@company.com","department":"Engineering","designation":"Developer","salary":85000,"join_date":"2024-01-15"}'
```

### Punch In
```bash
curl -X POST http://localhost:8000/attendance/punch-in \
  -H "Content-Type: application/json" \
  -d '{"employee_id":"<EMPLOYEE_ID>"}'
```

### Punch Out
```bash
curl -X POST http://localhost:8000/attendance/punch-out \
  -H "Content-Type: application/json" \
  -d '{"employee_id":"<EMPLOYEE_ID>"}'
```

### Department Analytics
```bash
curl http://localhost:8000/analytics/department
```

## Troubleshooting

### MongoDB Connection Issues
- Verify MongoDB is running: `mongosh` or `mongo` from terminal
- Check the connection string in `.env`
- For Atlas, ensure your IP is whitelisted

### Python Virtual Environment Issues
- Make sure venv is activated (you should see `(venv)` in the terminal prompt)
- On Windows, use `.\venv\Scripts\Activate.ps1` (PowerShell) or `.\venv\Scripts\activate.bat` (CMD)

### Test Issues
- Ensure `mongomock-motor` is installed for in-memory testing
- If using real MongoDB for tests, ensure it's running and accessible

## Technology Stack

- **FastAPI** 0.115.0 — Web framework
- **Motor** 3.5.1 — Async MongoDB driver
- **Pydantic** 2.9.2 — Data validation
- **Pytest** 8.3.3 — Testing framework
- **pytest-asyncio** 0.24.0 — Async test support
- **HTTPX** 0.27.2 — Async HTTP client for testing
