from app.db import SessionLocal
from app.models import Provider, AppointmentType, AvailabilityRule

def seed_data():
    db = SessionLocal()

    if not db.query(Provider).filter(Provider.id == 1).first():
        db.add(Provider(id=1, name="Dr. Especialista Cataratas"))

    if not db.query(AppointmentType).filter(AppointmentType.id == 1).first():
        db.add(AppointmentType(id=1, code="EVAL", duration_minutes=30))

    if not db.query(AvailabilityRule).filter(AvailabilityRule.provider_id == 1).first():
        for dow in range(5):
            db.add(AvailabilityRule(
                provider_id=1,
                day_of_week=dow,
                start_hhmm="09:00",
                end_hhmm="17:00",
                slot_minutes=30
            ))

    db.commit()
    db.close()
    print("✅ Seed listo: doctor + tipo cita + horarios")

