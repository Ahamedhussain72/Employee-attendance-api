"""
Employee Attendance & Analytics API
====================================
Complete FastAPI application implementing employee management, punch-in/punch-out
with concurrency protection, manual corrections with audit trail, and MongoDB
aggregation-based analytics.

Business rules R1–R10 are implemented as documented in PROBLEM_STATEMENT.
All application code is kept in this single module per assignment requirements.
"""

import os
import math
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List, Dict, Any
from enum import Enum

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator, ConfigDict
from bson import ObjectId

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "attendance_db")
DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "20"))
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "100"))

logger = logging.getLogger("attendance_api")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
client: Optional[AsyncIOMotorClient] = None
db = None


def get_collection_names():
    return {
        "employees": "employees",
        "attendance_logs": "attendance_logs",
        "audit_trail": "audit_trail",
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class PydanticObjectId(str):
    """Custom type so Pydantic can handle bson ObjectId values."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError("Invalid ObjectId")


class DepartmentEnum(str, Enum):
    ENGINEERING = "Engineering"
    HR = "HR"
    SALES = "Sales"
    MARKETING = "Marketing"
    FINANCE = "Finance"
    OPERATIONS = "Operations"


class EmployeeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=200)
    department: DepartmentEnum
    designation: str = Field(..., min_length=1, max_length=100)
    salary: float = Field(..., gt=0)
    join_date: str = Field(..., description="ISO date string YYYY-MM-DD")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v or "." not in v:
            raise ValueError("Invalid email format")
        return v.lower()

    @field_validator("join_date")
    @classmethod
    def validate_join_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("join_date must be YYYY-MM-DD")
        return v


class EmployeeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = Field(None, min_length=5, max_length=200)
    department: Optional[DepartmentEnum] = None
    designation: Optional[str] = Field(None, min_length=1, max_length=100)
    salary: Optional[float] = Field(None, gt=0)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if "@" not in v or "." not in v:
                raise ValueError("Invalid email format")
            return v.lower()
        return v


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(..., alias="_id")
    name: str
    email: str
    department: str
    designation: str
    salary: float
    join_date: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PunchInRequest(BaseModel):
    employee_id: str = Field(..., description="MongoDB ObjectId of the employee")
    punch_in_time: Optional[str] = Field(
        None, description="ISO 8601 datetime; defaults to now"
    )

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, v: str) -> str:
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid employee_id")
        return v

    @field_validator("punch_in_time")
    @classmethod
    def validate_punch_in_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("punch_in_time must be ISO 8601 format")
        return v


class PunchOutRequest(BaseModel):
    employee_id: str = Field(..., description="MongoDB ObjectId of the employee")
    punch_out_time: Optional[str] = Field(
        None, description="ISO 8601 datetime; defaults to now"
    )

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, v: str) -> str:
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid employee_id")
        return v

    @field_validator("punch_out_time")
    @classmethod
    def validate_punch_out_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("punch_out_time must be ISO 8601 format")
        return v


class CorrectionRequest(BaseModel):
    employee_id: str = Field(...)
    attendance_log_id: str = Field(...)
    field_name: str = Field(..., description="Field to correct: punch_in or punch_out")
    new_value: str = Field(..., description="New ISO 8601 datetime value")
    reason: str = Field(..., min_length=3, max_length=500)
    corrected_by: str = Field(..., min_length=1, max_length=100)

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, v: str) -> str:
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid employee_id")
        return v

    @field_validator("attendance_log_id")
    @classmethod
    def validate_log_id(cls, v: str) -> str:
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid attendance_log_id")
        return v

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, v: str) -> str:
        if v not in ("punch_in", "punch_out"):
            raise ValueError("field_name must be 'punch_in' or 'punch_out'")
        return v

    @field_validator("new_value")
    @classmethod
    def validate_new_value(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("new_value must be ISO 8601 format")
        return v


class AttendanceLogResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(..., alias="_id")
    employee_id: str
    punch_in: str
    punch_out: Optional[str] = None
    work_hours: Optional[float] = None
    status: str
    is_corrected: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


class AnalyticsResponse(BaseModel):
    department: str
    total_employees: int
    present_today: int
    absent_today: int
    avg_work_hours: float
    total_overtime_hours: float
    attendance_rate: float


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def utcnow_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(dt_str: str) -> datetime:
    """Parse an ISO 8601 string into an aware datetime."""
    if dt_str is None:
        return None
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def serialize_doc(doc: Optional[Dict]) -> Optional[Dict]:
    """Convert a MongoDB document to a JSON-safe dict with string IDs."""
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            result[key] = [
                serialize_doc(v) if isinstance(v, dict) else (str(v) if isinstance(v, ObjectId) else v)
                for v in value
            ]
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        else:
            result[key] = value
    return result


def calculate_work_hours(punch_in: str, punch_out: Optional[str]) -> Optional[float]:
    """Calculate work hours rounded to 2 decimal places."""
    if not punch_in or not punch_out:
        return None
    pi = parse_iso(punch_in)
    po = parse_iso(punch_out)
    if po <= pi:
        return 0.0
    delta = po - pi
    return round(delta.total_seconds() / 3600, 2)


def determine_status(punch_in: str, punch_out: Optional[str]) -> str:
    """
    R2: Determine attendance status.
    - 'present' if punched in
    - 'completed' if punched in and punched out
    - 'absent' if no log exists (used in analytics)
    """
    if punch_in and punch_out:
        return "completed"
    elif punch_in:
        return "present"
    return "absent"


def is_today(dt_str: str) -> bool:
    """Check if the given ISO datetime string is today (UTC)."""
    if not dt_str:
        return False
    dt = parse_iso(dt_str)
    today = datetime.now(timezone.utc).date()
    return dt.date() == today


def get_date_string(dt_str: str) -> str:
    """Extract YYYY-MM-DD from an ISO datetime string."""
    dt = parse_iso(dt_str)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Business rules R1–R10
# ---------------------------------------------------------------------------
# R1: Employee email must be unique.
# R2: Punch-in creates an attendance log; status = 'present'.
# R3: Punch-out updates the log; status = 'completed'; work_hours calculated.
# R4: Cannot punch in twice on the same day (concurrency-protected).
# R5: Cannot punch out without a punch-in on the same day.
# R6: Punch-out time must be after punch-in time.
# R7: Manual corrections are allowed with reason; audit trail recorded.
# R8: Corrected punch-out must still be after punch-in.
# R9: Standard work day = 8 hours; overtime = work_hours - 8 (> 0).
# R10: Attendance rate = present / total_employees per department.


async def check_duplicate_email(email: str, exclude_id: Optional[str] = None) -> None:
    """R1: Ensure email uniqueness."""
    query = {"email": email.lower()}
    if exclude_id:
        query["_id"] = {"$ne": ObjectId(exclude_id)}
    existing = await db[collection_employees].find_one(query)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Employee with email '{email}' already exists.",
        )


async def check_today_punch_in(employee_id: str) -> Optional[Dict]:
    """R4: Check if employee already punched in today."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = await db[collection_attendance].find_one({
        "employee_id": employee_id,
        "punch_in_date": today_str,
    })
    return log


# Collection name shortcuts (assigned in lifespan)
collection_employees = "employees"
collection_attendance = "attendance_logs"
collection_audit = "audit_trail"


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Employee Attendance & Analytics API",
    description=(
        "Complete API for employee management, attendance tracking with "
        "punch-in/punch-out, manual corrections with audit trail, and "
        "analytics powered by MongoDB aggregations."
    ),
    version="1.0.0",
)


@app.on_event("startup")
async def startup_db_client():
    """Connect to MongoDB and create indexes."""
    global client, db
    logger.info("Connecting to MongoDB at %s", MONGODB_URL)
    client = AsyncIOMotorClient(MONGODB_URL)
    # Touch the server to trigger connection early (helps in testing)
    try:
        await client.admin.command("ping")
    except Exception as exc:
        logger.warning("MongoDB ping failed on startup: %s", exc)
    db = client[MONGODB_DATABASE]
    await create_indexes()


@app.on_event("shutdown")
async def shutdown_db_client():
    """Close MongoDB connection."""
    global client
    if client:
        client.close()
        logger.info("MongoDB connection closed.")


async def create_indexes():
    """Create MongoDB indexes for query performance."""
    # Employee indexes
    await db[collection_employees].create_index("email", unique=True)
    await db[collection_employees].create_index("department")

    # Attendance indexes
    await db[collection_attendance].create_index(
        [("employee_id", 1), ("punch_in_date", 1)], unique=True
    )
    await db[collection_attendance].create_index("employee_id")
    await db[collection_attendance].create_index("punch_in_date")
    await db[collection_attendance].create_index("status")

    # Audit indexes
    await db[collection_audit].create_index("attendance_log_id")
    await db[collection_audit].create_index("employee_id")
    await db[collection_audit].create_index([("created_at", -1)])

    logger.info("MongoDB indexes created.")


# ---------------------------------------------------------------------------
# Employee endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Employees"],
    summary="Create a new employee",
)
async def create_employee(emp: EmployeeCreate):
    """Create a new employee record. R1: email must be unique."""
    await check_duplicate_email(emp.email)

    now = utcnow_iso()
    doc = {
        "name": emp.name,
        "email": emp.email.lower(),
        "department": emp.department.value,
        "designation": emp.designation,
        "salary": emp.salary,
        "join_date": emp.join_date,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[collection_employees].insert_one(doc)
    created = await db[collection_employees].find_one({"_id": result.inserted_id})
    return serialize_doc(created)


@app.get(
    "/employees",
    response_model=PaginatedResponse,
    tags=["Employees"],
    summary="List employees with pagination",
)
async def list_employees(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(
        DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page"
    ),
    department: Optional[DepartmentEnum] = Query(None, description="Filter by department"),
    search: Optional[str] = Query(None, description="Search by name or email"),
):
    """List all employees with pagination, optional department filter and search."""
    query: Dict[str, Any] = {}
    if department:
        query["department"] = department.value
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
        ]

    total = await db[collection_employees].count_documents(query)
    skip = (page - 1) * page_size
    cursor = db[collection_employees].find(query).skip(skip).limit(page_size)
    docs = await cursor.to_list(length=page_size)
    items = [serialize_doc(d) for d in docs]
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@app.get(
    "/employees/{employee_id}",
    response_model=EmployeeResponse,
    tags=["Employees"],
    summary="Get a single employee",
)
async def get_employee(employee_id: str):
    """Retrieve a single employee by ID."""
    if not ObjectId.is_valid(employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid employee ID format.",
        )
    doc = await db[collection_employees].find_one({"_id": ObjectId(employee_id)})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )
    return serialize_doc(doc)


@app.put(
    "/employees/{employee_id}",
    response_model=EmployeeResponse,
    tags=["Employees"],
    summary="Update an employee",
)
async def update_employee(employee_id: str, emp: EmployeeUpdate):
    """Update an employee's details. R1: email uniqueness still enforced."""
    if not ObjectId.is_valid(employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid employee ID format.",
        )
    existing = await db[collection_employees].find_one({"_id": ObjectId(employee_id)})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )

    update_data = emp.model_dump(exclude_unset=True)
    if "email" in update_data and update_data["email"] is not None:
        await check_duplicate_email(update_data["email"], exclude_id=employee_id)
        update_data["email"] = update_data["email"].lower()
    if "department" in update_data and update_data["department"] is not None:
        update_data["department"] = update_data["department"].value

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    update_data["updated_at"] = utcnow_iso()
    await db[collection_employees].update_one(
        {"_id": ObjectId(employee_id)}, {"$set": update_data}
    )
    updated = await db[collection_employees].find_one({"_id": ObjectId(employee_id)})
    return serialize_doc(updated)


@app.delete(
    "/employees/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Employees"],
    summary="Delete an employee",
)
async def delete_employee(employee_id: str):
    """Delete an employee and their associated attendance logs."""
    if not ObjectId.is_valid(employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid employee ID format.",
        )
    result = await db[collection_employees].delete_one({"_id": ObjectId(employee_id)})
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )
    # Cascade delete attendance logs and audit trail
    await db[collection_attendance].delete_many({"employee_id": employee_id})
    await db[collection_audit].delete_many({"employee_id": employee_id})
    return None


# ---------------------------------------------------------------------------
# Attendance endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/attendance/punch-in",
    response_model=AttendanceLogResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Attendance"],
    summary="Punch in for the day",
)
async def punch_in(req: PunchInRequest):
    """
    R2: Create an attendance log with punch_in time, status='present'.
    R4: Cannot punch in twice on the same day (concurrency-protected via
    unique compound index on employee_id + punch_in_date).
    """
    # Verify employee exists
    emp = await db[collection_employees].find_one({"_id": ObjectId(req.employee_id)})
    if not emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )

    punch_in_time = req.punch_in_time if req.punch_in_time else utcnow_iso()
    pi_dt = parse_iso(punch_in_time)
    punch_in_date_str = pi_dt.strftime("%Y-%m-%d")

    # R4: Check for duplicate punch-in today
    existing = await db[collection_attendance].find_one({
        "employee_id": req.employee_id,
        "punch_in_date": punch_in_date_str,
    })
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee has already punched in today.",
        )

    now = utcnow_iso()
    doc = {
        "employee_id": req.employee_id,
        "punch_in": punch_in_time,
        "punch_out": None,
        "work_hours": None,
        "status": "present",
        "is_corrected": False,
        "punch_in_date": punch_in_date_str,
        "created_at": now,
        "updated_at": now,
    }

    try:
        result = await db[collection_attendance].insert_one(doc)
    except Exception as exc:
        # Duplicate key from unique index (concurrency race condition)
        if "E11000" in str(exc) or "duplicate key" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee has already punched in today (concurrency protection triggered).",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create attendance log: {exc}",
        )

    created = await db[collection_attendance].find_one({"_id": result.inserted_id})
    return serialize_doc(created)


@app.post(
    "/attendance/punch-out",
    response_model=AttendanceLogResponse,
    tags=["Attendance"],
    summary="Punch out for the day",
)
async def punch_out(req: PunchOutRequest):
    """
    R3: Update attendance log with punch_out time, status='completed',
    work_hours calculated.
    R5: Cannot punch out without a punch-in on the same day.
    R6: Punch-out time must be after punch-in time.
    R9: Work hours and overtime calculated on completion.
    """
    # Verify employee exists
    emp = await db[collection_employees].find_one({"_id": ObjectId(req.employee_id)})
    if not emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )

    punch_out_time = req.punch_out_time if req.punch_out_time else utcnow_iso()
    po_dt = parse_iso(punch_out_time)
    punch_out_date_str = po_dt.strftime("%Y-%m-%d")

    # R5: Find today's punch-in log
    log = await db[collection_attendance].find_one({
        "employee_id": req.employee_id,
        "punch_in_date": punch_out_date_str,
    })
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No punch-in record found for today. Cannot punch out without punching in first.",
        )

    # Already punched out
    if log.get("punch_out") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee has already punched out today.",
        )

    # R6: Punch-out must be after punch-in
    pi_dt = parse_iso(log["punch_in"])
    if po_dt <= pi_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Punch-out time must be after punch-in time.",
        )

    work_hours = calculate_work_hours(log["punch_in"], punch_out_time)

    update_fields = {
        "punch_out": punch_out_time,
        "work_hours": work_hours,
        "status": "completed",
        "updated_at": utcnow_iso(),
    }

    await db[collection_attendance].update_one(
        {"_id": log["_id"]}, {"$set": update_fields}
    )

    updated = await db[collection_attendance].find_one({"_id": log["_id"]})
    return serialize_doc(updated)


@app.get(
    "/attendance/logs",
    response_model=PaginatedResponse,
    tags=["Attendance"],
    summary="List attendance logs with filters and pagination",
)
async def list_attendance_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    employee_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    status_filter: Optional[str] = Query(None, alias="status", description="present, completed, or absent"),
):
    """List attendance logs with optional filters and pagination."""
    query: Dict[str, Any] = {}
    if employee_id:
        if not ObjectId.is_valid(employee_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid employee_id format.",
            )
        query["employee_id"] = employee_id
    if status_filter:
        query["status"] = status_filter

    date_query: Dict[str, Any] = {}
    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_from must be YYYY-MM-DD",
            )
        date_query["$gte"] = date_from
    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_to must be YYYY-MM-DD",
            )
        date_query["$lte"] = date_to
    if date_query:
        query["punch_in_date"] = date_query

    total = await db[collection_attendance].count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        db[collection_attendance]
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    docs = await cursor.to_list(length=page_size)
    items = [serialize_doc(d) for d in docs]
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@app.get(
    "/attendance/logs/{log_id}",
    response_model=AttendanceLogResponse,
    tags=["Attendance"],
    summary="Get a single attendance log",
)
async def get_attendance_log(log_id: str):
    """Retrieve a single attendance log by ID."""
    if not ObjectId.is_valid(log_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log ID format.",
        )
    doc = await db[collection_attendance].find_one({"_id": ObjectId(log_id)})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attendance log not found.",
        )
    return serialize_doc(doc)


# ---------------------------------------------------------------------------
# Manual correction endpoint (R7, R8)
# ---------------------------------------------------------------------------
@app.patch(
    "/attendance/correction",
    response_model=AttendanceLogResponse,
    tags=["Attendance"],
    summary="Manually correct a punch-in or punch-out time",
)
async def manual_correction(req: CorrectionRequest):
    """
    R7: Manual corrections allowed with a reason; audit trail recorded.
    R8: Corrected punch-out must still be after punch-in.
    """
    # Find the attendance log
    log = await db[collection_attendance].find_one({
        "_id": ObjectId(req.attendance_log_id),
        "employee_id": req.employee_id,
    })
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attendance log not found for this employee.",
        )

    old_value = log.get(req.field_name)

    # R8: If correcting punch_out, ensure it's after punch_in
    if req.field_name == "punch_out":
        punch_in_dt = parse_iso(log["punch_in"])
        new_dt = parse_iso(req.new_value)
        if new_dt <= punch_in_dt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Corrected punch-out time must be after punch-in time.",
            )
    elif req.field_name == "punch_in":
        # If there's a punch_out, corrected punch_in must be before it
        if log.get("punch_out"):
            punch_out_dt = parse_iso(log["punch_out"])
            new_dt = parse_iso(req.new_value)
            if new_dt >= punch_out_dt:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Corrected punch-in time must be before punch-out time.",
                )

    # Prepare update
    update_fields: Dict[str, Any] = {
        req.field_name: req.new_value,
        "is_corrected": True,
        "updated_at": utcnow_iso(),
    }

    # Update punch_in_date if punch_in is corrected
    if req.field_name == "punch_in":
        new_dt = parse_iso(req.new_value)
        update_fields["punch_in_date"] = new_dt.strftime("%Y-%m-%d")

    # Recalculate work hours if both times exist
    punch_in_val = req.new_value if req.field_name == "punch_in" else log["punch_in"]
    punch_out_val = req.new_value if req.field_name == "punch_out" else log.get("punch_out")
    if punch_in_val and punch_out_val:
        update_fields["work_hours"] = calculate_work_hours(punch_in_val, punch_out_val)
        update_fields["status"] = "completed"
    elif req.field_name == "punch_in" and not log.get("punch_out"):
        update_fields["status"] = "present"

    # Capture old state for audit
    old_state = serialize_doc(log)

    # Apply the correction
    await db[collection_attendance].update_one(
        {"_id": log["_id"]}, {"$set": update_fields}
    )

    # R7: Record audit trail
    audit_entry = {
        "attendance_log_id": req.attendance_log_id,
        "employee_id": req.employee_id,
        "field_name": req.field_name,
        "old_value": old_value,
        "new_value": req.new_value,
        "reason": req.reason,
        "corrected_by": req.corrected_by,
        "old_state": old_state,
        "created_at": utcnow_iso(),
    }
    await db[collection_audit].insert_one(audit_entry)

    updated = await db[collection_attendance].find_one({"_id": log["_id"]})
    return serialize_doc(updated)


@app.get(
    "/attendance/audit/{log_id}",
    tags=["Attendance"],
    summary="Get audit trail for an attendance log",
)
async def get_audit_trail(log_id: str):
    """Retrieve the full audit trail for a given attendance log."""
    if not ObjectId.is_valid(log_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log ID format.",
        )
    cursor = (
        db[collection_audit]
        .find({"attendance_log_id": log_id})
        .sort("created_at", -1)
    )
    docs = await cursor.to_list(length=100)
    if not docs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No audit trail found for this log.",
        )
    return [serialize_doc(d) for d in docs]


# ---------------------------------------------------------------------------
# Analytics endpoints (R9, R10) — MongoDB aggregations
# ---------------------------------------------------------------------------
STANDARD_WORK_HOURS = 8.0


@app.get(
    "/analytics/department",
    tags=["Analytics"],
    summary="Department-wise attendance analytics",
)
async def department_analytics(
    date_filter: Optional[str] = Query(
        None, description="ISO date YYYY-MM-DD; defaults to today (UTC)"
    ),
):
    """
    R9: Standard work day = 8 hours; overtime = max(0, work_hours - 8).
    R10: Attendance rate = present+completed / total_employees per department.
    Uses MongoDB aggregation pipelines.
    """
    target_date = date_filter if date_filter else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if date_filter:
        try:
            datetime.strptime(date_filter, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_filter must be YYYY-MM-DD",
            )

    # Stage 1: Aggregate employees by department
    emp_pipeline = [
        {
            "$group": {
                "_id": "$department",
                "total_employees": {"$sum": 1},
                "employee_ids": {"$push": {"$toString": "$_id"}},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "total_employees": 1,
                "employee_ids": 1,
            }
        },
    ]

    cursor = db[collection_employees].aggregate(emp_pipeline)
    dept_results = await cursor.to_list(length=100)

    # Stage 2: Fetch attendance logs for the target date
    attendance_logs = await db[collection_attendance].find(
        {"punch_in_date": target_date}
    ).to_list(length=1000)

    # Build a lookup: employee_id -> attendance log
    attendance_by_emp: Dict[str, Dict] = {}
    for log in attendance_logs:
        emp_id = log.get("employee_id")
        if isinstance(emp_id, ObjectId):
            emp_id = str(emp_id)
        attendance_by_emp[emp_id] = log

    analytics = []
    for dept in dept_results:
        total_employees = dept["total_employees"]
        # Match attendance logs to employees in this department
        all_logs: List[Dict] = []
        for eid in dept["employee_ids"]:
            if eid in attendance_by_emp:
                all_logs.append(attendance_by_emp[eid])

        present_count = len(all_logs)
        absent_count = total_employees - present_count

        # Calculate work hours and overtime
        work_hours_list = [
            log["work_hours"] for log in all_logs if log.get("work_hours") is not None
        ]
        avg_work_hours = (
            round(sum(work_hours_list) / len(work_hours_list), 2)
            if work_hours_list
            else 0.0
        )
        total_overtime = round(
            sum(max(0, wh - STANDARD_WORK_HOURS) for wh in work_hours_list), 2
        )
        attendance_rate = round(
            (present_count / total_employees * 100) if total_employees > 0 else 0.0, 2
        )

        analytics.append(
            AnalyticsResponse(
                department=dept["department"],
                total_employees=total_employees,
                present_today=present_count,
                absent_today=absent_count,
                avg_work_hours=avg_work_hours,
                total_overtime_hours=total_overtime,
                attendance_rate=attendance_rate,
            ).model_dump()
        )

    # Sort by department name
    analytics.sort(key=lambda x: x["department"])
    return analytics


@app.get(
    "/analytics/employee/{employee_id}",
    tags=["Analytics"],
    summary="Employee attendance summary",
)
async def employee_analytics(
    employee_id: str,
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
):
    """Aggregated attendance summary for a specific employee."""
    if not ObjectId.is_valid(employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid employee ID format.",
        )
    emp = await db[collection_employees].find_one({"_id": ObjectId(employee_id)})
    if not emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )

    match_query: Dict[str, Any] = {"employee_id": employee_id}
    date_query: Dict[str, Any] = {}
    if date_from:
        date_query["$gte"] = date_from
    if date_to:
        date_query["$lte"] = date_to
    if date_query:
        match_query["punch_in_date"] = date_query

    pipeline = [
        {"$match": match_query},
        {
            "$group": {
                "_id": "$employee_id",
                "total_days": {"$sum": 1},
                "completed_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                },
                "present_only_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "present"]}, 1, 0]}
                },
                "total_work_hours": {"$sum": {"$ifNull": ["$work_hours", 0]}},
                "total_overtime_hours": {
                    "$sum": {
                        "$max": [
                            0,
                            {"$subtract": [{"$ifNull": ["$work_hours", 0]}, STANDARD_WORK_HOURS]},
                        ]
                    }
                },
                "corrected_count": {
                    "$sum": {"$cond": [{"$eq": ["$is_corrected", True]}, 1, 0]}
                },
                "avg_work_hours": {"$avg": {"$ifNull": ["$work_hours", None]}},
            }
        },
    ]

    cursor = db[collection_attendance].aggregate(pipeline)
    results = await cursor.to_list(length=1)
    if not results:
        return {
            "employee_id": employee_id,
            "employee_name": emp["name"],
            "department": emp["department"],
            "total_days": 0,
            "completed_days": 0,
            "present_only_days": 0,
            "total_work_hours": 0.0,
            "total_overtime_hours": 0.0,
            "corrected_count": 0,
            "avg_work_hours": 0.0,
            "avg_daily_hours": 0.0,
        }

    r = results[0]
    avg_wh = r.get("avg_work_hours")
    if avg_wh is not None:
        avg_wh = round(avg_wh, 2)
    else:
        avg_wh = 0.0

    return {
        "employee_id": employee_id,
        "employee_name": emp["name"],
        "department": emp["department"],
        "total_days": r["total_days"],
        "completed_days": r["completed_days"],
        "present_only_days": r["present_only_days"],
        "total_work_hours": round(r["total_work_hours"], 2),
        "total_overtime_hours": round(r["total_overtime_hours"], 2),
        "corrected_count": r["corrected_count"],
        "avg_work_hours": avg_wh,
        "avg_daily_hours": avg_wh,
    }


@app.get(
    "/analytics/summary",
    tags=["Analytics"],
    summary="Overall attendance summary across all departments",
)
async def attendance_summary(
    date_filter: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
):
    """Company-wide attendance summary for a given date (default: today)."""
    target_date = date_filter if date_filter else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if date_filter:
        try:
            datetime.strptime(date_filter, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_filter must be YYYY-MM-DD",
            )

    total_employees = await db[collection_employees].count_documents({})
    present_count = await db[collection_attendance].count_documents({
        "punch_in_date": target_date
    })

    # Aggregation for work hours on that date
    pipeline = [
        {"$match": {"punch_in_date": target_date, "work_hours": {"$ne": None}}},
        {
            "$group": {
                "_id": None,
                "avg_work_hours": {"$avg": "$work_hours"},
                "total_overtime": {
                    "$sum": {"$max": [0, {"$subtract": ["$work_hours", STANDARD_WORK_HOURS]}]}
                },
            }
        },
    ]
    cursor = db[collection_attendance].aggregate(pipeline)
    agg_results = await cursor.to_list(length=1)
    if agg_results:
        avg_wh = round(agg_results[0]["avg_work_hours"], 2)
        overtime = round(agg_results[0]["total_overtime"], 2)
    else:
        avg_wh = 0.0
        overtime = 0.0

    attendance_rate = round(
        (present_count / total_employees * 100) if total_employees > 0 else 0.0, 2
    )

    return {
        "date": target_date,
        "total_employees": total_employees,
        "present": present_count,
        "absent": total_employees - present_count,
        "avg_work_hours": avg_wh,
        "total_overtime_hours": overtime,
        "attendance_rate": attendance_rate,
    }


# ---------------------------------------------------------------------------
# Query execution plan inspection
# ---------------------------------------------------------------------------
@app.get(
    "/analytics/explain",
    tags=["Analytics"],
    summary="Inspect MongoDB query execution plan",
)
async def explain_query(
    collection: str = Query(..., description="Collection name: employees or attendance_logs"),
    employee_id: Optional[str] = Query(None),
    date_filter: Optional[str] = Query(None),
):
    """
    Run MongoDB's explain() on a representative query so the caller can
    inspect the execution plan (index usage, stage details, etc.).
    """
    valid_collections = {collection_employees, collection_attendance}
    if collection not in valid_collections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"collection must be one of: {', '.join(valid_collections)}",
        )

    query: Dict[str, Any] = {}
    if collection == collection_attendance:
        if employee_id:
            query["employee_id"] = employee_id
        if date_filter:
            query["punch_in_date"] = date_filter
    elif collection == collection_employees and employee_id:
        if ObjectId.is_valid(employee_id):
            query["_id"] = ObjectId(employee_id)

    explain_cmd = {
        "explain": {
            "find": collection,
            "filter": query,
        },
        "verbosity": "allPlansExecution",
    }
    try:
        result = await db.command(explain_cmd)
        return serialize_doc(result)
    except NotImplementedError:
        # mongomock does not support the explain command; return a
        # structured fallback so the endpoint is still callable in test envs
        return {
            "note": "explain() not supported by in-memory mock; use real MongoDB for full plan inspection.",
            "collection": collection,
            "filter": serialize_doc(query) or {},
            "indexes": [
                {"key": "email", "unique": True} if collection == "employees" else None,
                {"key": "department"} if collection == "employees" else None,
                {"key": ["employee_id", "punch_in_date"], "unique": True} if collection == "attendance_logs" else None,
                {"key": "employee_id"} if collection == "attendance_logs" else None,
                {"key": "punch_in_date"} if collection == "attendance_logs" else None,
                {"key": "status"} if collection == "attendance_logs" else None,
            ],
        }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"], summary="Health check")
async def health_check():
    """Check API and database health."""
    try:
        await client.admin.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "timestamp": utcnow_iso(),
    }


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.get("/", tags=["System"], summary="API root")
async def root():
    """API information and available endpoints."""
    return {
        "name": "Employee Attendance & Analytics API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "health": "/health",
    }
