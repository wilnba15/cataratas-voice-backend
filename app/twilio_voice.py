from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from sqlalchemy import asc

from app.db import SessionLocal
from app.tenancy import require_clinic
from app import models, crud
from app.config import settings
from app.routers.voice import handle_message

import os
import re


def normalize_speech(text: str) -> str:
    t = (text or "").strip()
    # Caso: 20260212 -> 2026-02-12
    if re.fullmatch(r"\d{8}", t):
        y, m, d = t[:4], t[4:6], t[6:8]
        return f"{y}-{m}-{d}"
    return t


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE
)


def say_lines(vr: VoiceResponse, text: str, *, voice: str, language: str, pause_seconds: float = 0.9) -> None:
    """Dice el texto por líneas para que haya pausas naturales entre opciones."""
    t = clean_tts(text or "")
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        return
    for i, ln in enumerate(lines):
        vr.say(ln, voice=voice, language=language)
        if i != len(lines) - 1:
            vr.pause(length=pause_seconds)


def clean_tts(text: str) -> str:
    if not text:
        return ""
    t = text
    t = t.replace("✅", "").replace("❌", "").replace("👉", "").replace("📅", "")
    # Mejora pronunciación de fechas: 2026-02-24 -> 24 de febrero de 2026
    t = ISO_DATE_RE.sub(_iso_to_es, t)
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t



ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

MONTHS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

def _iso_to_es(m: re.Match) -> str:
    y = int(m.group(1)); mo = int(m.group(2)); d = int(m.group(3))
    month = MONTHS_ES.get(mo, "")
    if not month:
        return m.group(0)
    # comas ayudan a pausas en TTS
    return f"{d} de {month} de {y}"

router = APIRouter()


def _say(node, text: str):
    node.say(text, language="es-ES", voice="Polly.Conchita")


def _gather(clinic_slug: str, sid: int):
    return Gather(
        input="speech dtmf",
        language="es-ES",
        action=f"/twilio/process?clinic={clinic_slug}&sid={sid}",
        method="POST",
        speech_timeout="auto",
        timeout=8,
    )


def _say_slots_with_pause(gather: Gather, prompt: str):
    p = clean_tts(prompt)
    _say(gather, "Estos son los horarios disponibles.")

    matches = re.findall(r"\b([1-5])\)\s*([0-2]?\d:\d{2})\b", p)
    if matches:
        for n, hhmm in matches:
            _say(gather, f"Opción {n}: {hhmm}.")
            gather.pause(length=0.9)
    else:
        _say(gather, p)

    _say(gather, "Por favor, marca el número de tu opción en el teclado.")


# =========================
# NUEVO: /twilio/call-me
# =========================
class CallMeRequest(BaseModel):
    name: str
    phone: str
    clinic_slug: str = "demo"


def _normalize_phone_e164(phone: str) -> str:
    """
    Normaliza a formato E.164 básico:
    - Debe comenzar con + y números
    - Quita espacios/guiones/paréntesis
    """
    p = (phone or "").strip()
    p = re.sub(r"[ \-\(\)]", "", p)
    if not p.startswith("+"):
        # si viene sin +, no adivinamos país: obligamos a +593...
        raise ValueError("El teléfono debe incluir código de país, por ejemplo: +593...")
    if not re.fullmatch(r"\+\d{8,15}", p):
        raise ValueError("Formato de teléfono inválido. Usa +593XXXXXXXXX.")
    return p


def _get_public_base_url(request: Request) -> str:
    """
    Mejor práctica: setear PUBLIC_BASE_URL en Render.
    Fallback: inferir desde request.base_url
    """
    env_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    # fallback
    return str(request.base_url).rstrip("/")


@router.post("/twilio/call-me")
async def twilio_call_me(payload: CallMeRequest, request: Request):
    """
    Dispara llamada OUTBOUND (Opción B: "Te llamamos") y conecta con /twilio/voice
    """
    # Validaciones mínimas
    try:
        to_phone = _normalize_phone_e164(payload.phone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    clinic_slug = (payload.clinic_slug or "demo").strip() or "demo"

    # Verifica que la clínica exista
    db = SessionLocal()
    try:
        clinic = require_clinic(db, clinic_slug)
    finally:
        db.close()

    # Credenciales Twilio
    account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None) or os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", None) or os.getenv("TWILIO_AUTH_TOKEN")
    from_number = getattr(settings, "TWILIO_PHONE_NUMBER", None) or os.getenv("TWILIO_PHONE_NUMBER")

    if not account_sid or not auth_token or not from_number:
        raise HTTPException(
            status_code=500,
            detail="Faltan variables de Twilio (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER)."
        )

    base_url = _get_public_base_url(request)
    twiml_url = f"{base_url}/twilio/voice?clinic={clinic_slug}"

    try:
        client = Client(account_sid, auth_token)
        call = client.calls.create(
            to=to_phone,
            from_=from_number,
            url=twiml_url,   # Twilio pedirá TwiML aquí
            method="POST",
        )
        return {"ok": True, "call_sid": call.sid, "clinic_slug": clinic_slug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error llamando con Twilio: {repr(e)}")


@router.post("/twilio/voice")
async def twilio_voice(
    request: Request,
    CallSid: str = Form(default=""),
):
    clinic_slug = request.query_params.get("clinic", "demo")

    vr = VoiceResponse()
    db = SessionLocal()
    try:
        clinic = require_clinic(db, clinic_slug)

        sess = crud.create_voice_session(db, clinic_id=clinic.id)

        try:
            sess.data_json = {**(sess.data_json or {}), "twilio_call_sid": CallSid}
            db.commit()
        except Exception:
            db.rollback()

        sid = sess.id

        # ✅ IMPORTANTE: decimos el saludo FUERA del Gather (más confiable en llamadas reales)
        _say(vr, "Hola, soy el asistente de la clínica.")
        vr.pause(length=1)
        _say(vr, "¿Cuál es tu nombre completo?")

        # Gather solo para escuchar (speech + teclado)
        gather = _gather(clinic_slug, sid)
        vr.append(gather)

        # Fallback si no detecta voz/teclas
        _say(vr, "No te escuché. Intentemos otra vez.")
        vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")
    finally:
        db.close()

    return Response(content=str(vr), media_type="text/xml")


@router.post("/twilio/process")
async def twilio_process(
    request: Request,
    SpeechResult: str = Form(default=""),
    Digits: str = Form(default=""),
):
    clinic_slug = request.query_params.get("clinic", "demo")
    sid_raw = request.query_params.get("sid", "")

    raw_input = Digits or SpeechResult
    text = normalize_speech(raw_input)

    vr = VoiceResponse()

    try:
        sid = int(sid_raw)
    except Exception:
        _say(vr, "Se perdió la sesión. Volvamos a empezar.")
        vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")
        return Response(content=str(vr), media_type="text/xml")

    if not text:
        gather = _gather(clinic_slug, sid)
        _say(gather, "No te escuché bien. Repite por favor.")
        vr.append(gather)

        _say(vr, "No te escuché. Intentemos otra vez.")
        vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")
        return Response(content=str(vr), media_type="text/xml")

    db = SessionLocal()
    try:
        clinic = require_clinic(db, clinic_slug)

        prov = (
            db.query(models.Provider)
            .filter(models.Provider.clinic_id == clinic.id)
            .order_by(asc(models.Provider.id))
            .first()
        )
        appt = (
            db.query(models.AppointmentType)
            .filter(models.AppointmentType.clinic_id == clinic.id)
            .order_by(asc(models.AppointmentType.id))
            .first()
        )
        provider_id = (prov.id if prov else None) or settings.DEFAULT_PROVIDER_ID
        type_id = (appt.id if appt else None) or settings.DEFAULT_APPT_TYPE_ID

        try:
            result = handle_message(
                db,
                clinic.id,
                sid,
                text,
                provider_id=provider_id,
                type_id=type_id,
            )
        except Exception as e:
            print("ERROR /twilio/process:", repr(e))
            result = {"prompt": "Hubo un problema técnico. Intentemos otra vez.", "done": False}
    finally:
        db.close()

    prompt = (result or {}).get("prompt") or "Perfecto. ¿Me repites por favor?"
    done = bool((result or {}).get("done", False))

    if done:
        say_lines(vr, prompt, voice="Polly.Conchita", language="es-ES")
        vr.hangup()
        return Response(content=str(vr), media_type="text/xml")

    gather = _gather(clinic_slug, sid)

    if "horarios disponibles" in (prompt or "").lower():
        _say_slots_with_pause(gather, prompt)
    else:
        # Para doctores/especialidad y otros listados: decir por líneas con pausas
        say_lines(gather, prompt, voice="Polly.Conchita", language="es-ES")

    vr.append(gather)

    _say(vr, "Si prefieres, marca el número en el teclado. Intentemos otra vez.")
    vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")

    return Response(content=str(vr), media_type="text/xml")
