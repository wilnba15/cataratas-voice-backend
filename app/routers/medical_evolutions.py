from datetime import datetime
import os

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.tenancy import require_clinic

router = APIRouter(prefix="/medical-evolutions", tags=["medical_evolutions"])

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-change-me")
JWT_ALGORITHM = "HS256"


class MedicalEvolutionCreate(BaseModel):
    patient_id: int
    evolution_datetime: datetime | None = None
    professional_name: str
    professional_role: str | None = None
    attention_type: str | None = None

    subjective: str | None = None
    objective: str | None = None
    assessment: str | None = None
    plan: str | None = None

    blood_pressure: str | None = None
    heart_rate: str | None = None
    respiratory_rate: str | None = None
    temperature: str | None = None
    oxygen_saturation: str | None = None
    weight: str | None = None
    glucose: str | None = None
    pain_scale: str | None = None

    diagnosis: str | None = None
    indications: str | None = None
    clinical_alerts: str | None = None
    next_review_date: datetime | None = None

    status: str | None = "draft"


class MedicalEvolutionUpdate(BaseModel):
    evolution_datetime: datetime | None = None
    professional_name: str | None = None
    professional_role: str | None = None
    attention_type: str | None = None

    subjective: str | None = None
    objective: str | None = None
    assessment: str | None = None
    plan: str | None = None

    blood_pressure: str | None = None
    heart_rate: str | None = None
    respiratory_rate: str | None = None
    temperature: str | None = None
    oxygen_saturation: str | None = None
    weight: str | None = None
    glucose: str | None = None
    pain_scale: str | None = None

    diagnosis: str | None = None
    indications: str | None = None
    clinical_alerts: str | None = None
    next_review_date: datetime | None = None

    status: str | None = None


class MedicalEvolutionOut(BaseModel):
    id: int
    clinic_id: int
    patient_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    evolution_datetime: datetime | None = None
    professional_name: str
    professional_role: str | None = None
    attention_type: str | None = None

    subjective: str | None = None
    objective: str | None = None
    assessment: str | None = None
    plan: str | None = None

    blood_pressure: str | None = None
    heart_rate: str | None = None
    respiratory_rate: str | None = None
    temperature: str | None = None
    oxygen_saturation: str | None = None
    weight: str | None = None
    glucose: str | None = None
    pain_scale: str | None = None

    diagnosis: str | None = None
    indications: str | None = None
    clinical_alerts: str | None = None
    next_review_date: datetime | None = None

    status: str | None = None


class MedicalEvolutionListItem(BaseModel):
    id: int
    clinic_id: int
    patient_id: int
    patient_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    evolution_datetime: datetime | None = None
    professional_name: str | None = None
    professional_role: str | None = None
    diagnosis: str | None = None
    status: str | None = None


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


def serialize_medical_evolution(record: models.MedicalEvolution):
    return {
        "id": record.id,
        "clinic_id": record.clinic_id,
        "patient_id": record.patient_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "evolution_datetime": record.evolution_datetime,
        "professional_name": record.professional_name,
        "professional_role": record.professional_role,
        "attention_type": record.attention_type,
        "subjective": record.subjective,
        "objective": record.objective,
        "assessment": record.assessment,
        "plan": record.plan,
        "blood_pressure": record.blood_pressure,
        "heart_rate": record.heart_rate,
        "respiratory_rate": record.respiratory_rate,
        "temperature": record.temperature,
        "oxygen_saturation": record.oxygen_saturation,
        "weight": record.weight,
        "glucose": record.glucose,
        "pain_scale": record.pain_scale,
        "diagnosis": record.diagnosis,
        "indications": record.indications,
        "clinical_alerts": record.clinical_alerts,
        "next_review_date": record.next_review_date,
        "status": record.status,
    }


def serialize_medical_evolution_list_item(record: models.MedicalEvolution):
    patient_name = ""
    if getattr(record, "patient", None):
        patient_name = record.patient.full_name or ""

    return {
        "id": record.id,
        "clinic_id": record.clinic_id,
        "patient_id": record.patient_id,
        "patient_name": patient_name,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "evolution_datetime": record.evolution_datetime,
        "professional_name": record.professional_name,
        "professional_role": record.professional_role,
        "diagnosis": record.diagnosis,
        "status": record.status,
    }


@router.post("", response_model=MedicalEvolutionOut)
def create_medical_evolution(
    payload: MedicalEvolutionCreate,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    patient = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == payload.patient_id,
            models.Patient.clinic_id == clinic.id,
        )
        .first()
    )

    if not patient:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")

    record = models.MedicalEvolution(
        clinic_id=clinic.id,
        patient_id=payload.patient_id,
        evolution_datetime=payload.evolution_datetime or datetime.utcnow(),
        professional_name=payload.professional_name,
        professional_role=payload.professional_role,
        attention_type=payload.attention_type,
        subjective=payload.subjective,
        objective=payload.objective,
        assessment=payload.assessment,
        plan=payload.plan,
        blood_pressure=payload.blood_pressure,
        heart_rate=payload.heart_rate,
        respiratory_rate=payload.respiratory_rate,
        temperature=payload.temperature,
        oxygen_saturation=payload.oxygen_saturation,
        weight=payload.weight,
        glucose=payload.glucose,
        pain_scale=payload.pain_scale,
        diagnosis=payload.diagnosis,
        indications=payload.indications,
        clinical_alerts=payload.clinical_alerts,
        next_review_date=payload.next_review_date,
        status=payload.status or "draft",
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_medical_evolution(record)


@router.get("/patient/{patient_id}", response_model=list[MedicalEvolutionListItem])
def list_medical_evolutions_by_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    patient = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == patient_id,
            models.Patient.clinic_id == clinic.id,
        )
        .first()
    )

    if not patient:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")

    records = (
        db.query(models.MedicalEvolution)
        .filter(
            models.MedicalEvolution.patient_id == patient_id,
            models.MedicalEvolution.clinic_id == clinic.id,
        )
        .order_by(models.MedicalEvolution.evolution_datetime.desc(), models.MedicalEvolution.id.desc())
        .all()
    )

    return [serialize_medical_evolution_list_item(record) for record in records]


@router.get("/{id}", response_model=MedicalEvolutionOut)
def get_medical_evolution(
    id: int,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    record = (
        db.query(models.MedicalEvolution)
        .filter(
            models.MedicalEvolution.id == id,
            models.MedicalEvolution.clinic_id == clinic.id,
        )
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Evolución médica no encontrada")

    return serialize_medical_evolution(record)


@router.put("/{id}", response_model=MedicalEvolutionOut)
def update_medical_evolution(
    id: int,
    payload: MedicalEvolutionUpdate,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    record = (
        db.query(models.MedicalEvolution)
        .filter(
            models.MedicalEvolution.id == id,
            models.MedicalEvolution.clinic_id == clinic.id,
        )
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Evolución médica no encontrada")

    data = payload.dict(exclude_unset=True)

    for field, value in data.items():
        setattr(record, field, value)

    db.commit()
    db.refresh(record)

    return serialize_medical_evolution(record)