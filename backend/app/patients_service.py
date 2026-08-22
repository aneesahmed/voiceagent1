"""app/patients_service.py -- business logic for patient records, shared by
both the REST API (app/patients_routes.py) and the voice agent's tools
(app/patient_tools.py). Neither caller talks to SQLite directly -- this is
the one place that knows the schema and does the actual writes, per the
assessment's "agent must use the REST API or directly invoke the same
service layer" requirement (we chose the latter: an in-process function
call, not the agent looping back over HTTP to its own server).
"""
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from app.patients_db import get_connection
from app.patients_schemas import REQUIRED_FIELDS, PatientCreate, PatientUpdate

_COLUMNS = [
    "patient_id", "first_name", "last_name", "date_of_birth", "sex",
    "phone_number", "email", "address_line_1", "address_line_2", "city",
    "state", "zip_code", "insurance_provider", "insurance_member_id",
    "preferred_language", "emergency_contact_name", "emergency_contact_phone",
    "created_at", "updated_at", "deleted_at",
]


class DuplicateContactError(Exception):
    """Raised when creating a patient whose phone number already matches
    an active (non-deleted) record -- callers should offer to update
    instead of silently creating a duplicate."""

    def __init__(self, existing: dict):
        self.existing = existing
        super().__init__(f"active patient already exists for phone {existing['phone_number']}")


def _row_to_dict(row) -> dict:
    return {col: row[col] for col in _COLUMNS}


def find_active_by_phone(phone_number: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE phone_number = ? AND deleted_at IS NULL",
            (phone_number,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_patients(
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
) -> list[dict]:
    query = "SELECT * FROM patients WHERE deleted_at IS NULL"
    params: list[str] = []
    if last_name:
        query += " AND last_name = ?"
        params.append(last_name)
    if date_of_birth:
        query += " AND date_of_birth = ?"
        params.append(date_of_birth)
    if phone_number:
        query += " AND phone_number = ?"
        params.append(phone_number)
    query += " ORDER BY created_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_patient(patient_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE patient_id = ? AND deleted_at IS NULL",
            (patient_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def create_patient(data: PatientCreate, *, allow_duplicate: bool = False) -> dict:
    """Raises DuplicateContactError if an active patient already has this
    phone number and allow_duplicate is False (the bonus "recognize
    returning callers" behavior -- see app/patient_tools.py)."""
    if not allow_duplicate:
        existing = find_active_by_phone(data.phone_number)
        if existing is not None:
            raise DuplicateContactError(existing)

    now = datetime.now(timezone.utc).isoformat()
    patient_id = str(uuid.uuid4())
    payload = data.model_dump()
    payload["date_of_birth"] = payload["date_of_birth"].isoformat()

    field_cols = _COLUMNS[1:-3]  # every column except patient_id/created_at/updated_at/deleted_at
    insert_cols = ["patient_id", *field_cols, "created_at", "updated_at"]
    with get_connection() as conn:
        conn.execute(
            f"""INSERT INTO patients ({", ".join(insert_cols)})
                VALUES ({", ".join("?" for _ in insert_cols)})""",
            [patient_id, *[payload[c] for c in field_cols], now, now],
        )
    return get_patient(patient_id)  # type: ignore[return-value]


def update_patient(patient_id: str, data: PatientUpdate) -> Optional[dict]:
    existing = get_patient(patient_id)
    if existing is None:
        return None

    updates = data.model_dump(exclude_unset=True)
    if not updates:
        return existing

    if "date_of_birth" in updates and isinstance(updates["date_of_birth"], date):
        updates["date_of_birth"] = updates["date_of_birth"].isoformat()

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{col} = ?" for col in updates)

    with get_connection() as conn:
        conn.execute(
            f"UPDATE patients SET {set_clause} WHERE patient_id = ?",
            [*updates.values(), patient_id],
        )
    return get_patient(patient_id)


def missing_required_fields(row: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if not row.get(f)]


def is_registration_complete(row: dict) -> bool:
    return not missing_required_fields(row)


def create_draft_patient(fields: dict) -> dict:
    """Inserts a new patient row from a partial field set -- unlike
    create_patient(), nothing is required. Used only by the voice agent's
    incremental save (app/patient_tools.py); the REST API's POST /patients
    always goes through create_patient()'s strict, fully-required
    PatientCreate, so a caller of the API directly still gets the
    assessment-mandated all-fields-at-once contract."""
    now = datetime.now(timezone.utc).isoformat()
    patient_id = str(uuid.uuid4())
    payload = dict(fields)
    if isinstance(payload.get("date_of_birth"), date):
        payload["date_of_birth"] = payload["date_of_birth"].isoformat()

    cols = ["patient_id", *payload.keys(), "created_at", "updated_at"]
    with get_connection() as conn:
        conn.execute(
            f"""INSERT INTO patients ({", ".join(cols)}) VALUES ({", ".join("?" for _ in cols)})""",
            [patient_id, *payload.values(), now, now],
        )
    return get_patient(patient_id)  # type: ignore[return-value]


def upsert_patient_by_phone(fields: dict, existing_patient_id: Optional[str] = None) -> dict:
    """The voice agent's one save primitive -- called repeatedly across a
    single call (and, via the phone-number lookup, across separate calls)
    so a dropped call leaves a resumable partial record instead of losing
    everything (see CLAUDE.md decision #20). Updates existing_patient_id
    if given; otherwise looks for an active record matching
    fields['phone_number'] to avoid creating a duplicate; otherwise
    creates a new draft."""
    if existing_patient_id:
        row = update_patient(existing_patient_id, PatientUpdate(**fields))
        if row is None:
            raise ValueError(f"no patient found with id {existing_patient_id}")
        return row

    phone = fields.get("phone_number")
    if phone:
        existing = find_active_by_phone(phone)
        if existing is not None:
            return update_patient(existing["patient_id"], PatientUpdate(**fields))  # type: ignore[return-value]

    return create_draft_patient(fields)


def soft_delete_patient(patient_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE patients SET deleted_at = ?, updated_at = ? WHERE patient_id = ? AND deleted_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), patient_id),
        )
        return cursor.rowcount > 0
