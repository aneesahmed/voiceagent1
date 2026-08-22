"""app/patients_schemas.py -- Pydantic models for the patient REST API.

Server-side validation lives here regardless of what the voice agent
already checked (per the assessment's "do not rely solely on the voice
agent for validation" requirement) -- the API is the source of truth.

Validation logic is written as plain functions (_check_*) rather than
pydantic validators directly, so both PatientCreate (all fields required)
and PatientUpdate (all fields optional, for partial PUTs) can share the
exact same rules without duplicating them.
"""
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]{0,49}$")
_PHONE_RE = re.compile(r"^\d{10}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
    # Canadian provinces accepted too -- the seed data and Meridian's original
    # market are Canadian; the assessment's brief only requires US, so this is
    # a superset rather than a narrower check.
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}
_SEX_VALUES = {"Male", "Female", "Other", "Decline to Answer"}

# Single source of truth for "what does a finished registration need" --
# imported by patients_service.is_registration_complete() and
# patient_tools.py so the voice agent, the REST API's PatientCreate, and
# the "what's still missing" logic can't drift out of sync.
REQUIRED_FIELDS = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
]


def _check_name(v: str) -> str:
    if not v or not _NAME_RE.match(v):
        raise ValueError("must be 1-50 alphabetic characters (hyphens/apostrophes allowed)")
    return v.strip()


def _check_dob(v: date) -> date:
    if v > date.today():
        raise ValueError("date of birth cannot be in the future")
    return v


def _check_sex(v: str) -> str:
    if v not in _SEX_VALUES:
        raise ValueError(f"must be one of {sorted(_SEX_VALUES)}")
    return v


def normalize_us_phone(raw: str) -> str:
    """Strips everything but digits and drops a leading country code 1 --
    e.g. Twilio's Caller ID arrives as E.164 ("+15551234567"), but every
    phone number in this system is stored/compared as bare 10 digits.
    Shared by validation here and by the voice-agent tools (patient_tools.py)
    so a real ANI lookup and a manually-typed number normalize identically."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _check_phone(v: str) -> str:
    digits = normalize_us_phone(v)
    if not _PHONE_RE.match(digits):
        raise ValueError("must be a valid U.S. 10-digit phone number")
    return digits


def _check_email(v: str) -> str:
    if not _EMAIL_RE.match(v):
        raise ValueError("must be a valid email address")
    return v


def _check_city(v: str) -> str:
    if not (1 <= len(v) <= 100):
        raise ValueError("must be 1-100 characters")
    return v.strip()


def _check_state(v: str) -> str:
    upper = (v or "").strip().upper()
    if upper not in _US_STATES:
        raise ValueError("must be a valid 2-letter state/province abbreviation")
    return upper


def _check_zip(v: str) -> str:
    if not _ZIP_RE.match((v or "").strip()):
        raise ValueError("must be a 5-digit or ZIP+4 U.S. postal code")
    return v.strip()


class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: str = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _check_name(v)

    @field_validator("date_of_birth")
    @classmethod
    def _v_dob(cls, v: date) -> date:
        return _check_dob(v)

    @field_validator("sex")
    @classmethod
    def _v_sex(cls, v: str) -> str:
        return _check_sex(v)

    @field_validator("phone_number")
    @classmethod
    def _v_phone(cls, v: str) -> str:
        return _check_phone(v)

    @field_validator("city")
    @classmethod
    def _v_city(cls, v: str) -> str:
        return _check_city(v)

    @field_validator("state")
    @classmethod
    def _v_state(cls, v: str) -> str:
        return _check_state(v)

    @field_validator("zip_code")
    @classmethod
    def _v_zip(cls, v: str) -> str:
        return _check_zip(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: Optional[str]) -> Optional[str]:
        return _check_email(v) if v else None

    @field_validator("emergency_contact_phone")
    @classmethod
    def _v_emergency_phone(cls, v: Optional[str]) -> Optional[str]:
        return _check_phone(v) if v else None


class PatientUpdate(BaseModel):
    """Same field-level validation as PatientCreate, but every field is
    optional so PUT can do partial updates -- only fields the caller sent
    are validated and applied."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _v_name(cls, v: Optional[str]) -> Optional[str]:
        return _check_name(v) if v is not None else None

    @field_validator("date_of_birth")
    @classmethod
    def _v_dob(cls, v: Optional[date]) -> Optional[date]:
        return _check_dob(v) if v is not None else None

    @field_validator("sex")
    @classmethod
    def _v_sex(cls, v: Optional[str]) -> Optional[str]:
        return _check_sex(v) if v is not None else None

    @field_validator("phone_number")
    @classmethod
    def _v_phone(cls, v: Optional[str]) -> Optional[str]:
        return _check_phone(v) if v is not None else None

    @field_validator("emergency_contact_phone")
    @classmethod
    def _v_emergency_phone(cls, v: Optional[str]) -> Optional[str]:
        return _check_phone(v) if v else None

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: Optional[str]) -> Optional[str]:
        return _check_email(v) if v else None

    @field_validator("city")
    @classmethod
    def _v_city(cls, v: Optional[str]) -> Optional[str]:
        return _check_city(v) if v is not None else None

    @field_validator("state")
    @classmethod
    def _v_state(cls, v: Optional[str]) -> Optional[str]:
        return _check_state(v) if v is not None else None

    @field_validator("zip_code")
    @classmethod
    def _v_zip(cls, v: Optional[str]) -> Optional[str]:
        return _check_zip(v) if v is not None else None


class PatientOut(BaseModel):
    """Fields beyond patient_id/timestamps are Optional here even though
    they're required for a *finished* registration (enforced by
    PatientCreate and by is_complete below) -- the voice agent's
    incremental save_patient can leave a genuinely partial/"draft" row in
    the DB (see CLAUDE.md decision #20), and this is what serializes it
    back out, so it can't assume those fields are always present."""

    patient_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: str
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None
    is_complete: bool = True
