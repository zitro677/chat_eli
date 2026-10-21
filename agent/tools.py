# agent/tools.py — Herramientas del agente ELI
# Generado por AgentKit
#
# Las dos herramientas envían eventos al escenario de Make (MAKE_WEBHOOK_URL):
#   evento="lead"          → Make agrega una fila a la Google Sheet de leads
#   evento="transferencia" → Make avisa al equipo que un cliente necesita a una persona
#
# Transferir NO pausa el bot: ELI sigue respondiendo mientras el equipo contacta al cliente.

import os
import logging
from datetime import datetime, timezone
from agent.http import nuevo_cliente

logger = logging.getLogger("agentkit")


# ── Definición de herramientas para OpenAI (function calling) ────────────────

TOOLS_DEFINITION = [
    {
        "type": "function",
        "function": {
            "name": "registrar_lead",
            "description": (
                "Guarda los datos de contacto de un cliente interesado en la hoja de leads de ELIM. "
                "Llámala SOLO cuando el cliente ya aceptó explícitamente que guardemos sus datos y "
                "tienes al menos su nombre y país. Nunca incluyas diagnósticos ni condiciones de salud."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre del cliente"},
                    "correo": {"type": "string", "description": "Correo electrónico (opcional, vacío si no lo dio)"},
                    "pais": {"type": "string", "description": "Colombia o Estados Unidos (u otro país que indique)"},
                    "horario": {"type": "string", "description": "Horario preferido de contacto, o día y hora deseados si pidió una cita (vacío si no lo dio)"},
                },
                "required": ["nombre", "pais"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transferir_a_humano",
            "description": (
                "Avisa al equipo de ELIM para que una persona contacte al cliente. Úsala cuando: el cliente "
                "pide una cita o hablar con una persona; envía un comprobante de pago; quiere pagar por PayPal "
                "y necesita ayuda; hay un reclamo, un pedido por confirmar, o una situación de salud delicada. "
                "ANTES de llamarla (salvo comprobante de pago o seguridad), pide los datos y llama a "
                "registrar_lead: el equipo necesita saber a quién contactar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "motivo": {
                        "type": "string",
                        "enum": ["cita", "pide_humano", "comprobante_pago", "pedido", "reclamo", "salud_delicada", "otro"],
                    },
                    "resumen": {
                        "type": "string",
                        "description": "Resumen breve (1-2 frases) de lo que necesita el cliente, sin datos médicos",
                    },
                },
                "required": ["motivo", "resumen"],
            },
        },
    },
]


# ── Envío a Make ─────────────────────────────────────────────────────────────

async def _enviar_a_make(payload: dict) -> bool:
    """POST JSON al webhook de Make. Retorna True si Make lo aceptó."""
    url = os.getenv("MAKE_WEBHOOK_URL", "")
    if not url:
        logger.warning(f"MAKE_WEBHOOK_URL no configurado — evento '{payload.get('evento')}' no enviado")
        return False
    try:
        async with nuevo_cliente(timeout=15.0) as client:
            r = await client.post(url, json=payload)
            if r.status_code >= 300:
                logger.error(f"Make respondió {r.status_code}: {r.text[:200]}")
            return r.status_code < 300
    except Exception as e:
        logger.error(f"Error enviando a Make: {e}")
        return False


def _fecha() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def registrar_lead(telefono: str, nombre: str, pais: str, correo: str = "", horario: str = "") -> dict:
    ok = await _enviar_a_make({
        "evento": "lead",
        "nombre": nombre,
        "telefono": telefono,
        "correo": correo,
        "pais": pais,
        "horario": horario,
        "fecha": _fecha(),
    })
    return {"ok": ok, "mensaje": "Lead registrado" if ok else "No se pudo registrar el lead; continúa la conversación normalmente"}


async def transferir_a_humano(telefono: str, motivo: str, resumen: str, nombre_perfil: str = "") -> dict:
    ok = await _enviar_a_make({
        "evento": "transferencia",
        "nombre": nombre_perfil,
        "telefono": telefono,
        "motivo": motivo,
        "resumen": resumen,
        "fecha": _fecha(),
    })
    return {"ok": ok, "mensaje": "Equipo notificado; dile al cliente que una persona lo contactará pronto y que puedes seguir ayudándole mientras tanto"}


# ── Despachador ──────────────────────────────────────────────────────────────

async def ejecutar_herramienta(nombre: str, argumentos: dict, contexto: dict) -> dict:
    """Ejecuta la herramienta pedida por el modelo. `contexto` trae telefono y nombre_perfil."""
    logger.info(f"Herramienta: {nombre}({list(argumentos.keys())})")
    try:
        if nombre == "registrar_lead":
            return await registrar_lead(telefono=contexto["telefono"], **argumentos)
        if nombre == "transferir_a_humano":
            return await transferir_a_humano(
                telefono=contexto["telefono"], nombre_perfil=contexto.get("nombre_perfil", ""), **argumentos
            )
        return {"ok": False, "error": f"Herramienta desconocida: {nombre}"}
    except Exception as e:
        logger.error(f"Herramienta {nombre} falló: {e}")
        return {"ok": False, "error": str(e)}
