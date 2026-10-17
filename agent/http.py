# agent/http.py — Cliente HTTP compartido
# Usa el almacén de certificados del sistema operativo (truststore), así funciona
# igual en Windows (antivirus/proxy con certificados propios) y en el VPS Linux,
# sin desactivar nunca la verificación SSL.

import ssl
import httpx
import truststore

_ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def nuevo_cliente(timeout: float = 15.0) -> httpx.AsyncClient:
    """Crea un httpx.AsyncClient con verificación SSL del sistema."""
    return httpx.AsyncClient(verify=_ssl_context, timeout=timeout)
