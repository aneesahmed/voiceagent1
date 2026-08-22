"""app/patient_tools.py -- Gemini function-calling tools for the
patient-intake persona. The actual DB write goes through
app/patients_service.py -- the same service layer the REST API uses --
not a second copy of the persistence logic.

save_patient is deliberately *incremental*: the agent is instructed (see
app/prompts/templates/patient_intake.yaml) to call it as soon as the
phone number is known and again after each field is collected, not just
once at the very end. That's what makes find_patient_by_phone useful for
more than duplicate-detection -- if a call drops mid-registration, the
next call's find_patient_by_phone finds the partial row and the agent
only asks for whatever's still missing (see CLAUDE.md decision #20).

Each dispatch function returns a small JSON-serializable dict that gets
fed straight back to the model as a function response, so the wording
(e.g. which field was invalid, which fields are still missing) is exactly
what the LLM sees and can relay to the caller.
"""
import logging

from google.genai import types
from pydantic import ValidationError

from app.patients_schemas import PatientUpdate, normalize_us_phone
from app.patients_service import find_active_by_phone, missing_required_fields, upsert_patient_by_phone

logger = logging.getLogger(__name__)

_PATIENT_FIELD_SCHEMA = {
    "first_name": types.Schema(type=types.Type.STRING, description="Patient's first name"),
    "last_name": types.Schema(type=types.Type.STRING, description="Patient's last name"),
    "date_of_birth": types.Schema(type=types.Type.STRING, description="Date of birth, ISO format YYYY-MM-DD"),
    "sex": types.Schema(type=types.Type.STRING, enum=["Male", "Female", "Other", "Decline to Answer"]),
    "phone_number": types.Schema(type=types.Type.STRING, description="10-digit U.S. phone number, digits only"),
    "email": types.Schema(type=types.Type.STRING, description="Email address, optional"),
    "address_line_1": types.Schema(type=types.Type.STRING, description="Street address"),
    "address_line_2": types.Schema(type=types.Type.STRING, description="Apt/suite/unit, optional"),
    "city": types.Schema(type=types.Type.STRING),
    "state": types.Schema(type=types.Type.STRING, description="2-letter U.S. state abbreviation"),
    "zip_code": types.Schema(type=types.Type.STRING, description="5-digit or ZIP+4 postal code"),
    "insurance_provider": types.Schema(type=types.Type.STRING, description="Optional"),
    "insurance_member_id": types.Schema(type=types.Type.STRING, description="Optional"),
    "preferred_language": types.Schema(type=types.Type.STRING, description="Optional, defaults to English"),
    "emergency_contact_name": types.Schema(type=types.Type.STRING, description="Optional"),
    "emergency_contact_phone": types.Schema(type=types.Type.STRING, description="Optional, 10-digit U.S. phone"),
}

FIND_PATIENT_BY_PHONE = types.FunctionDeclaration(
    name="find_patient_by_phone",
    description=(
        "Looks up an active patient record by phone number -- call this FIRST, immediately after the "
        "caller gives their phone number, before asking anything else. The result tells you whether "
        "this is a brand-new caller, a returning caller with a complete record (offer to update "
        "instead of re-registering), or a returning caller with an incomplete record from a call that "
        "was likely interrupted (only ask for the fields listed in missing_fields, not everything again)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"phone_number": _PATIENT_FIELD_SCHEMA["phone_number"]},
        required=["phone_number"],
    ),
)

SAVE_PATIENT = types.FunctionDeclaration(
    name="save_patient",
    description=(
        "Saves whatever patient fields you currently have -- call it incrementally as you go (right "
        "after the phone number is collected, and again after each subsequent field or small group of "
        "fields), not just once at the end. This means the record is never lost if the call drops. "
        "Pass existing_patient_id on every call after the first one succeeds (it's returned in the "
        "response as patient_id) so you keep updating the same record instead of creating new ones. "
        "The response's missing_fields tells you what's still needed before registration is complete -- "
        "do the final read-back/confirmation once missing_fields is empty. If this returns an error, "
        "tell the caller specifically which field was the problem, in plain language, and ask them to "
        "repeat just that one."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            **_PATIENT_FIELD_SCHEMA,
            "existing_patient_id": types.Schema(
                type=types.Type.STRING,
                description="The patient_id from a previous find_patient_by_phone or save_patient call. Omit only on the very first save of a brand-new caller.",
            ),
        },
        # Deliberately no `required` list -- save_patient supports partial,
        # incremental saves (see module docstring). A completely empty call
        # is rejected in _dispatch_save_patient instead.
    ),
)

PATIENT_INTAKE_TOOLS = [types.Tool(function_declarations=[FIND_PATIENT_BY_PHONE, SAVE_PATIENT])]


def _dispatch_find_patient_by_phone(args: dict) -> dict:
    phone = normalize_us_phone(str(args.get("phone_number", "")))
    existing = find_active_by_phone(phone)
    if existing is None:
        return {"found": False}

    missing = missing_required_fields(existing)
    return {
        "found": True,
        "complete": not missing,
        "missing_fields": missing,
        "patient_id": existing["patient_id"],
        # Every non-empty field already on file, so the agent can read the
        # existing record back (or resume collecting only what's missing)
        # without a second round-trip.
        "known_fields": {
            k: v
            for k, v in existing.items()
            if k not in ("patient_id", "created_at", "updated_at", "deleted_at") and v not in (None, "")
        },
    }


def _dispatch_save_patient(args: dict) -> dict:
    existing_id = args.pop("existing_patient_id", None)
    args = {k: v for k, v in args.items() if v not in (None, "")}

    if not args:
        return {"success": False, "error": "No fields provided to save."}

    try:
        partial = PatientUpdate(**args)
    except ValidationError as e:
        first_error = e.errors()[0]
        field = ".".join(str(p) for p in first_error["loc"])
        return {"success": False, "error": f"Invalid {field}: {first_error['msg']}"}

    fields = partial.model_dump(exclude_unset=True)

    try:
        row = upsert_patient_by_phone(fields, existing_patient_id=existing_id)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception:
        logger.exception("save_patient tool call failed")
        return {"success": False, "error": "Internal error while saving the record. Please try again."}

    logger.info("patient saved via voice agent: %s", row)
    missing = missing_required_fields(row)
    return {
        "success": True,
        "patient_id": row["patient_id"],
        "first_name": row.get("first_name"),
        "complete": not missing,
        "missing_fields": missing,
    }


TOOL_DISPATCH = {
    "find_patient_by_phone": _dispatch_find_patient_by_phone,
    "save_patient": _dispatch_save_patient,
}
