from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Body, Form, Header
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from openai import OpenAI
import os
import re
import unicodedata
import tempfile

from app.db import get_db
from app.config import settings
from app.services.availability import get_next_slots
from app import crud, schemas
from app.tenancy import get_clinic_slug, require_clinic

from sqlalchemy import asc
from app import models  # o donde importes Provider / AppointmentType


router = APIRouter(prefix="/voice", tags=["voice"])


# Endpoint de prueba de disponibilidad (SIN Twilio todavía)

@router.get("/test-slots")
def test_slots(
    request: Request,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
    clinic = require_clinic(db, slug)

    # 🔥 Para pruebas: fuerza 09:00 del día siguiente (evita que te dé 0 por estar fuera de horario)
    from_dt = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

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

    res = get_next_slots(
        db,
        clinic_id=clinic.id,
        provider_id=provider_id,
        type_id=type_id,
        from_dt=from_dt,
        limit=3
    )


    # ✅ Soporta ambas salidas: lista o {"value":[], "Count":0}
    slots = res["value"] if isinstance(res, dict) and "value" in res else res

    return [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in slots]



# ====== NUEVO: Flujo conversacional por texto (sin Twilio aún) ======

@router.post("/start", response_model=schemas.VoiceStartResponse)
def start_voice(
    request: Request,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
    clinic = require_clinic(db, slug)

    sess = crud.create_voice_session(db, clinic_id=clinic.id)
    return {"session_id": sess.id, "prompt": "Hola 👋 ¿Cuál es tu nombre completo?"}


WEEKDAYS_ES = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

def parse_date_es(text: str, now: datetime) -> str | None:
    t = (text or "").strip().lower()

    # ✅ Detecta formato 20260212
    if re.fullmatch(r"\d{8}", t):
        try:
            dt = datetime.strptime(t, "%Y%m%d").date()
            return dt.isoformat()
        except ValueError:
            return None


    if t == "hoy":
        return now.date().isoformat()
    if t in ("mañana", "manana"):
        return (now.date() + timedelta(days=1)).isoformat()

    if t in WEEKDAYS_ES:
        target = WEEKDAYS_ES[t]
        delta = (target - now.weekday()) % 7
        delta = 7 if delta == 0 else delta  # si hoy es lunes y dice "lunes", será el próximo lunes
        return (now.date() + timedelta(days=delta)).isoformat()

    # ✅ Detecta yyyy-mm-dd o yyyy mm dd o yyyy/ mm/ dd aunque venga con palabras (por voz)
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", t)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        d = int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # ISO yyyy-mm-dd (si vino perfecto)
    try:
        return datetime.strptime(t, "%Y-%m-%d").date().isoformat()
    except Exception:
        pass

    # dd/mm/yyyy
    try:
        return datetime.strptime(t, "%d/%m/%Y").date().isoformat()
    except Exception:
        pass

    return None


def looks_like_phone(s: str) -> bool:
    digits = re.sub(r"\D", "", s or "")
    return len(digits) >= 8 and (len(digits) >= int(0.7 * max(1, len(s))))

def normalize_es(text: str) -> str:
    """Normaliza texto en español: minúsculas, sin tildes, sin puntuación."""
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")  # quita tildes
    text = re.sub(r"[^a-z0-9\s]", "", text)  # quita puntuación
    text = re.sub(r"\s+", " ", text).strip()
    return text



def handle_message(db, clinic_id, session_id, text, provider_id: int, type_id: int):


    sess = crud.get_voice_session(db, session_id, clinic_id=clinic_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")


    text = (text or "").strip()
    if not text:
        return {
            "session_id": session_id,
            "prompt": "No te escuché bien 😅 ¿me repites?",
            "done": False
        }

    data = crud.session_data(sess)

    # ====== 1) NOMBRE (con validación) ======
    if sess.state == "ASK_NAME":
        # Evita que un número entre como “nombre”
        if len(text.split()) < 2 or looks_like_phone(text):
            return {
                "session_id": sess.id,
                "prompt": "Para registrarte correctamente necesito tus *nombres y apellidos* 😊",
                "done": False
            }

        data["full_name"] = text
        crud.update_voice_session(db, sess, "ASK_PHONE", data)
        return {
            "session_id": sess.id,
            "prompt": f"Gracias {data['full_name']} 😊 Ahora indícame tu número telefónico por favor.",
            "done": False
        }

    # ====== 2) TELÉFONO ======
    if sess.state == "ASK_PHONE":
        data["phone"] = text
        crud.update_voice_session(db, sess, "INFO_GENERAL", data)
        return {
            "session_id": sess.id,
            "prompt": (
                "Perfecto ✅\n"
                "Atendemos de lunes a viernes de 09:00 a 17:00.\n"
                "La consulta incluye evaluación completa con el especialista.\n\n"
                "Ahora sí, agendemos tu cita.\n"
                "¿Para qué fecha deseas la cita? (Ej: mañana, lunes, 2026-02-10)"
            ),
            "done": False
        }

    # ====== 3) FECHA (acepta natural) + slots ======
    if sess.state == "INFO_GENERAL":
        date_iso = parse_date_es(text, now=datetime.now())
        if not date_iso:
            return {
                "session_id": sess.id,
                "prompt": "No entendí la fecha 😅. Dime por ejemplo: 'mañana', 'lunes' o '2026-02-10'.",
                "done": False
            }

        data["date"] = date_iso

        # provider_id = settings.DEFAULT_PROVIDER_ID
        # type_id = settings.DEFAULT_APPT_TYPE_ID

        date_start = datetime.fromisoformat(data["date"] + "T00:00:00")
        date_end = date_start + timedelta(days=1)

        # Pedimos slots desde el día elegido (así NO depende del limit desde hoy)

        all_slots = get_next_slots(
            db,
            clinic_id=clinic_id,
            provider_id=provider_id,
            type_id=type_id,
            from_dt=date_start,
            days_ahead=1,
            limit=200
        )

        day_slots = [s for s in all_slots if date_start <= s[0] < date_end]

        if not day_slots:
            return {
                "session_id": sess.id,
                "prompt": "Ese día no hay disponibilidad 😬 ¿Qué otra fecha te sirve?",
                "done": False
            }

        options = day_slots[:5]
        data["slot_options"] = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in options]

        crud.update_voice_session(db, sess, "ASK_SLOT", data)

        opciones_txt = "\n".join([f"{i+1}) {opt['start'][11:16]}" for i, opt in enumerate(data["slot_options"])])
        return {
            "session_id": sess.id,
            "prompt": f"Estos son los horarios disponibles para {data['date']}:\n{opciones_txt}\nElige el número (1-5).",
            "done": False
        }

    # ====== 4) ELEGIR SLOT ======
    WORDS_TO_NUM = {
        "uno": 1, "una": 1, "primero": 1, "primera": 1,
        "dos": 2, "segundo": 2, "segunda": 2,
        "tres": 3, "tercero": 3, "tercera": 3,
        "cuatro": 4, "cuarto": 4, "cuarta": 4,
        "cinco": 5, "quinto": 5, "quinta": 5,
    }

    if sess.state == "ASK_SLOT":
        raw = (text or "").strip().lower()

        # 1) extrae dígito aunque venga como "opción 3", "3.", etc.
        m = re.search(r"\b([1-5])\b", raw)
        if m:
            idx = int(m.group(1)) - 1
        else:
            # 2) si viene en letras: "tres"
            n = WORDS_TO_NUM.get(raw)
            if n is None:
                return {
                    "session_id": sess.id,
                    "prompt": "No entendí 😅 Elige un número del 1 al 5 (por ejemplo: '3').",
                    "done": False
                }
            idx = n - 1

        try:
            chosen = data.get("slot_options", [])[idx]
        except Exception:
            return {
                "session_id": sess.id,
                "prompt": "Elige un número válido del 1 al 5 porfa 😊",
                "done": False
            }

        data["chosen_slot"] = chosen
        crud.update_voice_session(db, sess, "ASK_DOCTOR", data)

        return {
            "session_id": sess.id,
            "prompt": "¿Deseas atenderte con el doctor asignado por defecto? (sí/no)",
            "done": False
        }



    # ====== 5) DOCTOR (por ahora default) ======
    if sess.state == "ASK_DOCTOR":
        norm = normalize_es(text)

        YES = {"si", "s", "claro", "ok", "okay", "acepto", "confirmo", "de acuerdo", "dale"}
        NO = {"no", "n", "cancelar", "cancela", "negativo"}

        if norm in YES:
            # Por ahora usamos el doctor default siempre.
            data["doctor"] = settings.DEFAULT_PROVIDER_ID
            crud.update_voice_session(db, sess, "CONFIRM", data)

            return {
                "session_id": sess.id,
                "prompt": (
                    "Voy a agendar:"
                    f"Paciente: {data.get('full_name')}"
                    f"Teléfono: {data.get('phone')}"
                    f"Fecha: {data.get('date')}"
                    f"Hora: {data['chosen_slot']['start'][11:16]}"
                    "¿Confirmas la cita? (sí/no)"
                ),
                "done": False
            }

        if norm in NO:
            return {
                "session_id": sess.id,
                "prompt": "Por ahora solo tenemos el doctor asignado por defecto. ¿Confirmamos con él? (sí/no)",
                "done": False
            }

        return {
            "session_id": sess.id,
            "prompt": "No entendí 😅 Responde por favor: 'sí' o 'no'.",
            "done": False
        }

    # ====== 6) CONFIRMAR + GUARDAR ======
    if sess.state == "CONFIRM":
        norm = normalize_es(text)

        YES = {"si", "s", "claro", "ok", "okay", "acepto", "confirmo", "de acuerdo", "dale"}
        NO = {"no", "n", "cancelar", "cancela", "negativo"}

        if norm in NO:
            # Volvemos a pedir fecha:
            crud.update_voice_session(db, sess, "INFO_GENERAL", data)
            return {
                "session_id": sess.id,
                "prompt": "De acuerdo. ¿Qué fecha prefieres? (Ej: mañana, lunes, 2026-02-10)",
                "done": False
            }

        if norm not in YES:
            return {
                "session_id": sess.id,
                "prompt": "Solo para confirmar 😊 ¿sí o no?",
                "done": False
            }

        start_dt = datetime.fromisoformat(data["chosen_slot"]["start"])

        patient = crud.get_or_create_patient(
            db,
            clinic_id=clinic_id,
            full_name=data["full_name"],
            phone=data["phone"]
        )


        crud.create_appointment(
            db=db,
            clinic_id=clinic_id,
            patient_id=patient.id,
            provider_id=settings.DEFAULT_PROVIDER_ID,
            type_id=settings.DEFAULT_APPT_TYPE_ID,
            start_time=start_dt
        )


        crud.update_voice_session(db, sess, "END", data)

        return {
            "session_id": sess.id,
            "prompt": (
                "✅ Tu cita quedó agendada correctamente.\n"
                "Gracias por contactarnos.\n"
                "¡Que tengas un excelente día! 🙌"
            ),
            "done": True
        }

    return {
        "session_id": sess.id,
        "prompt": "La sesión ya terminó. Si deseas iniciar otra, usa /voice/start",
        "done": True
    }


@router.post("/message", response_model=schemas.VoiceMessageResponse)
def voice_message(
    request: Request,
    payload: schemas.VoiceMessageRequest,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
    clinic = require_clinic(db, slug)

    

    # defaults por clínica (sin columnas en Clinic)
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


    return handle_message(
        db,
        clinic.id,
        payload.session_id,
        payload.text,
        provider_id=provider_id,
        type_id=type_id,
    )





# (Más adelante aquí pondremos el webhook de Twilio para llamadas reales)
@router.post("/inbound")
async def inbound_call(request: Request):
    return {"message": "Inbound call endpoint listo"}


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Recibe un archivo de audio (m4a/mp3/wav) y devuelve texto transcrito."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió archivo")

    suffix = os.path.splitext(file.filename)[1].lower() or ".m4a"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        client = OpenAI()
        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="es"
            )
        return {"text": transcript.text, "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error transcribiendo audio: {str(e)}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@router.post("/speak")
async def speak(payload: dict = Body(...)):
    """Recibe { "text": "..." } y devuelve un mp3 con la voz."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Falta 'text' en el body")

    client = OpenAI()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        out_path = tmp.name

    try:
        audio = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )
        with open(out_path, "wb") as f:
            f.write(audio.read())

        return FileResponse(out_path, media_type="audio/mpeg", filename="respuesta.mp3")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error generando TTS: {str(e)}")
    finally:
        # Nota: para limpieza automática, luego podemos migrar a StreamingResponse.
        pass


@router.post("/chat-audio")
async def chat_audio(
    request: Request,
    session_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):

    """Recibe audio, lo transcribe, pasa por el flujo conversacional y devuelve mp3."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió archivo")

    suffix = os.path.splitext(file.filename)[1].lower() or ".m4a"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        client = OpenAI()

        # 1) Transcripción
        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="es"
            )
        texto_usuario = transcript.text

        # 2) Flujo conversacional (reutiliza la misma lógica de /message)

        slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
        
        clinic = require_clinic(db, slug)

        result = handle_message(db, clinic.id, session_id, texto_usuario)


        prompt = result["prompt"]

        # 3) TTS del prompt
        audio = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=prompt,
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_out:
            out_path = tmp_out.name
            tmp_out.write(audio.read())

        return FileResponse(out_path, media_type="audio/mpeg", filename="respuesta.mp3")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

@router.post("/chat-audio-json")
async def chat_audio_json(
    request: Request,
    session_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):

    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió archivo")

    suffix = os.path.splitext(file.filename)[1].lower() or ".webm"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        client = OpenAI()

        # 1) Transcripción
        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language = "es"
            )
        user_text = (transcript.text or "").strip()

        # 2) Flujo conversacional
        slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
        clinic = require_clinic(db, slug)

        result = handle_message(db, clinic.id, session_id, user_text)

        request: Request
        x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
        x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),

        return {
            "session_id": result.get("session_id", session_id),
            "transcript": user_text,
            "prompt": result.get("prompt"),
            "done": bool(result.get("done", False)),
        }

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


from sqlalchemy import text

@router.get("/debug/clinic")
def debug_clinic(
    request: Request,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
    clinic = require_clinic(db, slug)

    # OJO: cambia nombres de tabla si en tu DB se llaman distinto
    rules_count = db.execute(text("SELECT COUNT(*) FROM availability_rules WHERE clinic_id = :cid"), {"cid": clinic.id}).scalar()

    return {
        "slug": slug,
        "clinic_id": clinic.id,
        "provider_id_used": settings.DEFAULT_PROVIDER_ID,
        "type_id_used": settings.DEFAULT_APPT_TYPE_ID,
        "availability_rules_count": int(rules_count or 0),
    }
