from pydantic import BaseModel
from datetime import datetime

class AppointmentCreate(BaseModel):
    full_name: str
    phone: str
    provider_id: int
    type_id: int
    start_time: datetime

class AppointmentOut(BaseModel):
    id: int
    start_time: datetime
    end_time: datetime
    status: str

    class Config:
        from_attributes = True

class VoiceStartResponse(BaseModel):
    session_id: int
    prompt: str

class VoiceMessageRequest(BaseModel):
    session_id: int
    text: str

class VoiceMessageResponse(BaseModel):
    session_id: int
    prompt: str
    done: bool = False
