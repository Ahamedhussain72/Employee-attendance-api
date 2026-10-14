#!/usr/bin/env python3
"""
sample_seed.py
==============
Seeds a MongoDB database with sample employee and attendance data from the
JSON files in sample_data/. Attendance log employee_id placeholders are
replaced with the actual inserted employee ObjectIds.

Usage:
    python sample_seed.py

Environment variables (see .env.example):
    MONGODB_URL       - MongoDB connection string
    MONGODB_DATABASE  - Database name to seed
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "attendance_db")

BASE_DIR = Path(__file__).parent
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"


async def seed():
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[MONGODB_DATABASE]

    # Load employee data
    employees_path = SAMPLE_DATA_DIR / "employees.json"
    with open(employees_path, "r", encoding="utf-8") as f:
        employees = json.load(f)

    # Insert employees and collect their IDs
    inserted_employees = []
    for emp in employees:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        emp["created_at"] = now
        emp["updated_at"] = now
        result = await db["employees"].insert_one(emp)
        inserted_employees.append((emp, str(result.inserted_id)))

    print(f"Inserted {len(inserted_employees)} employees.")

    # Load and fix attendance log data
    attendance_path = SAMPLE_DATA_DIR / "attendance_logs.json"
    with open(attendance_path, "r", encoding="utf-8") as f:
        logs = json.load(f)

    # Assign employee IDs round-robin to attendance logs
    for i, log in enumerate(logs):
        emp_obj_id = inserted_employees[i % len(inserted_employees)][1]
        log["employee_id"] = emp_obj_id
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        log["created_at"] = now
        log["updated_at"] = now
        await db["attendance_logs"].insert_one(log)

    print(f"Inserted {len(logs)} attendance logs.")

    # Create indexes
    await db["employees"].create_index("email", unique=True)
    await db["employees"].create_index("department")
    await db["attendance_logs"].create_index(
        [("employee_id", 1), ("punch_in_date", 1)], unique=True
    )
    await db["attendance_logs"].create_index("employee_id")
    await db["attendance_logs"].create_index("punch_in_date")
    await db["attendance_logs"].create_index("status")
    await db["audit_trail"].create_index("attendance_log_id")
    await db["audit_trail"].create_index("employee_id")
    await db["audit_trail"].create_index([("created_at", -1)])

    print("Indexes created.")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
