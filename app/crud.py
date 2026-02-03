from sqlalchemy.orm import Session
from datetime import timedelta
from app.models import Patient, Appointment, AppointmentType

def get_or_create_patient(db: Session, full_name: str, phone: str):
    p = db.query(Patient).filter(Patient.phone == phone).first()
    if p:
        p.full_name = full_name
        db.commit()
        db.refresh(p)
        return p

    p = Patient(full_name=full_name, phone=phone)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

def create_appointment(db: Session, patient_id: int, provider_id: int, type_id: int, start_time):
    appt_type = db.query(AppointmentType).filter(AppointmentType.id == type_id).first()
    if not appt_type:
        raise ValueError("AppointmentType not found")

    end_time = start_time + timedelta(minutes=appt_type.duration_minutes)

    appt = Appointment(
        patient_id=patient_id,
        provider_id=provider_id,
        type_id=type_id,
        start_time=start_time,
        end_time=end_time,
        status="scheduled",
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt

import json
from app.models import VoiceSession

def create_voice_session(db: Session) -> VoiceSession:
    sess = VoiceSession(state="ASK_NAME", data_json="{}")
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess

def get_voice_session(db: Session, session_id: int) -> VoiceSession | None:
    return db.query(VoiceSession).filter(VoiceSession.id == session_id).first()

def session_data(sess: VoiceSession) -> dict:
    try:
        return json.loads(sess.data_json or "{}")
    except Exception:
        return {}

def update_voice_session(db: Session, sess: VoiceSession, state: str, data: dict) -> VoiceSession:
    sess.state = state
    sess.data_json = json.dumps(data, ensure_ascii=False)
    db.commit()
    db.refresh(sess)
    return sess
