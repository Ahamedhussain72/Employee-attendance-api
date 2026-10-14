"""
Tests for Analytics endpoints.
Covers R9 (overtime calculation) and R10 (attendance rate).
"""

import sys
import os
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import mongomock_motor
    HAS_MOCK = True
except ImportError:
    HAS_MOCK = False


@pytest_asyncio.fixture
async def client():
    import app.main as app_module

    if HAS_MOCK:
        from mongomock_motor import AsyncMongoMockClient
        mock_client = AsyncMongoMockClient()
        app_module.client = mock_client
        app_module.db = mock_client["test_attendance_db"]
    else:
        app_module.client = app_module.AsyncIOMotorClient(
            os.getenv("MONGODB_URL", "mongodb://localhost:27017")
        )
        app_module.db = app_module.client["test_attendance_db"]
        for col in ["employees", "attendance_logs", "audit_trail"]:
            await app_module.db[col].delete_many({})

    await app_module.db["employees"].create_index("email", unique=True)
    await app_module.db["attendance_logs"].create_index(
        [("employee_id", 1), ("punch_in_date", 1)], unique=True
    )

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    if HAS_MOCK:
        mock_client.close()
    else:
        for col in ["employees", "attendance_logs", "audit_trail"]:
            await app_module.db[col].delete_many({})
        app_module.client.close()


async def create_employee_and_attendance(
    client,
    email: str,
    department: str,
    punch_in_dt: datetime,
    punch_out_dt: datetime,
):
    """Helper: create employee, punch in, punch out."""
    emp_resp = await client.post("/employees", json={
        "name": f"Employee {email}",
        "email": email,
        "department": department,
        "designation": "Developer",
        "salary": 80000,
        "join_date": "2024-01-01",
    })
    emp_id = emp_resp.json()["_id"]

    await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_dt.isoformat(),
    })
    await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_dt.isoformat(),
    })
    return emp_id


@pytest.mark.asyncio
async def test_department_analytics_structure(client):
    """Analytics response should have correct structure per department."""
    target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    punch_in = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out = punch_in + timedelta(hours=8, minutes=30)

    await create_employee_and_attendance(
        client, "eng1@company.com", "Engineering", punch_in, punch_out
    )

    resp = await client.get("/analytics/department")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    eng = [d for d in data if d["department"] == "Engineering"]
    assert len(eng) == 1
    assert eng[0]["total_employees"] >= 1
    assert eng[0]["present_today"] >= 1
    assert "avg_work_hours" in eng[0]
    assert "total_overtime_hours" in eng[0]
    assert "attendance_rate" in eng[0]


@pytest.mark.asyncio
async def test_overtime_calculation(client):
    """R9: Overtime = work_hours - 8 (only positive)."""
    target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    punch_in = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out = punch_in + timedelta(hours=10)  # 10 hours = 2 hours overtime

    await create_employee_and_attendance(
        client, "overtime@company.com", "Finance", punch_in, punch_out
    )

    resp = await client.get("/analytics/department")
    data = resp.json()
    fin = [d for d in data if d["department"] == "Finance"]
    assert len(fin) == 1
    assert fin[0]["total_overtime_hours"] >= 2.0


@pytest.mark.asyncio
async def test_attendance_rate(client):
    """R10: Attendance rate = present / total_employees per department."""
    target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    punch_in = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out = punch_in + timedelta(hours=8)

    # One present employee
    await create_employee_and_attendance(
        client, "present@company.com", "Marketing", punch_in, punch_out
    )
    # One absent employee
    await client.post("/employees", json={
        "name": "Absent Employee",
        "email": "absent@company.com",
        "department": "Marketing",
        "designation": "Specialist",
        "salary": 70000,
        "join_date": "2024-01-01",
    })

    resp = await client.get("/analytics/department")
    data = resp.json()
    mkt = [d for d in data if d["department"] == "Marketing"]
    assert len(mkt) == 1
    assert mkt[0]["total_employees"] == 2
    assert mkt[0]["present_today"] == 1
    assert mkt[0]["absent_today"] == 1
    assert mkt[0]["attendance_rate"] == 50.0


@pytest.mark.asyncio
async def test_employee_analytics(client):
    """Employee analytics summary should aggregate correctly."""
    punch_in = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out = punch_in + timedelta(hours=9)

    emp_id = await create_employee_and_attendance(
        client, "empanalytics@company.com", "Engineering", punch_in, punch_out
    )

    resp = await client.get(f"/analytics/employee/{emp_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["employee_id"] == emp_id
    assert data["total_days"] >= 1
    assert data["completed_days"] >= 1
    assert data["total_work_hours"] >= 9.0
    assert data["total_overtime_hours"] >= 1.0


@pytest.mark.asyncio
async def test_attendance_summary(client):
    """Overall attendance summary should return correct counts."""
    punch_in = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out = punch_in + timedelta(hours=8)

    await create_employee_and_attendance(
        client, "summary@company.com", "Operations", punch_in, punch_out
    )

    resp = await client.get("/analytics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "date" in data
    assert "total_employees" in data
    assert "present" in data
    assert "absent" in data
    assert "attendance_rate" in data
    assert data["total_employees"] >= 1
    assert data["present"] >= 1


@pytest.mark.asyncio
async def test_explain_query(client):
    """Query execution plan inspection should return plan details."""
    # Create some data first
    await create_employee_and_attendance(
        client,
        "explain@company.com",
        "Engineering",
        datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=17, minute=0, second=0, microsecond=0),
    )

    resp = await client.get("/analytics/explain?collection=employees")
    assert resp.status_code == 200
    data = resp.json()
    assert (
        "queryPlanner" in data
        or "explainVersion" in data
        or "ok" in data
        or "note" in data
    )


@pytest.mark.asyncio
async def test_employee_analytics_not_found(client):
    """Analytics for non-existent employee should return 404."""
    resp = await client.get("/analytics/employee/507f1f77bcf86cd799439011")
    assert resp.status_code == 404
