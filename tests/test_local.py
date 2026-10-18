# tests/test_local.py — Simulador de chat en terminal
# Generado por AgentKit
#
# Prueba a ELI sin WhatsApp. Las herramientas sí se ejecutan: si MAKE_WEBHOOK_URL
# está configurado, los leads de prueba llegarán a la Google Sheet.

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta, inicializar_rag
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial, bot_pausado, reanudar_bot,
)

TELEFONO_TEST = "+10000000000"


async def main():
    await inicializar_db()
    await inicializar_rag()

    print()
    print("=" * 55)
    print("   ELI — Agua ELIM (Test Local)")
    print("=" * 55)
    print()
    print("  Escribe mensajes como si fueras un cliente.")
    print("  Comandos:")
    print("    'limpiar'  — borra el historial y la pausa")
    print("    'imagen'   — simula que el cliente envía una foto")
    print("    'salir'    — termina el test")
    print()
    print("-" * 55)
    print()

    while True:
        try:
            mensaje = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue
        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break
        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            await reanudar_bot(TELEFONO_TEST)
            print("[Historial y pausa borrados]\n")
            continue
        if mensaje.lower() == "imagen":
            mensaje = "[El cliente envió una imagen]"

        if await bot_pausado(TELEFONO_TEST):
            print("[Bot en pausa: una persona atendería este chat. Escribe 'limpiar' para reanudar]\n")
            continue

        historial = await obtener_historial(TELEFONO_TEST)

        print("\nELI: ", end="", flush=True)
        respuesta, tokens_in, tokens_out = await generar_respuesta(
            mensaje, historial, telefono=TELEFONO_TEST, nombre_perfil="Cliente de prueba"
        )
        print(respuesta)
        print(f"  [tokens: {tokens_in} in / {tokens_out} out]\n")

        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
