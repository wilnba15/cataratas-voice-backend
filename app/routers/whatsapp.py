from typing import Optional

from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse, Response
from twilio.twiml.messaging_response import MessagingResponse

from app.db import SessionLocal
from app.tenancy import require_clinic
from app import crud
from app.routers.voice import handle_message

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

# Sesiones locales de WhatsApp:
# guardan qué sesión conversacional de voice_sessions está asociada a cada número.
# IMPORTANTE: esto sirve para staging/demo. En producción conviene persistirlo en BD/Redis.
sessions = {}

# Mapeo por número WhatsApp destino -> slug de clínica
# Ajusta estos números reales según tu cuenta / sandbox / número asignado.
WHATSAPP_NUMBER_TO_CLINIC = {
    "whatsapp:+14155238886": "clinica-valle",           # Twilio Sandbox
}


def normalize_text(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def normalize_number(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def get_clinic_slug_by_to_number(to_number: Optional[str]) -> Optional[str]:
    return WHATSAPP_NUMBER_TO_CLINIC.get(normalize_number(to_number))


def reset_session(user_id: str):
    sessions[user_id] = {
        "mode": "MENU",
        "voice_session_id": None,
        "clinic_slug": None,
        "to_number": None,
    }


def get_session(user_id: str):
    if user_id not in sessions:
        reset_session(user_id)
    return sessions[user_id]


@router.get("/health")
def whatsapp_health():
    return {"ok": True, "channel": "whatsapp"}


@router.get("/test")
def whatsapp_test():
    return PlainTextResponse("WhatsApp router funcionando")


@router.post("/inbound")
async def whatsapp_inbound(
    From: str = Form(None),
    Body: str = Form(None),
    To: str = Form(None),
):
    print("=== WhatsApp inbound ===")
    print("From:", From)
    print("To:", To)
    print("Body:", Body)

    user_id = (From or "unknown").strip()
    incoming = normalize_text(Body)
    to_number = normalize_number(To)

    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(user_id)

    clinic_slug = get_clinic_slug_by_to_number(to_number)
    if not clinic_slug:
        msg.body(
            "Lo siento. No pude identificar la clínica asociada a este número de WhatsApp.\n"
            "Por favor contacta al administrador."
        )
        return Response(content=str(resp), media_type="application/xml")

    # Si entra por otro número, reiniciamos la sesión para evitar mezclar clínicas
    if session.get("to_number") and session["to_number"] != to_number:
        reset_session(user_id)
        session = get_session(user_id)

    session["clinic_slug"] = clinic_slug
    session["to_number"] = to_number

    greetings = [
        "hola", "hello", "buenas", "buenos dias", "buenas tardes",
        "buenas noches", "menu", "menú", "inicio"
    ]

    if incoming in greetings:
        reset_session(user_id)
        session = get_session(user_id)
        session["clinic_slug"] = clinic_slug
        session["to_number"] = to_number

        db = SessionLocal()
        try:
            clinic = require_clinic(db, clinic_slug)
            msg.body(
                f"Hola 👋\n"
                f"Soy el asistente virtual de {clinic.name}.\n\n"
                "¿Qué deseas hacer?\n"
                "1️⃣ Agendar cita\n"
                "2️⃣ Salir"
            )
            return Response(content=str(resp), media_type="application/xml")
        except Exception as e:
            print("ERROR cargando clínica WhatsApp:", repr(e))
            reset_session(user_id)
            msg.body(
                "Ocurrió un problema técnico identificando la clínica 😥\n"
                "Escribe *hola* para intentarlo nuevamente."
            )
            return Response(content=str(resp), media_type="application/xml")
        finally:
            db.close()

    if session["mode"] == "MENU":
        if incoming in ["2", "salir", "no"]:
            reset_session(user_id)
            msg.body(
                "Entendido 👍\n"
                "Cuando desees volver a agendar, escribe *hola*."
            )
            return Response(content=str(resp), media_type="application/xml")

        if incoming in ["1", "si", "sí", "agendar", "cita"]:
            db = SessionLocal()
            try:
                clinic = require_clinic(db, clinic_slug)
                voice_sess = crud.create_voice_session(db, clinic_id=clinic.id)
                session["mode"] = "BOOKING"
                session["voice_session_id"] = voice_sess.id
                session["clinic_slug"] = clinic_slug
                session["to_number"] = to_number

                msg.body("Perfecto ✅\nPor favor escribe tu *nombre completo*.")
                return Response(content=str(resp), media_type="application/xml")
            except Exception as e:
                print("ERROR creando sesión WhatsApp:", repr(e))
                reset_session(user_id)
                msg.body(
                    "Hubo un problema técnico creando la sesión 😥\n"
                    "Escribe *hola* para intentarlo nuevamente."
                )
                return Response(content=str(resp), media_type="application/xml")
            finally:
                db.close()

        msg.body(
            "No entendí tu mensaje.\n\n"
            "Escribe:\n"
            "1 para agendar una cita\n"
            "2 para salir\n\n"
            "O escribe *hola* para comenzar."
        )
        return Response(content=str(resp), media_type="application/xml")

    if session["mode"] == "BOOKING":
        db = SessionLocal()
        try:
            clinic = require_clinic(db, clinic_slug)
            result = handle_message(
                db,
                clinic.id,
                session["voice_session_id"],
                Body or ""
            )
            prompt = (result or {}).get("prompt") or "No entendí tu mensaje."
            done = bool((result or {}).get("done", False))

            msg.body(prompt)

            if done:
                reset_session(user_id)

            return Response(content=str(resp), media_type="application/xml")

        except Exception as e:
            print("ERROR en flujo WhatsApp:", repr(e))
            reset_session(user_id)
            msg.body(
                "Ocurrió un problema técnico 😥\n"
                "Escribe *hola* para comenzar nuevamente."
            )
            return Response(content=str(resp), media_type="application/xml")
        finally:
            db.close()

    reset_session(user_id)
    msg.body(
        "Se reinició la conversación por seguridad.\n"
        "Escribe *hola* para comenzar de nuevo."
    )
    return Response(content=str(resp), media_type="application/xml")
