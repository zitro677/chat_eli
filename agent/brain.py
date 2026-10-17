# agent/brain.py — Cerebro del agente ELI: conexión con OpenAI
# Generado por AgentKit
#
# El system prompt = reglas de config/prompts.yaml + archivos de /knowledge.
# Ese bloque es idéntico en cada llamada, así OpenAI lo cachea automáticamente
# (los datos que cambian — fecha, teléfono — van en un mensaje aparte al final).

import os
import json
import yaml
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from openai import AsyncOpenAI
from dotenv import load_dotenv
from agent.http import nuevo_cliente

load_dotenv()
logger = logging.getLogger("agentkit")

MODELO = os.getenv("OPENAI_MODEL", "gpt-5-mini")
ESFUERZO = os.getenv("OPENAI_REASONING_EFFORT", "low")
ZONA_HORARIA = ZoneInfo(os.getenv("BUSINESS_TIMEZONE", "America/Chicago"))
MAX_ITERACIONES = 5  # límite de llamadas a herramientas por mensaje

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), http_client=nuevo_cliente(timeout=60.0))

_system_prompt_cache: str | None = None


# ── Configuración ────────────────────────────────────────────────────────────

def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def _cargar_knowledge(archivos: list[str]) -> str:
    """Concatena los archivos de /knowledge listados en prompts.yaml."""
    bloques = []
    for ruta in archivos:
        try:
            with open(os.path.join("knowledge", ruta), "r", encoding="utf-8-sig") as f:
                bloques.append(f"<archivo nombre=\"{ruta}\">\n{f.read().strip()}\n</archivo>")
        except FileNotFoundError:
            logger.warning(f"Archivo de conocimiento no encontrado: knowledge/{ruta}")
    return "\n\n".join(bloques)


def cargar_system_prompt() -> str:
    """System prompt completo (reglas + base de conocimiento). Se cachea en memoria."""
    global _system_prompt_cache
    if _system_prompt_cache is None:
        config = cargar_config_prompts()
        reglas = config.get("system_prompt", "Eres ELI, asistente de Agua ELIM.")
        knowledge = _cargar_knowledge(config.get("knowledge_files", []))
        _system_prompt_cache = f"{reglas}\n\n# BASE DE CONOCIMIENTO\n\n{knowledge}" if knowledge else reglas
        logger.info(f"System prompt cargado ({len(_system_prompt_cache):,} caracteres)")
    return _system_prompt_cache


def obtener_mensaje_error() -> str:
    return cargar_config_prompts().get(
        "error_message",
        "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.",
    )


def obtener_mensaje_fallback() -> str:
    return cargar_config_prompts().get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


def _contexto_dinamico(telefono: str, nombre_perfil: str) -> str:
    """Datos que cambian en cada mensaje (van al final para no romper la caché)."""
    ahora = datetime.now(ZONA_HORARIA)
    return (
        f"Contexto de esta conversación (no lo menciones salvo que sea útil):\n"
        f"- Fecha y hora actual (hora central): {ahora:%A %Y-%m-%d %H:%M}\n"
        f"- WhatsApp del cliente: {telefono}\n"
        f"- Nombre de perfil de WhatsApp: {nombre_perfil or 'desconocido'}"
    )


# ── Generación de respuesta ──────────────────────────────────────────────────

async def generar_respuesta(
    mensaje: str,
    historial: list[dict],
    telefono: str,
    nombre_perfil: str = "",
) -> tuple[str, int, int]:
    """
    Genera la respuesta de ELI. Ejecuta herramientas si el modelo las pide.

    Returns: (texto_respuesta, tokens_entrada, tokens_salida)
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback(), 0, 0

    from agent.tools import TOOLS_DEFINITION, ejecutar_herramienta

    mensajes = [{"role": "system", "content": cargar_system_prompt()}]
    mensajes += [{"role": m["role"], "content": m["content"]} for m in historial]
    mensajes.append({"role": "system", "content": _contexto_dinamico(telefono, nombre_perfil)})
    mensajes.append({"role": "user", "content": mensaje})

    contexto = {"telefono": telefono, "nombre_perfil": nombre_perfil}
    extra = {}
    # El parámetro reasoning_effort solo aplica a modelos de razonamiento (gpt-5*, o*)
    if ESFUERZO and MODELO.startswith(("gpt-5", "o")):
        extra["reasoning_effort"] = ESFUERZO

    tokens_in = tokens_out = 0
    try:
        for _ in range(MAX_ITERACIONES):
            response = await client.chat.completions.create(
                model=MODELO,
                messages=mensajes,
                tools=TOOLS_DEFINITION,
                max_completion_tokens=2000,
                **extra,
            )
            if response.usage:
                tokens_in += response.usage.prompt_tokens
                tokens_out += response.usage.completion_tokens

            eleccion = response.choices[0].message

            if not eleccion.tool_calls:
                texto = (eleccion.content or "").strip()
                logger.info(f"Respuesta generada ({tokens_in} in / {tokens_out} out)")
                return texto or obtener_mensaje_fallback(), tokens_in, tokens_out

            # El modelo pidió herramientas: ejecutarlas y devolverle el resultado
            mensajes.append({
                "role": "assistant",
                "content": eleccion.content or "",
                "tool_calls": [tc.model_dump() for tc in eleccion.tool_calls],
            })
            for tc in eleccion.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                resultado = await ejecutar_herramienta(tc.function.name, args, contexto)
                mensajes.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(resultado, ensure_ascii=False),
                })

        logger.error("Se alcanzó el límite de iteraciones de herramientas")
        return obtener_mensaje_error(), tokens_in, tokens_out

    except Exception as e:
        logger.error(f"Error OpenAI API: {e}")
        return obtener_mensaje_error(), tokens_in, tokens_out
