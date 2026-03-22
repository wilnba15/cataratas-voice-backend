from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db import get_db
from app.schemas import AppointmentCreate, AppointmentOut
from app.crud import get_or_create_patient, create_appointment
from app import models
from app.tenancy import require_clinic
import jwt
import os

router = APIRouter(prefix="/appointments", tags=["appointments"])

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-change-me")
JWT_ALGORITHM = "HS256"


def get_current_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")

    token = authorization.split(" ", 1)[1].strip()

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido")


@router.post("", response_model=AppointmentOut)
def create(
    appt: AppointmentCreate,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    if not x_clinic_slug:
        raise HTTPException(status_code=400, detail="Falta header X-Clinic-Slug")

    clinic = require_clinic(db, x_clinic_slug)

    if auth.get("clinic_id") != clinic.id:
        raise HTTPException(status_code=403, detail="No autorizado para esta clínica")

    patient = get_or_create_patient(db, clinic.id, appt.full_name, appt.phone)

    created = create_appointment(
        db,
        clinic_id=clinic.id,
        patient_id=patient.id,
        provider_id=appt.provider_id,
        type_id=appt.type_id,
        start_time=appt.start_time,
    )
    return created


@router.get("")
def list_appointments(
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    if not x_clinic_slug:
        raise HTTPException(status_code=400, detail="Falta header X-Clinic-Slug")

    clinic = require_clinic(db, x_clinic_slug)

    if auth.get("clinic_id") != clinic.id:
        raise HTTPException(status_code=403, detail="No autorizado para esta clínica")

    try:
        appointments = (
            db.query(models.Appointment)
            .filter(models.Appointment.clinic_id == clinic.id)
            .order_by(desc(models.Appointment.start_time))
            .all()
        )

        results = []

        for appt in appointments:
            patient_name = ""
            patient_phone = ""

            if appt.patient:
                patient_name = appt.patient.full_name or ""
                patient_phone = appt.patient.phone or ""

            appt_date = ""
            appt_time = ""

            if appt.start_time:
                appt_date = appt.start_time.strftime("%Y-%m-%d")
                appt_time = appt.start_time.strftime("%H:%M")

            results.append({
                "id": appt.id,
                "patient_name": patient_name,
                "patient_phone": patient_phone,
                "date": appt_date,
                "time": appt_time,
                "status": appt.status or "",
            })

        return results

    except Exception as e:
        print("ERROR /appointments:", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Error interno en appointments: {str(e)}"
        )