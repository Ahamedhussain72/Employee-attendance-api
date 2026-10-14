# Design Decisions

This document records the key technical decisions made during the implementation
of the Employee Attendance & Analytics API.

## D1: Single-Module Application Architecture

**Decision:** All application code is in `app/main.py` rather than splitting into
separate modules (routes, models, services, etc.).

**Rationale:** The assignment explicitly requires "keeping all application code
inside app/main.py." The file is organized with clear section headers
(Configuration, Database Setup, Pydantic Models, Helper Functions, Business Rules,
Endpoints) to maintain readability despite being a single file.

**Trade-off:** A multi-module structure would be more conventional for production
code, but the single-file approach keeps the assignment self-contained and
easier to review.

---

## D2: Motor (Async MongoDB Driver) Over PyMongo

**Decision:** Use `motor` (async MongoDB driver) instead of synchronous `pymongo`.

**Rationale:** FastAPI is an async framework. Using Motor ensures the database
operations don't block the event loop, which is critical for concurrent
punch-in requests (R4). This also aligns with the concurrency testing
requirements.

---

## D3: Unique Compound Index for Concurrency Protection (R4)

**Decision:** Enforce R4 (no duplicate punch-in per day) with a MongoDB unique
compound index on `(employee_id, punch_in_date)` in addition to the
application-level check.

**Rationale:** An application-level `find_one` + `insert_one` sequence has a
race condition: two concurrent requests can both pass the check before either
inserts. A unique index at the database level is the only reliable way to
prevent duplicates under concurrent load. The application-level check remains
for better error messages (409 vs 500), and the `E11000` duplicate key error
is caught and converted to a 409 Conflict response.

---

## D4: `punch_in_date` as a Derived Stored Field

**Decision:** Store `punch_in_date` (a `YYYY-MM-DD` string extracted from the
punch-in datetime) as a separate field in `attendance_logs`, rather than
deriving it on the fly during queries.

**Rationale:**
- Enables the unique compound index for R4
- Makes date-range queries simple and index-efficient: `{punch_in_date: {$gte: "2025-10-01"}}`
- Simplifies the analytics aggregation pipelines by avoiding date extraction in the pipeline
- The small storage overhead is justified by the query performance gain

---

## D5: ISO 8601 Strings Instead of BSON Dates

**Decision:** Store all datetime fields as ISO 8601 strings rather than BSON
Date objects.

**Rationale:**
- Simplifies serialization — no custom JSON encoder needed
- Makes the API responses human-readable
- Pydantic validation can verify the format directly
- The `punch_in_date` field is already a string for indexing purposes

**Trade-off:** BSON Date objects would allow native MongoDB date operators
(`$dayOfMonth`, `$month`) in aggregations. The string approach requires
pre-computing the date, which is actually more efficient for our query patterns.

---

## D6: employee_id Stored as String in Attendance Logs

**Decision:** Store `employee_id` in `attendance_logs` and `audit_trail` as a
string (the string representation of the ObjectId), not as an ObjectId.

**Rationale:**
- Simplifies query construction — no need to convert between ObjectId and string
- Makes the `$lookup` aggregation with `$toString` straightforward
- Consistent with the API contract where IDs are always strings

---

## D7: Audit Trail with Full State Snapshot

**Decision:** Each audit trail entry includes an `old_state` field containing
a full snapshot of the attendance log document before the correction.

**Rationale:**
- Provides complete traceability — you can reconstruct the log at any point in time
- Useful for compliance and dispute resolution
- If someone corrects a log multiple times, the full history is preserved

**Trade-off:** Uses more storage, but audit trails are append-only and
typically small relative to the rest of the database.

---

## D8: MongoDB Aggregation Pipelines for Analytics

**Decision:** Implement analytics using MongoDB aggregation pipelines
(`$lookup`, `$group`, `$project`, `$sum`, `$avg`, `$cond`, `$max`) rather than
fetching documents and computing in Python.

**Rationale:**
- Leverages MongoDB's query engine for efficient server-side computation
- Reduces network traffic — only computed results are returned
- Demonstrates MongoDB aggregation proficiency (an assignment requirement)
- Scales better with large datasets

---

## D9: Standard Work Hours as a Constant

**Decision:** Define `STANDARD_WORK_HOURS = 8.0` as a constant in the code
rather than a configurable setting.

**Rationale:**
- The assignment specifies 8 hours as the standard (R9)
- Making it configurable would add complexity without clear value for this assignment
- If needed, it can easily be moved to an environment variable

---

## D10: mongomock-motor for In-Memory Testing

**Decision:** Tests use `mongomock-motor` for in-memory MongoDB simulation
when a real MongoDB instance is not available.

**Rationale:**
- Tests can run in any environment without MongoDB installed
- Tests are fast (no network I/O)
- Tests are isolated (each test gets a fresh in-memory database)

**Trade-off:** `mongomock-motor` does not perfectly replicate all MongoDB
behaviors (especially some aggregation operators and index enforcement
edge cases). Real MongoDB testing is recommended for production validation,
as noted in REVIEW.md.

---

## D11: Pydantic v2 for Validation

**Decision:** Use Pydantic v2 with `field_validator` decorators and
`ConfigDict(populate_by_name=True)` for response models.

**Rationale:**
- Pydantic v2 is significantly faster than v1
- `field_validator` is the modern replacement for `@validator`
- `populate_by_name` allows response models to accept `_id` from MongoDB
  and expose it as `id` in the response

---

## D12: Query Execution Plan Inspection Endpoint

**Decision:** Add a dedicated `/analytics/explain` endpoint that runs MongoDB's
`explain()` command on representative queries.

**Rationale:**
- The assignment requires query execution plan inspection
- This allows developers to verify that indexes are being used correctly
- Supports `allPlansExecution` verbosity for detailed analysis
- Can be used to compare query plans before and after adding indexes
