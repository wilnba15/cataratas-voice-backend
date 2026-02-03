from openai import OpenAI
import os

client = OpenAI()

AUDIO_PATH = "Recording.m4a"  # usa el nombre real

if not os.path.exists(AUDIO_PATH):
    raise FileNotFoundError(f"No existe el archivo: {AUDIO_PATH}")

with open(AUDIO_PATH, "rb") as f:
    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=f,
    )

print("=== TRANSCRIPCIÓN ===")
print(transcript.text)
