"""app/patients_routes.py -- REST API for patient records
(GET/POST/PUT/DELETE /patients), backed by app/patients_service.py.

Every response uses the assessment-mandated envelope { "data": ..., "error": ... }.
Validation errors from pydantic surface as 422 (via main.py's exception
handler for RequestValidationError); everything else here maps to the
appropriate status explicitly.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import ValidationError

from app.patients_schemas import PatientCreate, PatientOut, PatientUpdate
from app.patients_service import (
    DuplicateContactError,
    create_patient,
    get_patient,
    is_registration_complete,
    list_patients,
    soft_delete_patient,
    update_patient,
)

router = APIRouter(prefix="/patients", tags=["patients"])


def _envelope(data=None, error=None):
    return {"data": data, "error": error}


def _to_out(row: dict) -> PatientOut:
    return PatientOut(**row, is_complete=is_registration_complete(row))


@router.get("")
async def list_patients_route(
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
):
    rows = list_patients(last_name=last_name, date_of_birth=date_of_birth, phone_number=phone_number)
    return _envelope(data=[_to_out(r).model_dump() for r in rows])


@router.get("/{patient_id}")
async def get_patient_route(patient_id: str):
    row = get_patient(patient_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No patient found with id {patient_id}")
    return _envelope(data=_to_out(row).model_dump())


@router.post("", status_code=201)
async def create_patient_route(payload: dict, response: Response):
    try:
        patient_in = PatientCreate(**payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))

    try:
        row = create_patient(patient_in)
    except DuplicateContactError as e:
        response.status_code = 409
        return _envelope(
            data={"existing_patient": _to_out(e.existing).model_dump()},
            error=f"An active patient already exists with phone number {patient_in.phone_number}",
        )

    return _envelope(data=_to_out(row).model_dump())


@router.put("/{patient_id}")
async def update_patient_route(patient_id: str, payload: dict):
    try:
        patient_in = PatientUpdate(**payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))

    row = update_patient(patient_id, patient_in)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No patient found with id {patient_id}")
    return _envelope(data=_to_out(row).model_dump())


@router.delete("/{patient_id}")
async def delete_patient_route(patient_id: str):
    deleted = soft_delete_patient(patient_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No patient found with id {patient_id}")
    return _envelope(data={"patient_id": patient_id, "deleted": True})
