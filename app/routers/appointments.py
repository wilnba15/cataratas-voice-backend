from datetime import datetime
import os

import jwt
from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app import models
from app.crud import create_appointment, get_or_create_patient
from app.db import get_db
from app.schemas import AppointmentCreate, AppointmentOut
from app.tenancy import require_clinic

router = APIRouter(prefix="/appointments", tags=["appointments"])

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-change-me")
JWT_ALGORITHM = "HS256"


class AppointmentUpdate(BaseModel):
    patient_name: str | None = None
    patient_phone: str | None = None
    start_time: datetime | None = None


def get_current_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")

    token = authorization.split(" ", 1)[1].strip()

    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido")


def ensure_clinic_access(db: Session, x_clinic_slug: str | None, auth: dict):
    if not x_clinic_slug:
        raise HTTPException(status_code=400, detail="Falta header X-Clinic-Slug")

    clinic = require_clinic(db, x_clinic_slug)

    if auth.get("clinic_id") != clinic.id:
        raise HTTPException(status_code=403, detail="No autorizado para esta clínica")

    return clinic


def get_clinic_appointment(db: Session, clinic_id: int, appointment_id: int):
    appointment = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.id == appointment_id,
            models.Appointment.clinic_id == clinic_id,
        )
        .first()
    )

    if not appointment:
        raise HTTPException(status_code=404, detail="Cita no encontrada")

    return appointment


def serialize_appointment(appt: models.Appointment):
    patient_name = ""
    patient_phone = ""

    if getattr(appt, "patient", None):
        patient_name = appt.patient.full_name or ""
        patient_phone = appt.patient.phone or ""

    appt_date = ""
    appt_time = ""

    if appt.start_time:
        appt_date = appt.start_time.strftime("%Y-%m-%d")
        appt_time = appt.start_time.strftime("%H:%M")

    return {
        "id": appt.id,
        "patient_name": patient_name,
        "patient_phone": patient_phone,
        "date": appt_date,
        "time": appt_time,
        "status": appt.status or "",
    }


@router.post("", response_model=AppointmentOut)
def create(
    appt: AppointmentCreate,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

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
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    try:
        appointments = (
            db.query(models.Appointment)
            .filter(models.Appointment.clinic_id == clinic.id)
            .order_by(desc(models.Appointment.start_time))
            .all()
        )

        return [serialize_appointment(appt) for appt in appointments]

    except Exception as e:
        print("ERROR /appointments:", str(e))
        raise HTTPException(status_code=500, detail=f"Error interno en appointments: {str(e)}")


@router.patch("/{appointment_id}")
def update_appointment(
    appointment_id: int,
    payload: AppointmentUpdate = Body(...),
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)
    appointment = get_clinic_appointment(db, clinic.id, appointment_id)

    if payload.start_time is not None:
        appointment.start_time = payload.start_time
        if hasattr(appointment, "end_time") and appointment.end_time:
            duration = appointment.end_time - appointment.start_time
            if duration.total_seconds() <= 0:
                appointment.end_time = payload.start_time
        elif hasattr(appointment, "end_time"):
            appointment.end_time = payload.start_time

    if getattr(appointment, "patient", None):
        if payload.patient_name is not None:
            appointment.patient.full_name = payload.patient_name.strip()
        if payload.patient_phone is not None:
            appointment.patient.phone = payload.patient_phone.strip()

    db.commit()
    db.refresh(appointment)
    return serialize_appointment(appointment)


@router.patch("/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)
    appointment = get_clinic_appointment(db, clinic.id, appointment_id)

    appointment.status = "canceled"
    db.commit()
    db.refresh(appointment)
    return serialize_appointment(appointment)


@router.patch("/{appointment_id}/complete")
def complete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)
    appointment = get_clinic_appointment(db, clinic.id, appointment_id)

    appointment.status = "completed"
    db.commit()
    db.refresh(appointment)
    return serialize_appointment(appointment)
