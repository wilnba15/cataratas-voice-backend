from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime
from app.db import get_db
from app.schemas import AppointmentCreate, AppointmentOut
from app.crud import get_or_create_patient, create_appointment

router = APIRouter(prefix="/appointments", tags=["appointments"])

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
