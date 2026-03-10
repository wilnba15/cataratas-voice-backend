from app.db import SessionLocal
from app.models import Clinic, Provider, AppointmentType, AvailabilityRule


def seed_data():
    db = SessionLocal()

    try:
        # 1) Crear clínica demo si no existe
        clinic = db.query(Clinic).filter(Clinic.slug == "demo").first()
        if not clinic:
            clinic = Clinic(
                id=1,
                name="Clínica Demo",
                slug="demo"
            )
            db.add(clinic)
            db.commit()
            db.refresh(clinic)

        # 2) Crear provider si no existe
        provider = db.query(Provider).filter(
            Provider.id == 1,
            Provider.clinic_id == clinic.id
        ).first()

        if not provider:
            provider = Provider(
                id=1,
                clinic_id=clinic.id,
                name="Dr. Especialista Cataratas"
            )
            db.add(provider)

        # 3) Crear tipo de cita si no existe
        appt_type = db.query(AppointmentType).filter(
            AppointmentType.id == 1,
            AppointmentType.clinic_id == clinic.id
        ).first()

        if not appt_type:
            appt_type = AppointmentType(
                id=1,
                clinic_id=clinic.id,
                code="EVAL",
                duration_minutes=30
            )
            db.add(appt_type)

        db.commit()

        # 4) Crear reglas de disponibilidad si no existen
        existing_rule = db.query(AvailabilityRule).filter(
            AvailabilityRule.provider_id == provider.id,
            AvailabilityRule.clinic_id == clinic.id
        ).first()

        if not existing_rule:
            for dow in range(5):
                db.add(
                    AvailabilityRule(
                        clinic_id=clinic.id,
                        provider_id=provider.id,
                        day_of_week=dow,
                        start_hhmm="09:00",
                        end_hhmm="17:00",
                        slot_minutes=30
                    )
                )

        db.commit()
        print("✅ Seed listo: clínica demo + doctor + tipo cita + horarios")

    finally:
        db.close()