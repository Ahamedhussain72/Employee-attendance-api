# Data Model

## Overview

The Employee Attendance & Analytics API uses MongoDB as its data store with three
collections: `employees`, `attendance_logs`, and `audit_trail`.

## Collections

### 1. `employees`

Stores employee master records.

| Field         | Type    | Required | Description                                      |
|---------------|---------|----------|--------------------------------------------------|
| `_id`         | ObjectId| Auto     | MongoDB primary key                              |
| `name`        | string  | Yes      | Employee full name (1–100 chars)                 |
| `email`       | string  | Yes      | Unique, lowercased email (R1)                    |
| `department`  | string  | Yes      | One of: Engineering, HR, Sales, Marketing, Finance, Operations |
| `designation` | string  | Yes      | Job title (1–100 chars)                          |
| `salary`      | double  | Yes      | Annual salary (> 0)                              |
| `join_date`   | string  | Yes      | ISO date string `YYYY-MM-DD`                     |
| `created_at`  | string  | Auto     | ISO 8601 timestamp of record creation            |
| `updated_at`  | string  | Auto     | ISO 8601 timestamp of last update                |

**Indexes:**
- Unique index on `email` (enforces R1)
- Index on `department` (analytics query performance)

---

### 2. `attendance_logs`

Stores daily punch-in/punch-out records. One document per employee per day.

| Field           | Type    | Required | Description                                              |
|-----------------|---------|----------|----------------------------------------------------------|
| `_id`           | ObjectId| Auto     | MongoDB primary key                                      |
| `employee_id`   | string  | Yes      | References `employees._id` (stored as string)            |
| `punch_in`      | string  | Yes      | ISO 8601 datetime of punch-in (R2)                       |
| `punch_out`     | string  | No       | ISO 8601 datetime of punch-out; null until punched out (R3) |
| `work_hours`    | double  | No       | Calculated: (punch_out - punch_in) in hours; null until completed |
| `status`        | string  | Yes      | `present` (punched in only) or `completed` (punched in and out) |
| `is_corrected`  | boolean | Yes      | `true` if any manual correction has been applied (R7)    |
| `punch_in_date` | string  | Yes      | `YYYY-MM-DD` derived from punch_in; used for R4 duplicate check and analytics |
| `created_at`    | string  | Auto     | ISO 8601 timestamp                                       |
| `updated_at`    | string  | Auto     | ISO 8601 timestamp                                       |

**Indexes:**
- Unique compound index on `(employee_id, punch_in_date)` — enforces R4 (no duplicate punch-in per day)
- Index on `employee_id` — supports per-employee queries
- Index on `punch_in_date` — supports date-range analytics
- Index on `status` — supports status-filtered queries

**Business Rules Enforced:**
- R2: Punch-in creates a new document with `status = "present"`
- R3: Punch-out sets `punch_out`, `work_hours`, and `status = "completed"`
- R4: Unique compound index prevents two punch-ins on the same day
- R5: Punch-out requires an existing punch-in log for the same day
- R6: `punch_out` must be chronologically after `punch_in`
- R9: `work_hours = (punch_out - punch_in)` in hours, rounded to 2 decimals; overtime = max(0, work_hours - 8)

---

### 3. `audit_trail`

Immutable record of every manual correction applied to an attendance log.

| Field               | Type    | Required | Description                                              |
|---------------------|---------|----------|----------------------------------------------------------|
| `_id`               | ObjectId| Auto     | MongoDB primary key                                      |
| `attendance_log_id` | string  | Yes      | References `attendance_logs._id`                         |
| `employee_id`       | string  | Yes      | References `employees._id`                               |
| `field_name`        | string  | Yes      | Which field was corrected: `punch_in` or `punch_out`     |
| `old_value`         | string  | No       | Previous value (ISO 8601 datetime or null)               |
| `new_value`         | string  | Yes      | New value (ISO 8601 datetime)                            |
| `reason`            | string  | Yes      | Human-readable reason for the correction (3–500 chars)   |
| `corrected_by`      | string  | Yes      | Name/ID of the person making the correction              |
| `old_state`         | object  | Yes      | Full snapshot of the attendance log before correction    |
| `created_at`        | string  | Auto     | ISO 8601 timestamp of the correction                     |

**Indexes:**
- Index on `attendance_log_id` — supports audit lookup per log
- Index on `employee_id` — supports audit lookup per employee
- Descending index on `created_at` — supports chronological ordering

---

## Entity Relationships

```
employees (1) ──────── (N) attendance_logs (1) ──────── (N) audit_trail
   _id ────────────── employee_id
                        _id ────────────── attendance_log_id
```

- One employee can have many attendance logs (one per day)
- One attendance log can have many audit trail entries (one per correction)
- Deleting an employee cascades to delete their attendance logs and audit trail

## Data Types Summary

- All datetime fields are stored as ISO 8601 strings (e.g., `2025-10-01T09:00:00+00:00`)
- All date fields are stored as `YYYY-MM-DD` strings
- `employee_id` in `attendance_logs` and `audit_trail` is stored as a **string** (not ObjectId) for simpler query construction
- `_id` fields are ObjectId in MongoDB, serialized to string in API responses
