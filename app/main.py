from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI, HTTPException

from app.db.connection import close_pool, get_db_connection, init_pool
from app.mcp_server.server import mcp

# Convertimos el servidor MCP en una aplicacion ASGI montable dentro de
# FastAPI. path="/" hace que responda en la raiz de donde lo montemos
# (mas abajo lo montamos en "/mcp", asi que quedara disponible en "/mcp/").
mcp_app = mcp.http_app(path="/")


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    """
    Ciclo de vida combinado de la aplicacion.

    FastAPI solo acepta UN lifespan. Aqui anidamos el de fastmcp (necesario
    para que las tools de MCP funcionen) dentro de nuestro propio lifespan,
    de forma que en el futuro podamos anadir aqui mismo la apertura/cierre
    de recursos propios (por ejemplo, un pool de conexiones a Postgres) sin
    perder la inicializacion de MCP.
    """
    # "async with" entra en el lifespan de mcp_app: esto ejecuta el
    # arranque interno de MCP antes de seguir.
    async with mcp_app.lifespan(app):
        # Codigo de arranque propio: abrimos el pool de conexiones a
        # Postgres UNA vez, antes de que el servidor empiece a atender
        # peticiones. init_pool() es una funcion sincrona normal (no usa
        # await) porque psycopg2 tambien es sincrono.
        init_pool()

        # "yield" es el punto donde la aplicacion queda arrancada y
        # sirviendo peticiones. Todo lo de antes es "startup", todo lo
        # de despues (tras que alguien apague el servidor) es "shutdown".
        yield

        # Codigo de cierre propio: cerramos todas las conexiones del
        # pool de forma ordenada antes de que termine de apagarse MCP.
        close_pool()
    # Al salir del "async with", se ejecuta el cierre interno de MCP.


# Creamos la aplicacion de FastAPI usando nuestro lifespan combinado en vez
# de pasar mcp_app.lifespan directamente.
app = FastAPI(lifespan=combined_lifespan)

# Montamos el sub-app de MCP bajo el prefijo "/mcp": cualquier peticion a
# una URL que empiece por "/mcp" se la pasamos a mcp_app en vez de
# manejarla nosotros mismos.
app.mount("/mcp", mcp_app)


@app.get("/health")
def health():
    """Endpoint minimo para comprobar que el servidor esta vivo."""
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    """
    Comprueba que el pool de conexiones puede hablar de verdad con
    Postgres, ejecutando una consulta real (no solo abriendo/cerrando
    la conexion).

    Es una funcion sincrona normal (def, no async def): FastAPI la
    ejecuta en su threadpool interno, que es justo el escenario para el
    que preparamos ThreadedConnectionPool en connection.py.
    """
    try:
        # get_db_connection() es nuestro context manager: al entrar en
        # el "with" nos presta una conexion del pool, y al salir (pase
        # lo que pase dentro) la devuelve sola gracias a su finally.
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM clientes")
            # fetchone() devuelve una tupla con una sola fila, ej. (0,).
            # [0] saca el primer (y unico) valor de esa tupla.
            clientes_count = cursor.fetchone()[0]
            cursor.close()
    except psycopg2.Error as error:
        # psycopg2.Error es la clase base de la que heredan todos los
        # errores de psycopg2 (fallo de conexion, SQL invalido, etc.).
        # La capturamos aqui para no dejar que una excepcion cruda tire
        # abajo la peticion sin explicacion: en su lugar, respondemos
        # con un error HTTP 500 (error del servidor) y un mensaje claro.
        raise HTTPException(
            status_code=500,
            detail=f"Error de base de datos: {error}",
        )

    return {"status": "ok", "clientes_count": clientes_count}
