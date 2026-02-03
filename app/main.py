from fastapi import FastAPI
from app.db import Base, engine
import app.models  # importante: carga los modelos y crea tablas
from app.routers.voice import router as voice_router
from app.routers.appointments import router as appointments_router

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Cataratas Voice MVP - SQLite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # luego lo restringimos a tu dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Crear tablas si no existen
Base.metadata.create_all(bind=engine)

# Rutas
app.include_router(voice_router)
app.include_router(appointments_router)

@app.get("/health")
def health():
    return {"status": "ok"}
