# agent/providers/__init__.py — Factory de proveedores
# Generado por AgentKit

import os
from agent.providers.base import ProveedorWhatsApp


def obtener_proveedor() -> ProveedorWhatsApp:
    """Retorna el proveedor de WhatsApp configurado en .env."""
    proveedor = os.getenv("WHATSAPP_PROVIDER", "").lower()

    if not proveedor:
        raise ValueError("WHATSAPP_PROVIDER no configurado en .env. Usa: ycloud | meta")

    if proveedor == "ycloud":
        from agent.providers.ycloud import ProveedorYCloud
        return ProveedorYCloud()
    elif proveedor == "meta":
        # Se agregará cuando ELIM migre a Meta Cloud API directo (Tech Provider)
        from agent.providers.meta import ProveedorMeta
        return ProveedorMeta()
    else:
        raise ValueError(f"Proveedor no soportado: {proveedor}. Usa: ycloud | meta")
