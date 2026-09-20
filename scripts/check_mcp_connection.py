"""
Script de verificación (no forma parte del backend final).

Comprueba que el servidor MCP montado en /mcp de nuestra app FastAPI
responde de verdad al protocolo MCP: se conecta como un cliente real y
pide la lista de tools disponibles.

Requiere el servidor arrancado aparte en el puerto 8000 (uvicorn
app.main:app, SIN --reload).

Cambios del bloque calculate-estimate (2026-09-19):
  - /mcp exige ahora el token MCP_SECRET. Sin él, este script recibiría
    un 401, así que lo lee del .env y lo envía como "Bearer".
  - Ya no se espera una lista vacía: debe aparecer la tool
    calculate_estimate.
"""

import asyncio
import sys
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

from fastmcp import Client

# StreamableHttpTransport es el "cable" HTTP que usa Client por debajo.
# Hasta ahora se creaba solo al pasarle una URL a Client. Se crea a mano
# porque es el que acepta el parámetro auth=: con un texto, lo envía como
# cabecera "Authorization: Bearer <texto>" en cada petición.
from fastmcp.client.transports import StreamableHttpTransport

from app.config import MCP_SECRET


async def comprobar_conexion_mcp():
    """
    Se conecta al servidor MCP en localhost y pide la lista de tools.

    Usamos "async with" porque Client abre una conexión de red (un
    recurso que hay que cerrar después de usarlo); el "async with" se
    encarga de cerrarla automáticamente al salir del bloque, incluso si
    algo falla dentro.
    """
    transporte = StreamableHttpTransport("http://localhost:8000/mcp/", auth=MCP_SECRET)
    async with Client(transporte) as client:
        # list_tools() es una llamada al protocolo MCP: le pregunta al
        # servidor qué tools tiene registradas. "await" pausa esta
        # función hasta que la respuesta llega por la red, sin bloquear
        # el resto del programa mientras espera.
        tools = await client.list_tools()
        print("Conexión MCP establecida correctamente (con token).")
        print(f"Tools encontradas: {[t.name for t in tools]}")


if __name__ == "__main__":
    # asyncio.run() es el punto de entrada que arranca el "bucle de
    # eventos" (event loop) necesario para ejecutar código async. Todo
    # código con "await" tiene que correr dentro de un event loop.
    asyncio.run(comprobar_conexion_mcp())
