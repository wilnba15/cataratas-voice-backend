from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime
from app.db import get_db
from app.schemas import AppointmentCreate, AppointmentOut
from app.crud import get_or_create_patient, create_appointment
from app import models

router = APIRouter(prefix="/appointments", tags=["appointments"])


# =========================
# CREATE
# =========================
@router.post("", response_model=AppointmentOut)
def create(appt: AppointmentCreate, db: Session = Depends(get_db)):
    patient = get_or_create_patient(db, appt.full_name, appt.phone)
    created = create_appointment(
        db,
        patient_id=patient.id,
        provider_id=appt.provider_id,
        type_id=appt.type_id,
        start_time=appt.start_time
    )
    return created


# =========================
# LIST ALL APPOINTMENTS
# =========================
@router.get("")
def list_appointments(db: Session = Depends(get_db)):
    appointments = (
        db.query(models.Appointment)
        .order_by(desc(models.Appointment.start_time))
        .all()
    )

    results = []

    for appt in appointments:
        results.append({
            "id": appt.id,
            "patient_name": appt.patient.full_name if appt.patient else "",
            "phone": appt.patient.phone if appt.patient else "",
            "date": appt.start_time.strftime("%Y-%m-%d"),
            "time": appt.start_time.strftime("%H:%M"),
            "status": appt.status
        })

    return results
