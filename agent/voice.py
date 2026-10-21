# agent/voice.py — Notas de voz: audio → texto (transcripción) y texto → audio (TTS)
#
# Flujo: el cliente manda una nota de voz → se transcribe y ELI la procesa como texto →
# la respuesta se convierte en audio OGG/Opus (el formato de las notas de voz de WhatsApp),
# se guarda unos minutos y se sirve en {PUBLIC_BASE_URL}/media/<token>.ogg para que
# YCloud la descargue y la envíe.

import os
import re
import time
import secrets
import logging
import tempfile
from pathlib import Path
from openai import AsyncOpenAI
from agent.http import nuevo_cliente

logger = logging.getLogger("agentkit")

TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "coral")
TTS_INSTRUCTIONS = os.getenv(
    "OPENAI_TTS_INSTRUCTIONS",
    "Habla en español latino, con tono cálido, cercano y natural, a ritmo tranquilo.",
)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Los audios generados solo deben vivir lo suficiente para que YCloud los descargue
MEDIA_DIR = Path(tempfile.gettempdir()) / "eli_media"
MEDIA_TTL_SEG = 3600
NOMBRE_MEDIA = re.compile(r"^[A-Za-z0-9_-]{20,}\.ogg$")

_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), http_client=nuevo_cliente(timeout=60.0))


def voz_habilitada() -> bool:
    """Sin URL pública YCloud no puede descargar el audio: se responde en texto."""
    return bool(PUBLIC_BASE_URL)


async def transcribir(audio: bytes, mime: str = "audio/ogg") -> str:
    """Convierte la nota de voz del cliente en texto. Retorna '' si falla."""
    extension = "mp3" if "mpeg" in mime else "m4a" if "mp4" in mime else "ogg"
    try:
        r = await _client.audio.transcriptions.create(
            model=TRANSCRIBE_MODEL,
            file=(f"nota.{extension}", audio, mime.split(";")[0]),
        )
        return (r.text or "").strip()
    except Exception as e:
        logger.error(f"Error transcribiendo nota de voz: {e}")
        return ""


def _texto_para_voz(texto: str) -> str:
    """Quita lo que no se debe leer en voz alta: formato de WhatsApp, emojis y enlaces."""
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[*_~`]", "", texto)
    texto = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", texto)
    texto = re.sub(r"\bCOP\b", "pesos", texto)
    texto = re.sub(r"\bUSD\b", "dólares", texto)
    return re.sub(r"[ \t]+", " ", texto).strip()


def tiene_enlace(texto: str) -> bool:
    return bool(re.search(r"https?://\S+", texto))


async def sintetizar(texto: str) -> bytes | None:
    """Convierte la respuesta de ELI en una nota de voz OGG/Opus. Retorna None si falla."""
    limpio = _texto_para_voz(texto)[:4000]
    if not limpio:
        return None
    try:
        r = await _client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=limpio,
            instructions=TTS_INSTRUCTIONS,
            response_format="opus",
        )
        return r.content
    except Exception as e:
        logger.error(f"Error generando audio de respuesta: {e}")
        return None


def _limpiar_media_vieja():
    limite = time.time() - MEDIA_TTL_SEG
    for f in MEDIA_DIR.glob("*.ogg"):
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
        except OSError:
            pass


def guardar_audio(audio: bytes) -> str:
    """Guarda el audio con un nombre imposible de adivinar y retorna su URL pública."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    _limpiar_media_vieja()
    nombre = f"{secrets.token_urlsafe(24)}.ogg"
    (MEDIA_DIR / nombre).write_bytes(audio)
    return f"{PUBLIC_BASE_URL}/media/{nombre}"


def ruta_media(nombre: str) -> Path | None:
    """Ruta del audio pedido por YCloud, o None si el nombre no es válido o ya expiró."""
    if not NOMBRE_MEDIA.match(nombre):
        return None
    ruta = MEDIA_DIR / nombre
    return ruta if ruta.is_file() else None
