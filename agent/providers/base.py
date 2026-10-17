# agent/providers/base.py — Clase base para proveedores de WhatsApp
# Generado por AgentKit

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MensajeEntrante:
    """Mensaje normalizado — mismo formato sin importar el proveedor."""
    telefono: str               # Número del cliente (E.164)
    texto: str                  # Contenido del mensaje (o descripción si es multimedia)
    mensaje_id: str             # ID único del mensaje (para descartar duplicados)
    es_propio: bool             # True si lo envió el propio agente (se ignora)
    tipo: str = "text"          # text | image | document | audio | video | sticker | otro
    nombre: str = ""            # Nombre del perfil de WhatsApp del cliente, si viene
    es_eco_humano: bool = False # True si lo escribió una persona del equipo desde la app WhatsApp Business


class ProveedorWhatsApp(ABC):
    """Interfaz que cada proveedor de WhatsApp debe implementar."""

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Extrae y normaliza mensajes del payload del webhook."""
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de texto. Retorna True si fue exitoso."""
        ...

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (solo Meta la requiere). Retorna respuesta o None."""
        return None
