import sys
from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import Json

from app.api import estimates as estimates_api
from app.api import leads as leads_api
from app.db.connection import (
    close_pool,
    get_db_connection,
    get_transactional_connection,
    init_pool,
)
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

# Enganchamos el router de leads a la aplicacion principal. A partir de
# aqui, POST /leads existe y lo atiende la funcion definida en
# app/api/leads.py. Es el PRIMER include_router del proyecto: los
# endpoints /health y /health/db de mas abajo se definieron directamente
# sobre "app" porque son dos funciones sueltas de infraestructura, no un
# dominio de negocio con su propio archivo.
app.include_router(leads_api.router)

# Segundo router: POST /calculate-estimate (app/api/estimates.py). Es la
# puerta REST del cálculo de presupuestos. La puerta MCP del mismo
# cálculo es la tool calculate_estimate de app/mcp_server/server.py, ya
# montada más arriba bajo /mcp. Las dos llaman a la misma función de
# app/services/estimate_service.py.
app.include_router(estimates_api.router)


# Valores con los que se registra un error que NO pertenece a ninguna
# entidad concreta del negocio. La tabla logs es polimorfica: cada fila
# dice a que tipo de entidad se refiere (entity_type) y a cual en
# concreto (entity_id). Pero un fallo no controlado puede ocurrir antes
# de que exista ninguna entidad, y entity_id es NOT NULL, asi que hace
# falta un valor centinela. Se usa 0 porque las secuencias SERIAL de
# Postgres empiezan en 1: ningun registro real puede tener id 0, asi que
# no hay riesgo de confundirlo con una entidad de verdad.
LOG_ENTITY_TYPE_SISTEMA = "sistema"
LOG_ENTITY_ID_SIN_ENTIDAD = 0


@app.exception_handler(Exception)
def manejador_error_no_controlado(request: Request, exc: Exception):
    """
    Red de seguridad para CUALQUIER error no controlado de CUALQUIER
    endpoint.

    Un decorador @app.exception_handler(Exception) le dice a FastAPI:
    "si un endpoint lanza una excepcion que nadie ha capturado, en vez
    de responder tu error generico, llama a esta funcion".

    Importante: esto NO afecta a los errores de validacion. Los 422 que
    genera Pydantic y los HTTPException que lanzamos a proposito tienen
    sus propios manejadores dentro de FastAPI y siguen funcionando
    exactamente igual. Aqui solo cae lo IMPREVISTO: un fallo de red
    contra Postgres, un error de programacion en services/, una division
    por cero.

    Hace dos cosas:

      1. Deja rastro. Escribe una fila en la tabla logs con el mensaje
         real de la excepcion, para que el fallo siga existiendo despues
         de que el servidor se apague. Sin esto, el unico rastro seria
         la consola, que se pierde.

      2. Responde sin filtrar informacion. Devuelve un mensaje generico
         con codigo 500. NUNCA envia el traceback al cliente: un
         traceback revela rutas de archivos, nombres de tablas y
         estructura interna del sistema — informacion util para alguien
         que quisiera atacarlo.

    Es una funcion sincrona normal (def, no async def) a proposito:
    Starlette despacha los manejadores sincronos a su pool de hilos, que
    es donde debe ocurrir una escritura bloqueante con psycopg2. Si
    fuera async def, el INSERT bloquearia el bucle de eventos y frenaria
    todas las demas peticiones mientras dura.
    """
    # Se guarda el tipo de excepcion ademas del mensaje: "division by
    # zero" a secas es mucho menos util que saber que fue un
    # ZeroDivisionError. Y la ruta y el metodo, para saber que peticion
    # lo provoco.
    detalle = {
        "tipo": type(exc).__name__,
        "mensaje": str(exc),
        "ruta": request.url.path,
        "metodo": request.method,
    }

    # TODO el registro va dentro de su propio try/except. Motivo: si la
    # base de datos es justamente lo que esta fallando, el INSERT de
    # aqui tambien fallaria, y esa segunda excepcion se lanzaria DESDE
    # el manejador de excepciones — dejando al cliente sin ninguna
    # respuesta y ocultando el error original. Registrar es deseable;
    # responder es obligatorio.
    try:
        with get_transactional_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO logs (entity_type, entity_id, accion, detalle)
                VALUES (%s, %s, %s, %s);
                """,
                (
                    LOG_ENTITY_TYPE_SISTEMA,
                    LOG_ENTITY_ID_SIN_ENTIDAD,
                    "error_no_controlado",
                    Json(detalle),
                ),
            )
            cursor.close()
    except Exception as error_registro:
        # Si el registro en la base de datos falla, NO se relanza la
        # excepcion: lo importante es que el cliente reciba igualmente su
        # 500. Pero tampoco puede quedarse en silencio.
        #
        # El caso que esto cubre es el peor posible: si Postgres esta
        # caido, el error original no se puede guardar en logs
        # (precisamente porque la base de datos es lo que falla) y, sin
        # esta salida por consola, desapareceria sin dejar rastro en
        # ningun sitio. El fallo se volveria invisible justo cuando mas
        # falta hace verlo.
        #
        # Se escribe en stderr y no en stdout porque es el canal
        # convencional para errores: uvicorn, Docker, systemd y los
        # servicios de logs lo recogen por separado de la salida normal,
        # asi que un error no queda sepultado entre las lineas de acceso
        # HTTP.
        #
        # flush=True fuerza a que el mensaje salga en el momento, sin
        # esperar a que se llene el bufer de salida. Sin esto, si el
        # proceso muriera justo despues, el mensaje se perderia dentro
        # de ese bufer sin llegar nunca a escribirse.
        print(
            "[ERROR] No se pudo registrar en la tabla logs un error no controlado.\n"
            f"        Error original    : {detalle['tipo']}: {detalle['mensaje']}\n"
            f"        Peticion          : {detalle['metodo']} {detalle['ruta']}\n"
            f"        Fallo al registrar: "
            f"{type(error_registro).__name__}: {error_registro}",
            file=sys.stderr,
            flush=True,
        )

    # JSONResponse construye la respuesta HTTP a mano. Se usa en vez de
    # un simple "return {...}" porque los manejadores de excepciones no
    # pasan por la maquinaria normal de FastAPI que convierte un
    # diccionario en respuesta.
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Error interno"},
    )


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
