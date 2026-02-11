from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from sqlalchemy import asc

from app.db import SessionLocal
from app.tenancy import require_clinic
from app import models, crud
from app.config import settings
from app.routers.voice import handle_message

router = APIRouter()

def _say(node, text: str):
    node.say(text, language="es-ES")

def _gather(clinic_slug: str, sid: int):
    return Gather(
        input="speech",
        language="es-ES",
        action=f"/twilio/process?clinic={clinic_slug}&sid={sid}",
        method="POST",
        speech_timeout="auto",
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
            input="speech",
            language="es-ES",
            action=f"/twilio/process?clinic={clinic_slug}&sid={sid}",
            method="POST",
            speech_timeout="auto",
        )
        _say(gather, "Hola, soy el asistente de la clínica. ¿Cuál es tu nombre completo?")
        vr.append(gather)

        _say(vr, "No te escuché. Intentemos otra vez.")
        vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")

    finally:
        db.close()

    return Response(content=str(vr), media_type="application/xml")



@router.post("/twilio/process")
async def twilio_process(
    request: Request,
    SpeechResult: str = Form(default=""),
):
    clinic_slug = request.query_params.get("clinic", "demo")
    sid_raw = request.query_params.get("sid", "")
    text = (SpeechResult or "").strip()

    vr = VoiceResponse()

    # ✅ sid debe ser int
    try:
        sid = int(sid_raw)
    except Exception:
        _say(vr, "Se perdió la sesión. Volvamos a empezar.")
        vr.redirect(f"/twilio/voice?clinic={clinic_slug}", method="POST")
        return Response(content=str(vr), media_type="application/xml")

    if not text:
        gather = Gather(
            input="speech",
            language="es-ES",
            action=f"/twilio/process?clinic={clinic_slug}&sid={sid}",
            method="POST",
            speech_timeout="auto",
        )
        _say(gather, "No te escuché bien. Repite por favor.")
        vr.append(gather)
        return Response(content=str(vr), media_type="application/xml")

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

        # ✅ AQUÍ ya no hay strings: sid es INT
        result = handle_message(
            db,
            clinic.id,
            sid,
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
        action=f"/twilio/process?clinic={clinic_slug}&sid={sid}",
        method="POST",
        speech_timeout="auto",
    )
    _say(gather, prompt)
    vr.append(gather)
    return Response(content=str(vr), media_type="application/xml")

# este es