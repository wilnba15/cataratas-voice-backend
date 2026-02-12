from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from sqlalchemy import asc

from app.db import SessionLocal
from app.tenancy import require_clinic
from app import models, crud
from app.config import settings
from app.routers.voice import handle_message

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

def clean_tts(text: str) -> str:
    if not text:
        return ""
    t = text
    t = t.replace("✅", "").replace("❌", "").replace("👉", "").replace("📅", "")
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


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

        # ✅ CREA sesión en BD (id INT)
        sess = crud.create_voice_session(db, clinic_id=clinic.id)

        # (opcional) guardar el CallSid en data_json para trazabilidad
        try:
            sess.data_json = {**(sess.data_json or {}), "twilio_call_sid": CallSid}
            db.commit()
        except Exception:
            db.rollback()

        sid = sess.id  # ✅ ESTE ES EL QUE VAMOS A USAR SIEMPRE

        gather = Gather(
        input="speech dtmf",
        language="es-ES",
        action=f"/twilio/process?clinic={clinic_slug}&sid={sid}",
        method="POST",
        speech_timeout="auto",
        timeout=8
    )

    clean_prompt = clean_tts(prompt)

    # ✅ Mejora 1: Horarios claros + pedir marcar en teclado (DTMF)
    if "horarios disponibles" in clean_prompt.lower():
        _say(gather, "Estos son los horarios disponibles.")

        # Extrae opciones tipo: "1) 09:00"
        opciones = re.findall(r"(\b[1-5]\)\s*\d{1,2}:\d{2}\b)", clean_prompt)

        # Si no encontró en el formato esperado, intenta con otro formato más permisivo
        if not opciones:
            opciones = re.findall(r"(\b[1-5]\)\s*[^\s]+)", clean_prompt)

        for op in opciones:
            # op ejemplo: "3) 10:00"
            parts = op.split(")")
            numero = parts[0].strip()
            valor = parts[1].strip() if len(parts) > 1 else ""
            # Decir la hora/valor sin correr
            _say(gather, f"Opción {numero}: {valor}.")

        _say(gather, "Por favor, marca el número de tu opción en el teclado.")
    else:
        _say(gather, clean_prompt)

    vr.append(gather)
    return Response(content=str(vr), media_type="application/xml")
# este es