# app/tenancy.py
import os
from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.models import Clinic

DEFAULT_CLINIC_SLUG = os.getenv("DEFAULT_CLINIC_SLUG", "demo")
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "")  # ej: "tudominio.com" (opcional)

def _slug_from_host(host: str) -> str | None:
    """
    Extrae slug del host:
    - 'demo.tudominio.com' -> 'demo'
    - 'api.tudominio.com' -> None (no es clínica)
    - 'localhost' -> None
    """
    if not host:
        return None

    host = host.split(":")[0].lower()  # quita puerto
    if host in ("localhost", "127.0.0.1"):
        return None

    parts = host.split(".")
    if len(parts) < 3:
        return None

    sub = parts[0]
    if sub in ("www", "api"):
        return None

    # Si defines BASE_DOMAIN, validamos que termine en ese dominio
    if BASE_DOMAIN and not host.endswith(BASE_DOMAIN.lower()):
        return None

    return sub


def get_clinic_slug(
    request: Request,
    x_clinic_slug: str | None,
    x_forwarded_host: str | None,
) -> str:
    # 1) Header explícito (preferido para SaaS con backend en api.)
    if x_clinic_slug:
        return x_clinic_slug.strip().lower()

    # 2) Subdominio a partir del host real (proxy)
    host = (x_forwarded_host or request.headers.get("host") or "").strip()
    slug = _slug_from_host(host)
    if slug:
        return slug

    # 3) Fallback
    return DEFAULT_CLINIC_SLUG


def require_clinic(db: Session, slug: str) -> Clinic:
    clinic = db.query(Clinic).filter(Clinic.slug == slug, Clinic.active == 1).first()
    if not clinic:
        raise HTTPException(status_code=404, detail=f"Clinic '{slug}' not found or inactive")
    return clinic
