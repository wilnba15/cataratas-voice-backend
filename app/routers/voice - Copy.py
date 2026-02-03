from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from fastapi import UploadFile, File
from openai import OpenAI
import os
import tempfile

from fastapi import Body
from fastapi.responses import FileResponse



from app.db import get_db
from app.config import settings
from app.services.availability import get_next_slots

from app import crud, schemas

router = APIRouter(prefix="/voice", tags=["voice"])


# Endpoint de prueba de disponibilidad (SIN Twilio todavía)
@router.get("/test-slots")
def test_slots(db: Session = Depends(get_db)):
    provider_id = settings.DEFAULT_PROVIDER_ID
    type_id = settings.DEFAULT_APPT_TYPE_ID

    slots = get_next_slots(
        db,
        provider_id=provider_id,
        type_id=type_id,
        from_dt=datetime.now(),
        limit=3
    )

    return [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in slots]


# ====== NUEVO: Flujo conversacional por texto (sin Twilio aún) ======

@router.post("/start", response_model=schemas.VoiceStartResponse)
def start_voice(db: Session = Depends(get_db)):
    sess = crud.create_voice_session(db)
    return {"session_id": sess.id, "prompt": "Hola 👋 ¿Cuál es tu nombre completo?"}


@router.post("/message", response_model=schemas.VoiceMessageResponse)
def voice_message(payload: schemas.VoiceMessageRequest, db: Session = Depends(get_db)):
    sess = crud.get_voice_session(db, payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    text = (payload.text or "").strip()
    if not text:
        return {"session_id": payload.session_id, "prompt": "No te escuché bien 😅 ¿me repites?", "done": False}

    data = crud.session_data(sess)

    if sess.state == "ASK_NAME":
        data["full_name"] = text
        crud.update_voice_session(db, sess, "ASK_PHONE", data)
        return {"session_id": sess.id, "prompt": f"Perfecto, {data['full_name']} 😊 ¿Cuál es tu número de teléfono?", "done": False}

    if sess.state == "ASK_PHONE":
        data["phone"] = text
        crud.update_voice_session(db, sess, "ASK_DATE", data)
        return {"session_id": sess.id, "prompt": "Genial ✅ ¿Para qué fecha deseas la cita? (Ej: 2026-01-25)", "done": False}

    if sess.state == "ASK_DATE":
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except Exception:
            return {"session_id": sess.id, "prompt": "Formato inválido 😅 Usa así: 2026-01-25", "done": False}

        data["date"] = text

        provider_id = settings.DEFAULT_PROVIDER_ID
        type_id = settings.DEFAULT_APPT_TYPE_ID

        # Pedimos slots desde "hoy" (o desde ahora) y luego filtramos por la fecha solicitada
        all_slots = get_next_slots(
            db,
            provider_id=provider_id,
            type_id=type_id,
            from_dt=datetime.now(),
            days_ahead=30,
            limit=50
        )

        # Filtrar por el día elegido
        day_slots = [s for s in all_slots if s[0].date().isoformat() == data["date"]]

        if not day_slots:
            crud.update_voice_session(db, sess, "ASK_DATE", data)
            return {"session_id": sess.id, "prompt": "Ese día está full 😬 ¿Qué otra fecha te sirve? (YYYY-MM-DD)", "done": False}

        # guardamos 5 opciones
        options = day_slots[:5]
        data["slot_options"] = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in options]

        crud.update_voice_session(db, sess, "ASK_SLOT", data)

        opciones_txt = "\n".join([f"{i+1}) {opt['start'][11:16]}" for i, opt in enumerate(data["slot_options"])])
        return {
            "session_id": sess.id,
            "prompt": f"Estos son los horarios disponibles para {data['date']}:\n{opciones_txt}\nResponde con el número (1-5).",
            "done": False
        }

    if sess.state == "ASK_SLOT":
        try:
            idx = int(text) - 1
            chosen = data.get("slot_options", [])[idx]
        except Exception:
            return {"session_id": sess.id, "prompt": "Elige un número válido (1-5) porfa 😊", "done": False}

        data["chosen_slot"] = chosen
        crud.update_voice_session(db, sess, "DONE", data)

        return {
            "session_id": sess.id,
            "prompt": f"Listo ✅ Tengo estos datos:\n- Nombre: {data.get('full_name')}\n- Teléfono: {data.get('phone')}\n- Fecha: {data.get('date')}\n- Hora: {chosen['start'][11:16]}\n\n¿Confirmas la cita? (sí/no)",
            "done": True
        }

    return {"session_id": sess.id, "prompt": "La sesión ya terminó. Si deseas, inicia otra con /voice/start", "done": True}


# (Más adelante aquí pondremos el webhook de Twilio para llamadas reales)
@router.post("/inbound")
async def inbound_call(request: Request):
    return {"message": "Inbound call endpoint listo"}


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Recibe un archivo de audio (m4a/mp3/wav) y devuelve texto transcrito.
    """
    # Validación rápida
    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió archivo")

    # Guardar temporalmente (por seguridad y compatibilidad)
    suffix = os.path.splitext(file.filename)[1].lower() or ".m4a"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    try:
        client = OpenAI()

        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
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
    """
    Recibe { "text": "..." } y devuelve un mp3 con la voz.
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Falta 'text' en el body")

    client = OpenAI()

    # Archivo temporal para devolverlo como mp3
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        out_path = tmp.name

    try:
        audio = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )

        # Guardar bytes al mp3
        audio_bytes = audio.read()
        with open(out_path, "wb") as f:
            f.write(audio_bytes)

        return FileResponse(
            out_path,
            media_type="audio/mpeg",
            filename="respuesta.mp3",
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error generando TTS: {str(e)}")

    finally:
        # OJO: FileResponse necesita el archivo en disco para enviarlo.
        # Para no borrarlo antes de tiempo, lo dejamos y luego lo limpiamos con job/cron (o lo borramos en otra ruta).
        pass

@router.post("/chat-audio")
async def chat_audio(file: UploadFile = File(...)):
    """
    Recibe audio, lo transcribe, procesa la intención
    y devuelve respuesta hablada (mp3).
    """

    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió archivo")

    suffix = os.path.splitext(file.filename)[1].lower() or ".m4a"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    client = OpenAI()

    try:
        # 1️⃣ Transcripción
        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )

        texto_usuario = transcript.text

        # 2️⃣ Pasar al flujo conversacional existente
        respuesta_texto = handle_message(texto_usuario)

        # 3️⃣ Convertir respuesta a voz
        audio = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=respuesta_texto,
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_out:
            out_path = tmp_out.name
            tmp_out.write(audio.read())

        return FileResponse(
            out_path,
            media_type="audio/mpeg",
            filename="respuesta.mp3",
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    finally:
        try:
            os.remove(tmp_path)
        except:
            pass
