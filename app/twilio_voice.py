from fastapi import APIRouter, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather

router = APIRouter()

@router.post("/twilio/voice")
async def twilio_voice(request: Request):
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        action="/twilio/process",
        method="POST",
        language="es-ES"
    )

    gather.say("Hola, soy el asistente de la clínica de cataratas. ¿En qué puedo ayudarte?")
    response.append(gather)

    return Response(content=str(response), media_type="application/xml")


@router.post("/twilio/process")
async def twilio_process(request: Request):
    form = await request.form()
    speech = form.get("SpeechResult", "")

    response = VoiceResponse()

    # Aquí luego conectamos con tu /voice/message
    response.say(f"Has dicho: {speech}. Estamos procesando tu solicitud.")

    return Response(content=str(response), media_type="application/xml")
