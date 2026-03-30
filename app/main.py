from fastapi import FastAPI
print(">>> MAIN REAL EJECUTADO")
from app.db import Base, engine
import app.models
from app.routers.voice import router as voice_router
from app.routers.appointments import router as appointments_router
from app.routers.whatsapp import router as whatsapp_router
from fastapi.middleware.cors import CORSMiddleware
from app.routers.auth import router as auth_router
from app.routers.medical_records import router as medical_records_router
from app.routers.medical_evolutions import router as medical_evolutions_router

from app.seed import seed_data  # <-- NUEVO

app = FastAPI(title="Cataratas Voice MVP - SQLite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

# seed_data()  # <-- NUEVO

app.include_router(voice_router)
app.include_router(appointments_router)
app.include_router(whatsapp_router)
app.include_router(auth_router)
app.include_router(medical_records_router)
app.include_router(medical_evolutions_router)

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok"}

from app.db import SessionLocal
from app.models import AppointmentType, Provider, AvailabilityRule

@app.get("/debug/seed")
def debug_seed():
    db = SessionLocal()
    try:
        return {
            "providers": db.query(Provider).count(),
            "appointment_types": db.query(AppointmentType).count(),
            "availability_rules": db.query(AvailabilityRule).count(),
        }
    finally:
        db.close()

from app.twilio_voice import router as twilio_router
app.include_router(twilio_router)