from datetime import datetime, timedelta, time
from sqlalchemy.orm import Session
from app.models import AvailabilityRule, Appointment, AppointmentType

def _parse_hhmm(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))

def get_next_slots(db: Session, provider_id: int, type_id: int, from_dt: datetime, days_ahead: int = 14, limit: int = 3):
    appt_type = db.query(AppointmentType).filter(AppointmentType.id == type_id).first()
    if not appt_type:
        raise ValueError("AppointmentType not found")

    duration = timedelta(minutes=appt_type.duration_minutes)
    results = []

    for d in range(days_ahead + 1):
        day = (from_dt.date() + timedelta(days=d))
        dow = day.weekday()

        rules = db.query(AvailabilityRule).filter(
            AvailabilityRule.provider_id == provider_id,
            AvailabilityRule.day_of_week == dow
        ).all()

        if not rules:
            continue

        day_start = datetime.combine(day, time(0, 0))
        day_end = datetime.combine(day, time(23, 59))
        busy = db.query(Appointment).filter(
            Appointment.provider_id == provider_id,
            Appointment.start_time >= day_start,
            Appointment.start_time <= day_end,
            Appointment.status != "cancelled"
        ).all()

        busy_ranges = [(b.start_time, b.end_time) for b in busy]

        for rule in rules:
            start_t = _parse_hhmm(rule.start_hhmm)
            end_t = _parse_hhmm(rule.end_hhmm)
            slot = datetime.combine(day, start_t)

            end_limit = datetime.combine(day, end_t)
            step = timedelta(minutes=rule.slot_minutes)

            while slot + duration <= end_limit:
                if slot < from_dt:
                    slot += step
                    continue

                slot_end = slot + duration
                overlaps = any(not (slot_end <= b0 or slot >= b1) for (b0, b1) in busy_ranges)

                if not overlaps:
                    results.append((slot, slot_end))
                    if len(results) >= limit:
                        return results

                slot += step

    return results
