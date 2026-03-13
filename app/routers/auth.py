from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import os
import jwt

from app.db import get_db
from app.models import User, Clinic
from app.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 12


def create_access_token(user: User, clinic: Clinic) -> str:
    payload = {
        "sub": str(user.id),
        "user_id": user.id,
        "clinic_id": user.clinic_id,
        "clinic_slug": clinic.slug,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@router.post("/login", response_model=LoginResponse)
def login(
    data: LoginRequest,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None),
):
    if not x_clinic_slug:
        raise HTTPException(status_code=400, detail="Falta header X-Clinic-Slug")

    clinic = (
        db.query(Clinic)
        .filter(Clinic.slug == x_clinic_slug.strip().lower(), Clinic.active == True)
        .first()
    )
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica no encontrada o inactiva")

    user = (
        db.query(User)
        .filter(
            User.email == data.email.strip().lower(),
            User.clinic_id == clinic.id,
            User.active == True,
        )
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    # Por ahora comparación simple.
    # Luego la cambiamos a password hash (bcrypt/passlib).
    if user.password_hash != data.password:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = create_access_token(user, clinic)

    return LoginResponse(
        access_token=token,
        clinic_slug=clinic.slug,
        role=user.role,
    )