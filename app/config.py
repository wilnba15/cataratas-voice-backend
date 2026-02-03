import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./cataratas.db")
    CLINIC_NAME: str = os.getenv("CLINIC_NAME", "Clinica Ocular")
    DEFAULT_PROVIDER_ID: int = int(os.getenv("DEFAULT_PROVIDER_ID", "1"))
    DEFAULT_APPT_TYPE_ID: int = int(os.getenv("DEFAULT_APPT_TYPE_ID", "1"))
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

settings = Settings()
