# agent/main.py — Servidor FastAPI + Webhook de WhatsApp para ELI (Agua ELIM)
# Generado por AgentKit
#
# El webhook responde 200 de inmediato y procesa en segundo plano: así YCloud
# no reintenta por demora y el cliente nunca recibe respuestas duplicadas.

import os
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi import HTTPException
from dotenv import load_dotenv

from agent.brain import generar_respuesta, inicializar_rag
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    marcar_procesado, bot_pausado, pausar_bot,
)
from agent.providers import obtener_proveedor
from agent.providers.base import MensajeEntrante
from agent import voice

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
logging.basicConfig(level=logging.DEBUG if ENVIRONMENT == "development" else logging.INFO)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
HANDOFF_PAUSE_HOURS = float(os.getenv("HANDOFF_PAUSE_HOURS", "8"))

# Un candado por cliente: si escribe 3 mensajes seguidos se responden en orden
_candados: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    await inicializar_rag()
    logger.info(f"ELI iniciado | Puerto {PORT} | Proveedor {proveedor.__class__.__name__}")
    yield


app = FastAPI(title="ELI — Agente WhatsApp de Agua ELIM", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def health_check():
    """Endpoint de salud para monitoreo."""
    return {"status": "ok", "service": "eli-agent"}


@app.get("/media/{nombre}")
async def media(nombre: str):
    """Sirve las notas de voz generadas para que YCloud las descargue."""
    ruta = voice.ruta_media(nombre)
    if not ruta:
        raise HTTPException(status_code=404)
    return FileResponse(ruta, media_type="audio/ogg")


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerida por Meta Cloud API, no-op para YCloud)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request, background: BackgroundTasks):
    """Recibe eventos de WhatsApp, responde 200 al instante y procesa en segundo plano."""
    mensajes = await proveedor.parsear_webhook(request)  # lanza 401 si la firma no es válida
    for msg in mensajes:
        background.add_task(_procesar_mensaje, msg)
    return {"status": "ok"}


async def _procesar_mensaje(msg: MensajeEntrante):
    """Lógica central: duplicados → pausa humana → IA → respuesta."""
    try:
        if msg.es_propio or not msg.texto or not msg.telefono:
            return
        if not await marcar_procesado(msg.mensaje_id):
            logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
            return

        async with _candados[msg.telefono]:
            # Un asesor respondió desde la app WhatsApp Business → el bot se aparta
            if msg.es_eco_humano:
                await guardar_mensaje(msg.telefono, "assistant", f"[Asesor humano]: {msg.texto}")
                await pausar_bot(msg.telefono, HANDOFF_PAUSE_HOURS, "asesor_respondio")
                logger.info(f"Asesor atendiendo a {msg.telefono} — bot pausado {HANDOFF_PAUSE_HOURS}h")
                return

            # Nota de voz → texto (antes de la pausa, para que el asesor también tenga el contexto)
            es_voz = msg.tipo == "audio" and bool(msg.media_url)
            if es_voz:
                audio = await proveedor.descargar_media(msg.media_url)
                transcripcion = await voice.transcribir(audio, msg.media_mime or "audio/ogg") if audio else ""
                if transcripcion:
                    msg.texto = f"[Nota de voz] {transcripcion}"
                else:
                    es_voz = False  # sin transcripción ELI pide que lo escriba; responde en texto

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            if await bot_pausado(msg.telefono):
                # Guardamos el mensaje para que ELI tenga contexto cuando vuelva
                await guardar_mensaje(msg.telefono, "user", msg.texto)
                logger.info(f"Bot en pausa para {msg.telefono} — lo atiende una persona")
                return

            historial = await obtener_historial(msg.telefono)
            respuesta, _, _ = await generar_respuesta(
                msg.texto, historial, telefono=msg.telefono, nombre_perfil=msg.nombre
            )

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)
            await _responder(msg.telefono, respuesta, en_voz=es_voz)
            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

    except Exception as e:
        logger.exception(f"Error procesando mensaje de {msg.telefono}: {e}")


async def _responder(telefono: str, respuesta: str, en_voz: bool):
    """Si el cliente habló, ELI contesta con nota de voz; si algo falla, cae a texto.
    Los enlaces (pagos, catálogo) no se pueden leer en voz alta, así que también van por texto."""
    if en_voz and voice.voz_habilitada():
        audio = await voice.sintetizar(respuesta)
        if audio and await proveedor.enviar_audio(telefono, voice.guardar_audio(audio)):
            if voice.tiene_enlace(respuesta):
                await proveedor.enviar_mensaje(telefono, respuesta)
            return
        logger.warning(f"No se pudo responder con voz a {telefono} — se envía texto")
    await proveedor.enviar_mensaje(telefono, respuesta)
