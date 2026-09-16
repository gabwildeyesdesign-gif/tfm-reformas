"""
Script temporal de verificacion (no forma parte del backend final).

Comprueba si el APAGADO ORDENADO del servidor se completa o se cuelga,
y en que fase: antes de close_pool(), dentro de close_pool(), o despues
(en el cierre interno de fastmcp).

Por que no lanzamos uvicorn por consola y le mandamos Ctrl+C:
en Windows, Ctrl+C es un "evento de consola" que solo llega a procesos
que comparten una ventana de consola real. Si el proceso se lanza sin
consola, el evento se pierde y la prueba fallaria por un motivo ajeno a
nuestro codigo. En su lugar, arrancamos uvicorn desde Python y ponemos
server.should_exit = True, que es exactamente lo que hace el manejador
de senales de uvicorn (Server.handle_exit) cuando recibe Ctrl+C: el
apagado recorre el mismo camino (lifespan -> close_pool -> cierre MCP).
"""

import asyncio
import faulthandler
import sys
import time
import urllib.request
from pathlib import Path

import uvicorn

# Este script vive en scripts/, pero el paquete "app" esta en la raiz del
# repositorio. Al ejecutar "python scripts/check_graceful_shutdown.py",
# Python solo anade la carpeta del archivo (scripts/) a su lista de rutas
# donde buscar modulos (sys.path), asi que "import app.main" fallaria con
# ModuleNotFoundError. Lo arreglamos calculando la raiz a partir de la
# ubicacion de este mismo archivo:
#   __file__            -> ruta de este archivo
#   .resolve()          -> la convierte en ruta absoluta
#   .parents[1]         -> sube dos niveles: scripts/ y luego la raiz
# insert(0, ...) la pone la PRIMERA de la lista, la que Python consulta
# antes que ninguna otra. Usamos pathlib en vez de pegar textos con "/"
# para que funcione igual en Windows y en Linux.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))

# Importamos el MODULO app.main (no solo "app") para poder sustituir la
# referencia a close_pool que usa combined_lifespan. Importar este modulo
# no abre todavia el pool: init_pool() solo se ejecuta al arrancar el
# lifespan, cuando uvicorn arranque el servidor.
import app.main as app_main
import app.db.connection as db_connection

URL_BASE = "http://127.0.0.1:8000"

# Segundos maximos que damos al apagado antes de considerarlo colgado.
LIMITE_APAGADO = 20

# Instante de referencia: todas las marcas de tiempo se imprimen como
# segundos transcurridos desde aqui, para ver la duracion de cada fase.
INICIO = time.perf_counter()


def marca(texto):
    """Imprime un mensaje precedido de los segundos transcurridos."""
    # flush=True fuerza a que el texto salga por pantalla al momento, en
    # vez de quedarse en un buffer: si el proceso se colgara, veriamos
    # hasta el ultimo mensaje realmente alcanzado.
    print(f"[{time.perf_counter() - INICIO:7.3f}s] {texto}", flush=True)


# Guardamos la close_pool original antes de sustituirla.
close_pool_original = app_main.close_pool

# Aqui dejaremos las conexiones que tenia el pool justo antes de cerrarse,
# para comprobar despues que de verdad quedaron cerradas.
conexiones_antes_de_cerrar = []


def close_pool_instrumentada():
    """
    Envoltorio de close_pool() que mide su duracion y guarda el estado
    del pool justo antes de cerrarlo.

    close_pool() pone db_connection._pool a None al terminar, asi que
    tenemos que mirar dentro del pool ANTES de llamar a la original.
    """
    pool = db_connection._pool
    # En psycopg2, un pool guarda las conexiones libres en la lista
    # _pool y las prestadas en el diccionario _used. Son atributos
    # internos (empiezan por "_"): aceptable en un script de diagnostico,
    # nunca en el codigo del backend.
    libres = list(pool._pool)
    prestadas = list(pool._used.values())
    conexiones_antes_de_cerrar.extend(libres + prestadas)
    marca(
        f"close_pool(): ENTRA  (libres={len(libres)}, "
        f"prestadas={len(prestadas)})"
    )
    close_pool_original()
    marca("close_pool(): SALE")


# Sustituimos el nombre close_pool DENTRO del modulo app.main. Funciona
# porque combined_lifespan busca el nombre close_pool en su modulo en el
# momento de llamarlo, no cuando se definio la funcion.
app_main.close_pool = close_pool_instrumentada


def pedir(ruta):
    """Hace una peticion GET bloqueante y devuelve (codigo, cuerpo)."""
    with urllib.request.urlopen(URL_BASE + ruta, timeout=10) as respuesta:
        return respuesta.status, respuesta.read().decode()


async def prueba():
    """
    Arranca el servidor, lo usa, simula Ctrl+C y mide el apagado.

    Devuelve True si el apagado termino dentro del limite y todas las
    conexiones del pool quedaron cerradas; False en caso contrario.
    """
    config = uvicorn.Config(
        app_main.app, host="127.0.0.1", port=8000, log_level="info"
    )
    servidor = uvicorn.Server(config)

    # create_task lanza servidor.serve() "en paralelo" dentro del mismo
    # bucle de eventos: el servidor corre mientras esta funcion sigue.
    tarea_servidor = asyncio.create_task(servidor.serve())

    # servidor.started pasa a True cuando uvicorn termina de arrancar
    # (lifespan incluido, es decir, despues de init_pool()).
    while not servidor.started:
        if tarea_servidor.done():
            # Si el servidor murio durante el arranque, result() relanza
            # la excepcion original para que veamos el error real.
            tarea_servidor.result()
        await asyncio.sleep(0.1)
    marca("Servidor arrancado")

    # urllib es bloqueante; asyncio.to_thread lo ejecuta en otro hilo para
    # no congelar el bucle de eventos, que tiene que seguir atendiendo
    # la propia peticion en el servidor.
    for ruta in ("/health", "/health/db"):
        codigo, cuerpo = await asyncio.to_thread(pedir, ruta)
        marca(f"GET {ruta} -> {codigo} {cuerpo}")

    # Vigilante: si en LIMITE_APAGADO segundos no hemos cancelado esto,
    # Python imprime por stderr la pila de TODOS los hilos (donde esta
    # parado cada uno). exit=False: solo imprime, no mata el proceso.
    faulthandler.dump_traceback_later(LIMITE_APAGADO, exit=False)

    marca("Simulando Ctrl+C (servidor.should_exit = True)")
    inicio_apagado = time.perf_counter()
    servidor.should_exit = True

    try:
        # wait_for espera a que serve() termine, pero como mucho
        # LIMITE_APAGADO + 5 segundos (margen para que el vigilante
        # imprima antes).
        await asyncio.wait_for(tarea_servidor, timeout=LIMITE_APAGADO + 5)
    except asyncio.TimeoutError:
        marca("APAGADO COLGADO: serve() no termino a tiempo")
        return False
    finally:
        faulthandler.cancel_dump_traceback_later()

    marca(f"Apagado completo en {time.perf_counter() - inicio_apagado:.3f}s")

    # conn.closed vale 0 si la conexion sigue abierta y distinto de 0 si
    # esta cerrada (atributo publico de psycopg2).
    abiertas = [c for c in conexiones_antes_de_cerrar if c.closed == 0]
    marca(
        f"Conexiones del pool comprobadas: {len(conexiones_antes_de_cerrar)}, "
        f"siguen abiertas: {len(abiertas)}"
    )
    marca(f"db_connection._pool tras el apagado: {db_connection._pool}")

    return bool(conexiones_antes_de_cerrar) and not abiertas


if __name__ == "__main__":
    exito = asyncio.run(prueba())
    marca("RESULTADO: OK" if exito else "RESULTADO: FALLO")
    # Codigo de salida 0 = exito, 1 = fallo (convencion de terminal).
    sys.exit(0 if exito else 1)
