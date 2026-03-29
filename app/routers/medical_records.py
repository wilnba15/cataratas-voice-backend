from datetime import datetime
import os

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.tenancy import require_clinic

router = APIRouter(prefix="/medical-records", tags=["medical_records"])

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-change-me")
JWT_ALGORITHM = "HS256"


class MedicalRecordCreate(BaseModel):
    patient_id: int
    motivo_consulta: str | None = None
    antecedentes: str | None = None
    diagnostico: str | None = None
    observaciones: str | None = None


class MedicalRecordOut(BaseModel):
    id: int
    clinic_id: int
    patient_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    motivo_consulta: str | None = None
    antecedentes: str | None = None
    diagnostico: str | None = None
    observaciones: str | None = None


class MedicalRecordListItem(BaseModel):
    id: int
    clinic_id: int
    patient_id: int
    patient_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    motivo_consulta: str | None = None
    diagnostico: str | None = None


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


def serialize_medical_record(record: models.MedicalRecord):
    return {
        "id": record.id,
        "clinic_id": record.clinic_id,
        "patient_id": record.patient_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "motivo_consulta": record.motivo_consulta,
        "antecedentes": record.antecedentes,
        "diagnostico": record.diagnostico,
        "observaciones": record.observaciones,
    }


def serialize_medical_record_list_item(record: models.MedicalRecord):
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
        "motivo_consulta": record.motivo_consulta,
        "diagnostico": record.diagnostico,
    }


@router.post("", response_model=MedicalRecordOut)
def create_medical_record(
    payload: MedicalRecordCreate,
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

    existing = (
        db.query(models.MedicalRecord)
        .filter(
            models.MedicalRecord.patient_id == payload.patient_id,
            models.MedicalRecord.clinic_id == clinic.id,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="El paciente ya tiene una historia clínica creada",
        )

    record = models.MedicalRecord(
        clinic_id=clinic.id,
        patient_id=payload.patient_id,
        motivo_consulta=payload.motivo_consulta,
        antecedentes=payload.antecedentes,
        diagnostico=payload.diagnostico,
        observaciones=payload.observaciones,
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    return record


@router.get("", response_model=list[MedicalRecordListItem])
def list_medical_records(
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    records = (
        db.query(models.MedicalRecord)
        .filter(models.MedicalRecord.clinic_id == clinic.id)
        .order_by(models.MedicalRecord.id.desc())
        .all()
    )

    return [serialize_medical_record_list_item(record) for record in records]


@router.get("/patient/{patient_id}", response_model=MedicalRecordOut)
def get_medical_record_by_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
    auth=Depends(get_current_auth),
):
    clinic = ensure_clinic_access(db, x_clinic_slug, auth)

    record = (
        db.query(models.MedicalRecord)
        .filter(
            models.MedicalRecord.patient_id == patient_id,
            models.MedicalRecord.clinic_id == clinic.id,
        )
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Historia clínica no encontrada")

    return record