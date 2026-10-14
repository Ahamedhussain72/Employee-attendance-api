"""
Tests for Attendance punch-in/punch-out and manual correction endpoints.
Covers business rules R2–R8.
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


async def create_test_employee(client, email="test@company.com"):
    """Helper to create an employee and return its ID."""
    resp = await client.post("/employees", json={
        "name": "Test Employee",
        "email": email,
        "department": "Engineering",
        "designation": "Developer",
        "salary": 80000,
        "join_date": "2024-01-01",
    })
    assert resp.status_code == 201
    return resp.json()["_id"]


@pytest.mark.asyncio
async def test_punch_in_success(client):
    """R2: Punch-in creates attendance log with status 'present'."""
    emp_id = await create_test_employee(client, "punchin@company.com")
    now = datetime.now(timezone.utc).isoformat()
    resp = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": now,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "present"
    assert data["punch_out"] is None
    assert data["work_hours"] is None


@pytest.mark.asyncio
async def test_punch_in_duplicate_same_day(client):
    """R4: Cannot punch in twice on the same day."""
    emp_id = await create_test_employee(client, "duppunch@company.com")
    now = datetime.now(timezone.utc).isoformat()
    resp1 = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": now,
    })
    assert resp1.status_code == 201

    resp2 = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": now,
    })
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_punch_in_nonexistent_employee(client):
    """Punch-in for non-existent employee should return 404."""
    resp = await client.post("/attendance/punch-in", json={
        "employee_id": "507f1f77bcf86cd799439011",
        "punch_in_time": datetime.now(timezone.utc).isoformat(),
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_punch_out_success(client):
    """R3: Punch-out completes the log and calculates work_hours."""
    emp_id = await create_test_employee(client, "punchout@company.com")
    punch_in_time = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out_time = punch_in_time + timedelta(hours=8, minutes=30)

    await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_time.isoformat(),
    })

    resp = await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_time.isoformat(),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["punch_out"] is not None
    assert data["work_hours"] is not None
    assert data["work_hours"] == 8.5


@pytest.mark.asyncio
async def test_punch_out_without_punch_in(client):
    """R5: Cannot punch out without a punch-in on the same day."""
    emp_id = await create_test_employee(client, "nopunchin@company.com")
    resp = await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": datetime.now(timezone.utc).isoformat(),
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_punch_out_before_punch_in(client):
    """R6: Punch-out time must be after punch-in time."""
    emp_id = await create_test_employee(client, "beforepunch@company.com")
    punch_in_time = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    punch_out_time = punch_in_time - timedelta(hours=1)

    await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_time.isoformat(),
    })

    resp = await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_time.isoformat(),
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_punch_out_already_completed(client):
    """Cannot punch out twice on the same day."""
    emp_id = await create_test_employee(client, "twiceout@company.com")
    punch_in_time = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out_time = punch_in_time + timedelta(hours=8)

    await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_time.isoformat(),
    })
    await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_time.isoformat(),
    })

    resp2 = await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": (punch_out_time + timedelta(hours=1)).isoformat(),
    })
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_manual_correction_punch_out(client):
    """R7: Manual correction with audit trail."""
    emp_id = await create_test_employee(client, "correct@company.com")
    punch_in_time = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out_time = punch_in_time + timedelta(hours=8)

    in_resp = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_time.isoformat(),
    })
    log_id = in_resp.json()["_id"]

    await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_time.isoformat(),
    })

    corrected_time = (punch_in_time + timedelta(hours=9)).isoformat()
    resp = await client.patch("/attendance/correction", json={
        "employee_id": emp_id,
        "attendance_log_id": log_id,
        "field_name": "punch_out",
        "new_value": corrected_time,
        "reason": "Employee worked late but forgot to punch out",
        "corrected_by": "HR Manager",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_corrected"] is True
    assert data["work_hours"] == 9.0


@pytest.mark.asyncio
async def test_manual_correction_audit_trail(client):
    """R7: Audit trail is recorded after correction."""
    emp_id = await create_test_employee(client, "audit@company.com")
    punch_in_time = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    punch_out_time = punch_in_time + timedelta(hours=8)

    in_resp = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_time.isoformat(),
    })
    log_id = in_resp.json()["_id"]

    await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_time.isoformat(),
    })

    await client.patch("/attendance/correction", json={
        "employee_id": emp_id,
        "attendance_log_id": log_id,
        "field_name": "punch_out",
        "new_value": (punch_in_time + timedelta(hours=9, minutes=30)).isoformat(),
        "reason": "Correcting punch-out time",
        "corrected_by": "Admin",
    })

    audit_resp = await client.get(f"/attendance/audit/{log_id}")
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert len(audit_data) >= 1
    assert audit_data[0]["field_name"] == "punch_out"
    assert audit_data[0]["reason"] == "Correcting punch-out time"


@pytest.mark.asyncio
async def test_correction_punch_out_before_punch_in(client):
    """R8: Corrected punch-out must be after punch-in."""
    emp_id = await create_test_employee(client, "r8test@company.com")
    punch_in_time = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    punch_out_time = punch_in_time + timedelta(hours=8)

    in_resp = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": punch_in_time.isoformat(),
    })
    log_id = in_resp.json()["_id"]

    await client.post("/attendance/punch-out", json={
        "employee_id": emp_id,
        "punch_out_time": punch_out_time.isoformat(),
    })

    # Try to correct punch_out to a time BEFORE punch_in
    bad_time = (punch_in_time - timedelta(hours=1)).isoformat()
    resp = await client.patch("/attendance/correction", json={
        "employee_id": emp_id,
        "attendance_log_id": log_id,
        "field_name": "punch_out",
        "new_value": bad_time,
        "reason": "Testing R8 rule",
        "corrected_by": "Admin",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_attendance_logs(client):
    """List attendance logs with pagination."""
    emp_id = await create_test_employee(client, "listlogs@company.com")
    now = datetime.now(timezone.utc)
    resp = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": now.isoformat(),
    })
    assert resp.status_code == 201

    list_resp = await client.get("/attendance/logs")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_get_single_attendance_log(client):
    """Get a single attendance log by ID."""
    emp_id = await create_test_employee(client, "singlelog@company.com")
    now = datetime.now(timezone.utc)
    in_resp = await client.post("/attendance/punch-in", json={
        "employee_id": emp_id,
        "punch_in_time": now.isoformat(),
    })
    log_id = in_resp.json()["_id"]

    resp = await client.get(f"/attendance/logs/{log_id}")
    assert resp.status_code == 200
    assert resp.json()["_id"] == log_id
