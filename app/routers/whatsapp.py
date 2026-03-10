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
sessions = {}

DEFAULT_CLINIC_SLUG = "demo"


def normalize_text(text: str) -> str:
    return (text or "").strip().lower()


def reset_session(user_id: str):
    sessions[user_id] = {
        "mode": "MENU",
        "voice_session_id": None,
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

    user_id = From or "unknown"
    incoming = normalize_text(Body)

    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(user_id)

    greetings = [
        "hola", "hello", "buenas", "buenos dias", "buenas tardes",
        "buenas noches", "menu", "menú", "inicio"
    ]

    if incoming in greetings:
        reset_session(user_id)
        msg.body(
            "Hola 👋\n"
            "Soy el asistente virtual de la clínica.\n\n"
            "¿Qué deseas hacer?\n"
            "1️⃣ Agendar cita\n"
            "2️⃣ Salir"
        )
        return Response(content=str(resp), media_type="application/xml")

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
                clinic = require_clinic(db, DEFAULT_CLINIC_SLUG)
                voice_sess = crud.create_voice_session(db, clinic_id=clinic.id)
                session["mode"] = "BOOKING"
                session["voice_session_id"] = voice_sess.id

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
            clinic = require_clinic(db, DEFAULT_CLINIC_SLUG)
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
