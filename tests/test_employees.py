"""
Tests for Employee CRUD endpoints.
Uses mongomock for in-memory MongoDB when real MongoDB is unavailable.
"""

import asyncio
import sys
import os

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Ensure the app module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Try to import mongomock for in-memory testing
try:
    import mongomock_motor
    HAS_MOCK = True
except ImportError:
    HAS_MOCK = False


@pytest_asyncio.fixture
async def client():
    """Create a test client with an isolated database."""
    import app.main as app_module

    # Use mongomock if available, else fall back to real MongoDB test DB
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
        # Clean collections before tests
        for col in ["employees", "attendance_logs", "audit_trail"]:
            await app_module.db[col].delete_many({})

    # Create indexes
    await app_module.db["employees"].create_index("email", unique=True)
    await app_module.db["attendance_logs"].create_index(
        [("employee_id", 1), ("punch_in_date", 1)], unique=True
    )

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # Cleanup
    if HAS_MOCK:
        mock_client.close()
    else:
        for col in ["employees", "attendance_logs", "audit_trail"]:
            await app_module.db[col].delete_many({})
        app_module.client.close()


@pytest.mark.asyncio
async def test_create_employee(client):
    """R1: Create a new employee successfully."""
    resp = await client.post("/employees", json={
        "name": "Test User",
        "email": "test.user@company.com",
        "department": "Engineering",
        "designation": "Developer",
        "salary": 80000,
        "join_date": "2024-01-15",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test User"
    assert data["email"] == "test.user@company.com"
    assert data["department"] == "Engineering"
    assert "_id" in data


@pytest.mark.asyncio
async def test_create_employee_duplicate_email(client):
    """R1: Duplicate email should return 409."""
    payload = {
        "name": "First User",
        "email": "dup@company.com",
        "department": "HR",
        "designation": "Manager",
        "salary": 90000,
        "join_date": "2024-01-15",
    }
    resp1 = await client.post("/employees", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/employees", json=payload)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_create_employee_invalid_email(client):
    """R1: Invalid email should return 422."""
    resp = await client.post("/employees", json={
        "name": "Bad Email",
        "email": "not-an-email",
        "department": "Engineering",
        "designation": "Dev",
        "salary": 50000,
        "join_date": "2024-01-15",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_employee(client):
    """Get a single employee by ID."""
    create_resp = await client.post("/employees", json={
        "name": "Get Me",
        "email": "getme@company.com",
        "department": "Sales",
        "designation": "Rep",
        "salary": 60000,
        "join_date": "2024-03-01",
    })
    emp_id = create_resp.json()["_id"]

    resp = await client.get(f"/employees/{emp_id}")
    assert resp.status_code == 200
    assert resp.json()["_id"] == emp_id


@pytest.mark.asyncio
async def test_get_employee_not_found(client):
    """Non-existent employee should return 404."""
    resp = await client.get("/employees/507f1f77bcf86cd799439011")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_employee_invalid_id(client):
    """Invalid ObjectId should return 400."""
    resp = await client.get("/employees/invalid-id")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_employees_pagination(client):
    """List employees with pagination."""
    for i in range(5):
        await client.post("/employees", json={
            "name": f"User {i}",
            "email": f"user{i}@company.com",
            "department": "Engineering",
            "designation": "Dev",
            "salary": 70000 + i * 1000,
            "join_date": "2024-01-01",
        })

    resp = await client.get("/employees?page=1&page_size=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["page"] == 1
    assert data["page_size"] == 3
    assert len(data["items"]) == 3
    assert data["total_pages"] == 2


@pytest.mark.asyncio
async def test_list_employees_filter_department(client):
    """Filter employees by department."""
    await client.post("/employees", json={
        "name": "Eng User",
        "email": "eng@company.com",
        "department": "Engineering",
        "designation": "Dev",
        "salary": 80000,
        "join_date": "2024-01-01",
    })
    await client.post("/employees", json={
        "name": "HR User",
        "email": "hr@company.com",
        "department": "HR",
        "designation": "Manager",
        "salary": 90000,
        "join_date": "2024-01-01",
    })

    resp = await client.get("/employees?department=Engineering")
    assert resp.status_code == 200
    data = resp.json()
    assert all(item["department"] == "Engineering" for item in data["items"])


@pytest.mark.asyncio
async def test_update_employee(client):
    """Update an employee's details."""
    create_resp = await client.post("/employees", json={
        "name": "Original Name",
        "email": "original@company.com",
        "department": "Finance",
        "designation": "Analyst",
        "salary": 70000,
        "join_date": "2024-01-01",
    })
    emp_id = create_resp.json()["_id"]

    resp = await client.put(f"/employees/{emp_id}", json={
        "name": "Updated Name",
        "salary": 85000,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"
    assert data["salary"] == 85000


@pytest.mark.asyncio
async def test_delete_employee(client):
    """Delete an employee and verify cascade."""
    create_resp = await client.post("/employees", json={
        "name": "Delete Me",
        "email": "delete@company.com",
        "department": "Operations",
        "designation": "Lead",
        "salary": 75000,
        "join_date": "2024-01-01",
    })
    emp_id = create_resp.json()["_id"]

    resp = await client.delete(f"/employees/{emp_id}")
    assert resp.status_code == 204

    # Verify deletion
    get_resp = await client.get(f"/employees/{emp_id}")
    assert get_resp.status_code == 404
