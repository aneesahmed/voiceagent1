"""app/patients_db.py -- SQLite connection + schema for patient records.

SQLite over Postgres is a deliberate trade-off (see README "Known
limitations"): zero setup, a single file, survives restarts -- more than
enough for this assessment's scale. Swap for Postgres by replacing this
module and patients_service.py's SQL if the project ever needs concurrent
writers or a managed multi-instance deployment.

A fresh connection is opened per call rather than pooled -- SQLite has no
real connection overhead for a file this size, and it sidesteps any
cross-thread sharing issues since FastAPI runs sync DB code via
asyncio.to_thread.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id              TEXT PRIMARY KEY,
    first_name               TEXT,
    last_name                TEXT,
    date_of_birth             TEXT,
    sex                       TEXT CHECK (sex IS NULL OR sex IN ('Male', 'Female', 'Other', 'Decline to Answer')),
    phone_number              TEXT,
    email                     TEXT,
    address_line_1             TEXT,
    address_line_2             TEXT,
    city                      TEXT,
    state                     TEXT,
    zip_code                  TEXT,
    insurance_provider         TEXT,
    insurance_member_id        TEXT,
    preferred_language         TEXT NOT NULL DEFAULT 'English',
    emergency_contact_name      TEXT,
    emergency_contact_phone     TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    deleted_at                TEXT
);
CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients (phone_number);
CREATE INDEX IF NOT EXISTS idx_patients_last_name ON patients (last_name);
"""
# NOTE: columns are nullable (beyond patient_id/created_at/updated_at) so a
# call that drops mid-registration leaves a resumable partial/"draft" row
# instead of nothing -- see patients_service.upsert_patient_by_phone() and
# CLAUDE.md decision #20. Required-ness for a *finished* registration is
# still enforced (by PatientCreate for the REST API's POST /patients, and
# by is_registration_complete() for the voice agent's own logic) -- it's
# just not a hard DB constraint, since a draft is legitimately partial.

_SEED = [
    (
        "11111111-1111-4111-8111-111111111111",
        "Jane", "Doe", "1985-03-14", "Female", "5551234567", "jane.doe@example.com",
        "123 Maple Street", "Apt 4B", "Toronto", "ON", "M4B1B3",
        "Sun Life", "SL-88221", "English", "John Doe", "5559876543",
    ),
    (
        "22222222-2222-4222-8222-222222222222",
        "Carlos", "Mendes", "1978-11-02", "Male", "5552223333", None,
        "45 River Road", None, "Vancouver", "BC", "V5K0A1",
        None, None, "English", None, None,
    ),
]


def _connect() -> sqlite3.Connection:
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(seed: bool = True) -> None:
    """Creates the patients table if missing, and seeds 2 demo records the
    first time the DB is created (skipped if patients already exist, so
    this is safe to call on every server startup)."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        if seed:
            (count,) = conn.execute("SELECT COUNT(*) FROM patients").fetchone()
            if count == 0:
                now = datetime.now(timezone.utc).isoformat()
                for row in _SEED:
                    conn.execute(
                        """INSERT INTO patients (
                            patient_id, first_name, last_name, date_of_birth, sex,
                            phone_number, email, address_line_1, address_line_2, city,
                            state, zip_code, insurance_provider, insurance_member_id,
                            preferred_language, emergency_contact_name, emergency_contact_phone,
                            created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        row + (now, now),
                    )
