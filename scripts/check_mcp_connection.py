"""
Script temporal de verificacion (no forma parte del backend final).

Comprueba que el servidor MCP montado en /mcp de nuestra app FastAPI
responde de verdad al protocolo MCP: se conecta como un cliente real y
pide la lista de tools disponibles. Como todavia no hemos definido
ninguna tool, se espera una lista vacia, sin ningun error.
"""

import asyncio

from fastmcp import Client


async def comprobar_conexion_mcp():
    """
    Se conecta al servidor MCP en localhost y pide la lista de tools.

    Usamos "async with" porque Client abre una conexion de red (un
    recurso que hay que cerrar despues de usarlo); el "async with" se
    encarga de cerrarla automaticamente al salir del bloque, incluso si
    algo falla dentro.
    """
    async with Client("http://localhost:8000/mcp/") as client:
        # list_tools() es una llamada al protocolo MCP: le pregunta al
        # servidor que tools tiene registradas. "await" pausa esta
        # funcion hasta que la respuesta llega por la red, sin bloquear
        # el resto del programa mientras espera.
        tools = await client.list_tools()
        print(f"Conexion MCP establecida correctamente.")
        print(f"Tools encontradas: {tools}")


if __name__ == "__main__":
    # asyncio.run() es el punto de entrada que arranca el "bucle de
    # eventos" (event loop) necesario para ejecutar codigo async. Todo
    # codigo con "await" tiene que correr dentro de un event loop.
    asyncio.run(comprobar_conexion_mcp())
