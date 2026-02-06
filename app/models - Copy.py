from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base

from sqlalchemy import Text
import json


class Clinic(Base):
    __tablename__ = "clinics"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)  # ej: "clinica1"
    active = Column(Integer, nullable=False, default=1)  # 1 activo / 0 inactivo
    created_at = Column(DateTime, default=datetime.utcnow)


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(200), nullable=False)
    phone = Column(String(50), nullable=False, index=True)
    id_doc = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="patient")

class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)

class AppointmentType(Base):
    __tablename__ = "appointment_types"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), nullable=False)   # EVAL, PREOP, etc.
    duration_minutes = Column(Integer, nullable=False)

class AvailabilityRule(Base):
    __tablename__ = "availability_rules"
    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Mon ... 6=Sun
    start_hhmm = Column(String(5), nullable=False) # "09:00"
    end_hhmm = Column(String(5), nullable=False)   # "17:00"
    slot_minutes = Column(Integer, nullable=False, default=30)

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    type_id = Column(Integer, ForeignKey("appointment_types.id"), nullable=False)

    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    status = Column(String(30), nullable=False, default="scheduled")  # scheduled/confirmed/cancelled

    patient = relationship("Patient", back_populates="appointments")


class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String(50), nullable=False, default="ASK_NAME")  # estado del flujo
    data_json = Column(Text, nullable=False, default="{}")          # datos guardados (json string)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
