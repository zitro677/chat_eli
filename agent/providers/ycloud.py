# agent/providers/ycloud.py — Adaptador para YCloud WhatsApp Business API
# Generado por AgentKit — formato verificado contra la spec oficial de webhooks de YCloud (v2)

import os
import hmac
import time
import hashlib
import logging
from fastapi import Request, HTTPException
from agent.http import nuevo_cliente
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

YCLOUD_API_BASE = "https://api.ycloud.com/v2"

# Descripción que recibe el modelo cuando el cliente envía algo que no es texto
DESCRIPCION_MULTIMEDIA = {
    "image": "[El cliente envió una imagen]",
    "document": "[El cliente envió un documento]",
    "audio": "[El cliente envió una nota de voz que no puedes escuchar]",
    "video": "[El cliente envió un video que no puedes ver]",
    "sticker": "[El cliente envió un sticker]",
    "location": "[El cliente compartió una ubicación]",
}

# Antigüedad máxima aceptada para la marca de tiempo de la firma (evita reenvíos viejos)
TOLERANCIA_FIRMA_SEG = 300


class ProveedorYCloud(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando YCloud Business API (soporta coexistencia)."""

    def __init__(self):
        self.api_key = os.getenv("YCLOUD_API_KEY")
        self.phone_number = os.getenv("YCLOUD_PHONE_NUMBER")
        self.webhook_secret = os.getenv("YCLOUD_WEBHOOK_SECRET", "")
        if not self.webhook_secret:
            logger.warning("YCLOUD_WEBHOOK_SECRET vacío — la firma de los webhooks NO se verifica")

    def _verificar_firma(self, raw: bytes, cabecera: str) -> bool:
        """Valida YCloud-Signature: 't=<timestamp>,s=<hmac_sha256_hex>' calculado sobre '<t>.<body>'."""
        partes = dict(p.split("=", 1) for p in cabecera.split(",") if "=" in p)
        ts, firma = partes.get("t", ""), partes.get("s", "")
        if not ts.isdigit() or not firma:
            return False
        if abs(time.time() - int(ts)) > TOLERANCIA_FIRMA_SEG:
            return False
        esperado = hmac.new(
            self.webhook_secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(esperado, firma)

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea eventos de YCloud: mensajes de clientes y ecos de la app WhatsApp Business."""
        raw = await request.body()

        if self.webhook_secret and not self._verificar_firma(raw, request.headers.get("YCloud-Signature", "")):
            logger.warning("Webhook YCloud con firma inválida — descartado")
            raise HTTPException(status_code=401, detail="Firma inválida")

        try:
            body = await request.json()
        except Exception:
            return []

        evento_id = body.get("id", "")
        tipo_evento = body.get("type", "")

        # Mensaje de un cliente
        if tipo_evento == "whatsapp.inbound_message.received":
            msg = body.get("whatsappInboundMessage") or {}
            tipo = msg.get("type", "")
            if tipo == "text":
                texto = (msg.get("text") or {}).get("body", "")
            elif tipo == "button":
                texto = (msg.get("button") or {}).get("text", "")
            elif tipo == "interactive":
                inter = msg.get("interactive") or {}
                respuesta = inter.get("button_reply") or inter.get("list_reply") or {}
                texto = respuesta.get("title", "")
            else:
                texto = DESCRIPCION_MULTIMEDIA.get(tipo, "")
                caption = (msg.get(tipo) or {}).get("caption", "") if tipo in ("image", "document", "video") else ""
                if texto and caption:
                    texto += f' con el texto: "{caption}"'
            if not texto:
                return []
            audio = (msg.get("audio") or {}) if tipo == "audio" else {}
            return [MensajeEntrante(
                telefono=msg.get("from", ""),
                texto=texto,
                mensaje_id=msg.get("wamid") or msg.get("id") or evento_id,
                es_propio=False,
                tipo=tipo or "otro",
                nombre=(msg.get("customerProfile") or {}).get("name", ""),
                media_url=audio.get("link", ""),
                media_mime=audio.get("mime_type", ""),
            )]

        # Mensaje escrito por el equipo desde la app WhatsApp Business (coexistencia)
        if tipo_evento == "whatsapp.smb.message.echoes":
            msg = body.get("whatsappMessage") or {}
            texto = (msg.get("text") or {}).get("body", "") or "[El asesor envió un archivo]"
            return [MensajeEntrante(
                telefono=msg.get("to", ""),
                texto=texto,
                mensaje_id=msg.get("wamid") or msg.get("id") or evento_id,
                es_propio=False,
                tipo=msg.get("type", "text"),
                es_eco_humano=True,
            )]

        # Otros eventos (estados de entrega, historial, etc.) no requieren acción
        return []

    async def descargar_media(self, url: str) -> bytes | None:
        """Descarga la nota de voz del cliente (el enlace de YCloud requiere X-API-Key)."""
        try:
            async with nuevo_cliente(timeout=30.0) as client:
                r = await client.get(url, headers={"X-API-Key": self.api_key or ""}, follow_redirects=True)
                if r.status_code != 200:
                    logger.error(f"Error descargando audio YCloud: {r.status_code} — {r.text[:200]}")
                    return None
                return r.content
        except Exception as e:
            logger.error(f"Excepción al descargar audio YCloud: {e}")
            return None

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via YCloud (endpoint síncrono sendDirectly)."""
        return await self._enviar(telefono, {"type": "text", "text": {"body": mensaje}})

    async def enviar_audio(self, telefono: str, url_audio: str) -> bool:
        """Envía una nota de voz; YCloud la descarga desde url_audio."""
        return await self._enviar(telefono, {"type": "audio", "audio": {"link": url_audio}})

    async def _enviar(self, telefono: str, contenido: dict) -> bool:
        if not self.api_key or not self.phone_number:
            logger.warning("YCLOUD_API_KEY o YCLOUD_PHONE_NUMBER no configurados — mensaje no enviado")
            return False

        url = f"{YCLOUD_API_BASE}/whatsapp/messages/sendDirectly"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        payload = {"from": self.phone_number, "to": telefono, **contenido}

        try:
            async with nuevo_cliente(timeout=15.0) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code not in (200, 201):
                    logger.error(f"Error YCloud API: {r.status_code} — {r.text}")
                return r.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Excepción al enviar mensaje YCloud: {e}")
            return False
