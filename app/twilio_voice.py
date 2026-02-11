from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from sqlalchemy import asc

from app.db import SessionLocal
from app.tenancy import require_clinic
from app import models
from app.config import settings
from app.routers.voice import handle_message

router = APIRouter()

def _say(vr_or_gather, text: str):
    vr_or_gather.say(text, language="es-ES")

@router.post("/twilio/voice")
async def twilio_voice(request: Request):
    clinic_slug = request.query_params.get("clinic", "demo")

    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        language="es-ES",
        action=f"/twilio/process?clinic={clinic_slug}",
        method="POST",
        speech_timeout="auto",
    )
    _say(gather, "Hola, soy el asistente de la clínica. ¿En qué puedo ayudarte?")
    vr.append(gather)

    _say(vr, "No te escuché. Intentemos otra vez.")
    vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")
    return Response(content=str(vr), media_type="application/xml")

@router.post("/twilio/process")
async def twilio_process(
    request: Request,
    SpeechResult: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    clinic_slug = request.query_params.get("clinic", "demo")
    text = (SpeechResult or "").strip()

    vr = VoiceResponse()

    if not text:
        gather = Gather(
            input="speech",
            language="es-ES",
            action=f"/twilio/process?clinic={clinic_slug}",
            method="POST",
            speech_timeout="auto",
        )
        _say(gather, "No te escuché bien. Repite por favor.")
        vr.append(gather)
        return Response(content=str(vr), media_type="application/xml")

    session_id = CallSid or "no-callsid"

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

        result = handle_message(
            db,
            clinic.id,
            session_id,
            text,
            provider_id=provider_id,
            type_id=type_id,
        )
    finally:
        db.close()

    prompt = (result or {}).get("prompt") or "Perfecto. ¿Me repites por favor?"
    done = bool((result or {}).get("done", False))

    if done:
        _say(vr, prompt)
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    gather = Gather(
        input="speech",
        language="es-ES",
        action=f"/twilio/process?clinic={clinic_slug}",
        method="POST",
        speech_timeout="auto",
    )
    _say(gather, prompt)
    vr.append(gather)

    return Response(content=str(vr), media_type="application/xml")
