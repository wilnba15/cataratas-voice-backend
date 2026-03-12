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

from sqlalchemy import asc, text
from app import models


router = APIRouter(prefix="/voice", tags=["voice"])

FALLBACK_CLINIC_NAME = "la clínica"


def clinic_display_name(clinic) -> str:
    return getattr(clinic, "name", None) or FALLBACK_CLINIC_NAME


def clinic_display_address(clinic) -> str:
    return (getattr(clinic, "address", None) or "").strip()


def appointment_type_label(appt_type) -> str:
    return (
        getattr(appt_type, "name", None)
        or getattr(appt_type, "code", None)
        or f"Especialidad {appt_type.id}"
    )


def get_appointment_types_for_clinic(db: Session, clinic_id: int):
    return (
        db.query(models.AppointmentType)
        .filter(models.AppointmentType.clinic_id == clinic_id)
        .order_by(asc(models.AppointmentType.id))
        .all()
    )


def get_providers_for_clinic(db: Session, clinic_id: int, limit: int | None = None):
    q = (
        db.query(models.Provider)
        .filter(models.Provider.clinic_id == clinic_id)
        .order_by(asc(models.Provider.id))
    )
    if limit:
        q = q.limit(limit)
    return q.all()


def build_specialty_menu(appt_types) -> tuple[str, list]:
    lines = []
    options = []
    for idx, appt in enumerate(appt_types, start=1):
        label = appointment_type_label(appt)
        options.append({"index": idx, "id": appt.id, "label": label})
        lines.append(f"{idx}) {label}")
    return "\n".join(lines), options


def build_provider_menu(providers) -> tuple[str, list]:
    lines = []
    options = []
    for idx, provider in enumerate(providers, start=1):
        label = getattr(provider, "name", None) or f"Doctor {idx}"
        options.append({"index": idx, "id": provider.id, "label": label})
        lines.append(f"{idx}) {label}")
    return "\n".join(lines), options


@router.get("/test-slots")
def test_slots(
    request: Request,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
    clinic = require_clinic(db, slug)

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
        limit=3,
    )

    slots = res["value"] if isinstance(res, dict) and "value" in res else res
    return [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in slots]


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
    return {
        "session_id": sess.id,
        "prompt": f"Hola 👋 Bienvenido a {clinic_display_name(clinic)}. ¿Cuál es tu nombre completo?"
    }


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


def normalize_es(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_date_es(text: str, now: datetime) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    t = raw.strip().lower()
    norm = normalize_es(raw)

    if re.fullmatch(r"\d{8}", t):
        try:
            dt = datetime.strptime(t, "%Y%m%d").date()
            return dt.isoformat()
        except ValueError:
            return None

    if norm == "hoy":
        return now.date().isoformat()
    if norm in ("manana", "mañana"):
        return (now.date() + timedelta(days=1)).isoformat()

    if norm in WEEKDAYS_ES:
        target = WEEKDAYS_ES[norm]
        delta = (target - now.weekday()) % 7
        delta = 7 if delta == 0 else delta
        return (now.date() + timedelta(days=delta)).isoformat()

    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", t)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        d = int(m.group(3))
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return None

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
    if m:
        d = int(m.group(1))
        mo = int(m.group(2))
        y = int(m.group(3))
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return None

    month_map = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    m = re.search(
        r"\b(\d{1,2})\s*(?:de\s+)?"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"\s*(?:de\s+)?(\d{4})\b",
        norm,
    )
    if m:
        d = int(m.group(1))
        month_name = m.group(2)
        y = int(m.group(3))
        mo = month_map.get(month_name)
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return None

    return None


MONTHS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}
WEEKDAYS_NAME_ES = {
    0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo",
}


def format_date_es(date_iso: str) -> str:
    try:
        d = datetime.fromisoformat(date_iso).date()
    except Exception:
        return date_iso
    wd = WEEKDAYS_NAME_ES.get(d.weekday(), "")
    month = MONTHS_ES.get(d.month, "")
    return f"{wd}, {d.day} de {month} de {d.year}".strip()


def format_time_hhmm(iso_dt: str) -> str:
    return (iso_dt or "")[11:16]


def looks_like_phone(s: str) -> bool:
    digits = re.sub(r"\D", "", s or "")
    return len(digits) >= 8 and (len(digits) >= int(0.7 * max(1, len(s))))


def parse_yes_no(text: str) -> bool | None:
    norm = normalize_es(text)

    if norm == "1":
        return True
    if norm == "2":
        return False

    yes = {"si", "sí", "s", "claro", "ok", "okay", "acepto", "confirmo", "de acuerdo", "dale", "afirmativo"}
    no = {"no", "n", "cancelar", "cancela", "negativo"}

    for y in yes:
        if y in norm:
            return True
    for n in no:
        if n in norm:
            return False
    return None


def get_defaults_for_clinic(db: Session, clinic_id: int) -> tuple[int, int]:
    prov = (
        db.query(models.Provider)
        .filter(models.Provider.clinic_id == clinic_id)
        .order_by(asc(models.Provider.id))
        .first()
    )

    appt = (
        db.query(models.AppointmentType)
        .filter(models.AppointmentType.clinic_id == clinic_id)
        .order_by(asc(models.AppointmentType.id))
        .first()
    )

    provider_id = (prov.id if prov else None) or settings.DEFAULT_PROVIDER_ID
    type_id = (appt.id if appt else None) or settings.DEFAULT_APPT_TYPE_ID
    return provider_id, type_id


def handle_message(db, clinic_id, session_id, text, provider_id: int | None = None, type_id: int | None = None):
    sess = crud.get_voice_session(db, session_id, clinic_id=clinic_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    clinic = db.query(models.Clinic).filter(models.Clinic.id == clinic_id).first()

    text = (text or "").strip()
    if not text:
        return {
            "session_id": session_id,
            "prompt": "No te escuché bien 😅 ¿me repites?",
            "done": False,
        }

    data = crud.session_data(sess)

    if provider_id is None or type_id is None:
        provider_id, type_id = get_defaults_for_clinic(db, clinic_id)

    if sess.state == "ASK_NAME":
        if len(text.split()) < 2 or looks_like_phone(text):
            return {
                "session_id": sess.id,
                "prompt": "Para registrarte correctamente necesito tus *nombres y apellidos* 😊",
                "done": False,
            }

        data["full_name"] = text
        crud.update_voice_session(db, sess, "ASK_PHONE", data)
        return {
            "session_id": sess.id,
            "prompt": f"Gracias {data['full_name']} 😊 Ahora indícame tu número telefónico por favor.",
            "done": False,
        }

    if sess.state == "ASK_PHONE":
        data["phone"] = text

        appt_types = get_appointment_types_for_clinic(db, clinic_id)
        if not appt_types:
            crud.update_voice_session(db, sess, "INFO_GENERAL", data)
            return {
                "session_id": sess.id,
                "prompt": "Perfecto ✅ Ahora sí, agendemos tu cita.\n¿Qué fecha deseas? (Ejemplo: 18 marzo 2026)",
                "done": False,
            }

        menu, specialty_options = build_specialty_menu(appt_types)
        data["specialty_options"] = specialty_options

        crud.update_voice_session(db, sess, "ASK_SPECIALTY", data)
        return {
            "session_id": sess.id,
            "prompt": (
                "Perfecto ✅\n"
                "Antes de agendar, dime por favor la *especialidad*:\n"
                f"{menu}\n"
                f"Responde con el número del 1 al {len(specialty_options)}."
            ),
            "done": False,
        }

    if sess.state == "ASK_SPECIALTY":
        options = data.get("specialty_options") or []
        if not options:
            appt_types = get_appointment_types_for_clinic(db, clinic_id)
            _, options = build_specialty_menu(appt_types)

        if not options:
            crud.update_voice_session(db, sess, "INFO_GENERAL", data)
            return {
                "session_id": sess.id,
                "prompt": "No encontré especialidades configuradas. Indícame la fecha deseada.",
                "done": False,
            }

        raw = (text or "").strip().lower()
        choice = None
        m = re.search(r"\b(\d+)\b", raw)
        if m:
            choice = int(m.group(1))

        norm = normalize_es(text)
        selected = None

        if choice is not None:
            for opt in options:
                if opt["index"] == choice:
                    selected = opt
                    break

        if selected is None:
            for opt in options:
                label_norm = normalize_es(opt["label"])
                if label_norm in norm or norm in label_norm:
                    selected = opt
                    break

        if selected is None:
            menu = "\n".join([f"{opt['index']}) {opt['label']}" for opt in options])
            return {
                "session_id": sess.id,
                "prompt": f"No entendí 😅 Elige una opción válida:\n{menu}",
                "done": False,
            }

        data["specialty"] = selected["label"]
        data["type_id"] = selected["id"]
        crud.update_voice_session(db, sess, "INFO_GENERAL", data)
        return {
            "session_id": sess.id,
            "prompt": (
                f"Perfecto ✅ Especialidad: {data['specialty']}\n\n"
                "Ahora sí, agendemos tu cita.\n"
                "¿Para qué fecha deseas la cita? (Ejemplo: 18 marzo 2026)"
            ),
            "done": False,
        }

    if sess.state == "INFO_GENERAL":
        date_iso = parse_date_es(text, now=datetime.now())
        if not date_iso:
            return {
                "session_id": sess.id,
                "prompt": "No entendí la fecha 😅. Repite nuevamente.",
                "done": False,
            }

        data["date"] = date_iso

        selected_type_id = int(data.get("type_id") or type_id)
        selected_provider_id = int(data.get("doctor") or provider_id)

        date_start = datetime.fromisoformat(data["date"] + "T00:00:00")
        date_end = date_start + timedelta(days=1)

        res = get_next_slots(
            db,
            clinic_id=clinic_id,
            provider_id=selected_provider_id,
            type_id=selected_type_id,
            from_dt=date_start,
            days_ahead=1,
            limit=200,
        )
        all_slots = res["value"] if isinstance(res, dict) and "value" in res else res
        day_slots = [s for s in all_slots if date_start <= s[0] < date_end]

        if not day_slots:
            return {
                "session_id": sess.id,
                "prompt": "Ese día no hay disponibilidad 😬 ¿Qué otra fecha te sirve?",
                "done": False,
            }

        options = day_slots[:5]
        data["slot_options"] = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in options]
        crud.update_voice_session(db, sess, "ASK_SLOT", data)

        opciones_txt = "\n".join([f"{i+1}) {opt['start'][11:16]}" for i, opt in enumerate(data["slot_options"])])
        return {
            "session_id": sess.id,
            "prompt": f"Estos son los horarios disponibles para {format_date_es(data['date'])}:\n{opciones_txt}\nElige el número del 1 al {len(data['slot_options'])}.",
            "done": False,
        }

    words_to_num = {
        "uno": 1, "una": 1, "primero": 1, "primera": 1,
        "dos": 2, "segundo": 2, "segunda": 2,
        "tres": 3, "tercero": 3, "tercera": 3,
        "cuatro": 4, "cuarto": 4, "cuarta": 4,
        "cinco": 5, "quinto": 5, "quinta": 5,
    }

    if sess.state == "ASK_SLOT":
        raw = (text or "").strip().lower()

        m = re.search(r"\b([1-5])\b", raw)
        if m:
            idx = int(m.group(1)) - 1
        else:
            n = words_to_num.get(raw)
            if n is None:
                return {
                    "session_id": sess.id,
                    "prompt": "No entendí 😅 Elige un número de la lista (por ejemplo: '3').",
                    "done": False,
                }
            idx = n - 1

        try:
            chosen = data.get("slot_options", [])[idx]
        except Exception:
            return {
                "session_id": sess.id,
                "prompt": "Elige un número válido de la lista porfa 😊",
                "done": False,
            }

        data["chosen_slot"] = chosen

        providers = get_providers_for_clinic(db, clinic_id, limit=5)
        if not providers:
            data["doctor"] = int(provider_id)
            data["doctor_name"] = "Doctor asignado"
            crud.update_voice_session(db, sess, "CONFIRM", data)

            hora = format_time_hhmm(data.get("chosen_slot", {}).get("start", ""))
            fecha_humana = format_date_es(data.get("date", ""))

            return {
                "session_id": sess.id,
                "prompt": (
                    "Voy a agendar tu cita con estos datos:\n"
                    f"Paciente: {data.get('full_name', '')}\n"
                    f"Teléfono: {data.get('phone', '')}\n"
                    f"Especialidad: {data.get('specialty', '')}\n"
                    f"Doctor: {data.get('doctor_name', '')}\n"
                    f"Fecha: {fecha_humana}\n"
                    f"Hora: {hora}\n\n"
                    "Para confirmar tu cita, presiona 1. Para cancelar, presiona 2."
                ),
                "done": False,
            }

        menu, provider_options = build_provider_menu(providers)
        data["doctor_options"] = provider_options
        crud.update_voice_session(db, sess, "ASK_DOCTOR", data)

        return {
            "session_id": sess.id,
            "prompt": (
                "Perfecto ✅ Ahora elige el doctor:\n"
                f"{menu}\n"
                f"Responde con el número del 1 al {len(provider_options)}. (También puedes decir el nombre)"
            ),
            "done": False,
        }

    if sess.state == "ASK_DOCTOR":
        options = data.get("doctor_options") or []
        if not options:
            providers = get_providers_for_clinic(db, clinic_id, limit=5)
            _, options = build_provider_menu(providers)

        raw = (text or "").strip().lower()
        choice = None
        mnum = re.search(r"\b(\d+)\b", raw)
        if mnum:
            choice = int(mnum.group(1))

        norm = normalize_es(text)
        selected = None

        if choice is not None:
            for opt in options:
                if opt["index"] == choice:
                    selected = opt
                    break

        if selected is None:
            for opt in options:
                label_norm = normalize_es(opt["label"])
                if label_norm in norm or norm in label_norm:
                    selected = opt
                    break

        if selected is None:
            menu = "\n".join([f"{opt['index']}) {opt['label']}" for opt in options])
            return {
                "session_id": sess.id,
                "prompt": f"No entendí 😅 Elige una opción válida para el doctor:\n{menu}",
                "done": False,
            }

        data["doctor"] = int(selected["id"])
        data["doctor_name"] = selected["label"]
        crud.update_voice_session(db, sess, "CONFIRM", data)

        hora = format_time_hhmm(data.get("chosen_slot", {}).get("start", ""))
        fecha_humana = format_date_es(data.get("date", ""))

        return {
            "session_id": sess.id,
            "prompt": (
                "Voy a agendar tu cita con estos datos:\n"
                f"Paciente: {data.get('full_name', '')}\n"
                f"Teléfono: {data.get('phone', '')}\n"
                f"Especialidad: {data.get('specialty', '')}\n"
                f"Doctor: {data.get('doctor_name', '')}\n"
                f"Fecha: {fecha_humana}\n"
                f"Hora: {hora}\n\n"
                "Para confirmar tu cita, presiona 1. Para cancelar, presiona 2."
            ),
            "done": False,
        }

    if sess.state == "CONFIRM":
        yn = parse_yes_no(text)

        if yn is None:
            return {
                "session_id": sess.id,
                "prompt": "Para confirmar tu cita, presiona 1. Para cancelar, presiona 2.",
                "done": False,
            }

        if yn is False:
            crud.update_voice_session(db, sess, "INFO_GENERAL", data)
            return {
                "session_id": sess.id,
                "prompt": "De acuerdo. ¿Qué fecha prefieres? (Ej: mañana, lunes, 2026-03-18)",
                "done": False,
            }

        start_dt = datetime.fromisoformat(data["chosen_slot"]["start"])

        patient = crud.get_or_create_patient(
            db,
            clinic_id=clinic_id,
            full_name=data["full_name"],
            phone=data["phone"],
        )

        prov_id = int(data.get("doctor") or provider_id)
        appt_type_id = int(data.get("type_id") or type_id)

        crud.create_appointment(
            db=db,
            clinic_id=clinic_id,
            patient_id=patient.id,
            provider_id=prov_id,
            type_id=appt_type_id,
            start_time=start_dt,
        )

        crud.update_voice_session(db, sess, "END", data)

        clinic_name = clinic_display_name(clinic)
        clinic_address = clinic_display_address(clinic)
        location_text = f" Te esperamos en {clinic_name}." if clinic_name else ""
        if clinic_address:
            location_text += f" {clinic_address}."

        return {
            "session_id": sess.id,
            "prompt": (
                "✅ Listo. "
                f"Tu cita queda agendada para {format_date_es(data.get('date', ''))}, "
                f"a las {format_time_hhmm(data.get('chosen_slot', {}).get('start', ''))}. "
                f"En la especialidad de {data.get('specialty', '')}, "
                f"con el doctor {data.get('doctor_name', '')}."
                f"{location_text} "
                "Que tengas un excelente día 🙌"
            ),
            "done": True,
        }

    if sess.state == "END":
        return {
            "session_id": sess.id,
            "prompt": "La sesión ya terminó. Si deseas iniciar otra, usa /voice/start",
            "done": True,
        }

    return {
        "session_id": sess.id,
        "prompt": "No entendí 😅 ¿me repites por favor?",
        "done": False,
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


@router.post("/inbound")
async def inbound_call(request: Request):
    return {"message": "Inbound call endpoint listo"}


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
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
                language="es",
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
                language="es",
            )
        texto_usuario = transcript.text

        slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
        clinic = require_clinic(db, slug)

        result = handle_message(db, clinic.id, session_id, texto_usuario)
        prompt = result["prompt"]

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

        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="es",
            )
        user_text = (transcript.text or "").strip()

        slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
        clinic = require_clinic(db, slug)

        result = handle_message(db, clinic.id, session_id, user_text)

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


@router.get("/debug/clinic")
def debug_clinic(
    request: Request,
    db: Session = Depends(get_db),
    x_clinic_slug: str | None = Header(default=None, alias="X-Clinic-Slug"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    slug = get_clinic_slug(request, x_clinic_slug, x_forwarded_host)
    clinic = require_clinic(db, slug)

    rules_count = db.execute(
        text("SELECT COUNT(*) FROM availability_rules WHERE clinic_id = :cid"),
        {"cid": clinic.id},
    ).scalar()

    provider_id, type_id = get_defaults_for_clinic(db, clinic.id)

    return {
        "slug": slug,
        "clinic_id": clinic.id,
        "provider_id_used": provider_id,
        "type_id_used": type_id,
        "availability_rules_count": int(rules_count or 0),
    }
