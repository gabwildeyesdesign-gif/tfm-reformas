"""
Verificación real de la validación de conexiones del pool y de los
tiempos límite (rama fix/n0-timeouts-db).

Problema que se arregla: las conexiones guardadas en el pool morían tras
minutos ociosas (cortes de red o del pooler de Supabase), psycopg2 no se
enteraba (conn.closed seguía valiendo 0) y la primera consulta de negocio
fallaba con OperationalError, a veces tras más de 3 minutos colgada.

CASOS:
  0) Valores por defecto: en un proceso hijo SIN variables de entorno de
     timeouts, una conexión del pool debe tener statement_timeout = 30s y
     los parámetros de libpq (connect_timeout, keepalives...) aplicados.
  A) Camino normal: con conexiones vivas, el préstamo NO descarta nada.
     Se mide el coste del préstamo (SELECT 1 + rollback).
  1) Corte de red simulado: se matan con pg_terminate_backend TODAS las
     conexiones ociosas del pool (no solo una: un corte real las mata a
     la vez). Después, get_transactional_connection() debe funcionar,
     descartando exactamente esas conexiones y entregando una nueva.
  2) statement_timeout efectivo y sobrescribible por entorno: este script
     fija DB_STATEMENT_TIMEOUT_MS=2000 ANTES de importar app.config. Tras
     un rollback en la conexión (para demostrar que el SET se confirmó y
     no se deshace), SELECT pg_sleep(5) debe cancelarse a los ~2 s.

Si un caso espera una excepción y no sale ninguna, se marca FALLO de
forma explícita (rama else del try): que no salga nada también es un
fallo, y la comprobación del tipo de excepción no lo detectaría.

Este script NO escribe en ninguna tabla. Sí termina conexiones del
servidor, pero solo las que abrió él mismo (sus pids se leen antes).
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Este script vive en scripts/, pero el paquete "app" está en la raíz del
# repositorio: se añade la raíz a sys.path (misma técnica que
# scripts/check_rollback_conn_cerrada.py).
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

# Tiene que ir ANTES de importar nada de app: app/config.py lee las
# variables de entorno en el momento de importarse, una sola vez. Si se
# fijara después, ya habría leído el valor por defecto (30000).
# load_dotenv() no pisa una variable que ya existe en el entorno, así que
# este valor gana aunque algún día se añada DB_STATEMENT_TIMEOUT_MS al .env.
TIMEOUT_PRUEBA_MS = 2000
os.environ["DB_STATEMENT_TIMEOUT_MS"] = str(TIMEOUT_PRUEBA_MS)

import psycopg2

from app.config import DATABASE_URL
from app.db import connection as db

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}   {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"  [FALLO] {titulo}   {detalle}")


@contextlib.contextmanager
def capturar_avisos():
    """
    Recoge lo que se escriba en stderr mientras dura el "with".

    _obtener_conexion_validada() avisa en stderr de cada conexión
    descartada. redirect_stderr sustituye sys.stderr por un buffer en
    memoria (StringIO) durante el bloque; como el print del aviso busca
    sys.stderr en el momento de escribir, el texto acaba en el buffer.
    Al salir se vuelve a mostrar en pantalla, para que se vea.
    """
    buffer = io.StringIO()
    lineas = []
    with contextlib.redirect_stderr(buffer):
        yield lineas
    texto = buffer.getvalue()
    for linea in texto.splitlines():
        print(f"          stderr> {linea}")
    lineas.extend(l for l in texto.splitlines() if l.startswith("[AVISO]"))


# ---------------------------------------------------------------------------
# CASO 0: valores por defecto, en un proceso hijo sin la variable de prueba
# ---------------------------------------------------------------------------
print("\nCASO 0: valores por defecto (proceso hijo sin DB_STATEMENT_TIMEOUT_MS)")

# Código que ejecuta el proceso hijo: abre el pool con la configuración
# por defecto, pide una conexión y devuelve en JSON lo que interesa.
CODIGO_HIJO = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from app.db import connection as db
db.init_pool()
with db.get_db_connection() as conn:
    cur = conn.cursor()
    cur.execute("SHOW statement_timeout")
    timeout = cur.fetchone()[0]
    params = conn.get_dsn_parameters()
db.close_pool()
print(json.dumps({"statement_timeout": timeout, "params": params}))
"""

# Copia del entorno actual SIN la variable de prueba, para que el hijo use
# el valor por defecto de app/config.py. PYTHONIOENCODING=utf-8: gotcha de
# cp1252 en Windows al capturar la salida de un hijo (ver CLAUDE.md).
entorno_hijo = {k: v for k, v in os.environ.items() if k != "DB_STATEMENT_TIMEOUT_MS"}
entorno_hijo["PYTHONIOENCODING"] = "utf-8"
hijo = subprocess.run(
    [sys.executable, "-c", CODIGO_HIJO, str(RAIZ_REPO)],
    capture_output=True,
    encoding="utf-8",
    env=entorno_hijo,
    timeout=60,
)
if hijo.returncode != 0:
    comprobar("el proceso hijo termina sin error", False, hijo.stderr.strip()[-300:])
else:
    datos = json.loads(hijo.stdout.strip().splitlines()[-1])
    comprobar(
        "SHOW statement_timeout por defecto = '30s'",
        datos["statement_timeout"] == "30s",
        f"(obtenido: {datos['statement_timeout']!r})",
    )
    esperados = {
        "connect_timeout": "10",
        "keepalives": "1",
        "keepalives_idle": "30",
        "keepalives_interval": "10",
        "keepalives_count": "3",
    }
    for clave, valor in esperados.items():
        obtenido = datos["params"].get(clave)
        comprobar(f"parámetro libpq {clave} = {valor}", obtenido == valor, f"(obtenido: {obtenido!r})")


# A partir de aquí, el pool de ESTE proceso, con el timeout de prueba.
db.init_pool()

# ---------------------------------------------------------------------------
# CASO A: camino normal, con conexiones vivas
# ---------------------------------------------------------------------------
print("\nCASO A: préstamo con conexiones vivas (no debe descartar nada)")
tiempos = []
with capturar_avisos() as avisos_a:
    for _ in range(5):
        t0 = time.perf_counter()
        with db.get_db_connection():
            # Se mide solo lo que tarda en ENTREGAR la conexión (validación
            # incluida); el bloque está vacío a propósito.
            tiempos.append(time.perf_counter() - t0)
comprobar("0 conexiones descartadas en 5 préstamos", len(avisos_a) == 0, f"(avisos: {len(avisos_a)})")
print(
    "          coste por préstamo (ms): "
    + ", ".join(f"{t * 1000:.0f}" for t in tiempos)
    + f"  | media {sum(tiempos) / len(tiempos) * 1000:.0f} ms"
)

# ---------------------------------------------------------------------------
# CASO 1: matar TODAS las conexiones ociosas del pool
# ---------------------------------------------------------------------------
print("\nCASO 1: pg_terminate_backend sobre TODAS las conexiones ociosas del pool")

# _pool._pool es la lista interna de conexiones ociosas de psycopg2 (la
# misma que inspecciona scripts/check_graceful_shutdown.py). Se lee el pid
# de cada una con SELECT pg_backend_pid() usándolas directamente, sin
# sacarlas del pool, y se cierra la transacción implícita con rollback.
ociosas = list(db._pool._pool)
pids = []
for conn in ociosas:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        pids.append(cur.fetchone()[0])
    conn.rollback()
print(f"          conexiones ociosas: {len(ociosas)}  | pids: {pids}")
comprobar("hay al menos 2 conexiones ociosas que matar", len(pids) >= 2, f"({len(pids)})")

# Conexión aparte, fuera del pool, para matar a las otras.
verdugo = psycopg2.connect(DATABASE_URL)
with verdugo.cursor() as cur:
    for pid in pids:
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        comprobar(f"pg_terminate_backend({pid}) devuelve true", cur.fetchone()[0] is True)
verdugo.commit()
verdugo.close()

# Pausa breve para que el aviso de cierre llegue al socket del cliente.
time.sleep(2)
print(f"          conn.closed de las ociosas tras matarlas: {[c.closed for c in ociosas]}")

with capturar_avisos() as avisos_1:
    try:
        with db.get_transactional_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                pid_nuevo = cur.fetchone()[0]
    except Exception as error:
        comprobar(
            "get_transactional_connection() funciona tras matar las ociosas",
            False,
            f"-> {type(error).__name__}: {str(error).strip()[:150]}",
        )
    else:
        comprobar(
            "get_transactional_connection() funciona tras matar las ociosas",
            True,
            f"(pid nuevo {pid_nuevo})",
        )
        comprobar("la conexión entregada es una nueva, no una de las muertas", pid_nuevo not in pids)
comprobar(
    "se descartaron exactamente las conexiones muertas",
    len(avisos_1) == len(pids),
    f"(avisos: {len(avisos_1)}, muertas: {len(pids)})",
)

# ---------------------------------------------------------------------------
# CASO 2: statement_timeout efectivo, tras un rollback
# ---------------------------------------------------------------------------
print(f"\nCASO 2: statement_timeout = {TIMEOUT_PRUEBA_MS} ms fijado por entorno")
with db.get_db_connection() as conn:
    cur = conn.cursor()
    cur.execute("SHOW statement_timeout")
    antes = cur.fetchone()[0]
    # rollback() deshace la transacción en curso. Si el SET de la factoría
    # no se hubiera confirmado con commit(), esto lo desharía también.
    conn.rollback()
    cur.execute("SHOW statement_timeout")
    despues = cur.fetchone()[0]
    comprobar("SHOW statement_timeout = '2s' antes del rollback", antes == "2s", f"({antes!r})")
    comprobar("SHOW statement_timeout = '2s' tras el rollback", despues == "2s", f"({despues!r})")
    conn.rollback()

    t0 = time.perf_counter()
    try:
        cur.execute("SELECT pg_sleep(5)")
    except psycopg2.errors.QueryCanceled as error:
        transcurrido = time.perf_counter() - t0
        comprobar("pg_sleep(5) se cancela con QueryCanceled", True, f"({str(error).strip()})")
        comprobar(
            "se cancela a los ~2 s (entre 1,8 y 4 s), no a los 5",
            1.8 <= transcurrido <= 4,
            f"(medido: {transcurrido:.2f} s)",
        )
    except Exception as error:
        comprobar(
            "pg_sleep(5) se cancela con QueryCanceled",
            False,
            f"-> salió {type(error).__name__} a los {time.perf_counter() - t0:.2f} s",
        )
    else:
        # Rama else del try: solo se ejecuta si NO salió ninguna excepción.
        comprobar(
            "pg_sleep(5) se cancela con QueryCanceled",
            False,
            f"-> NO salió ninguna excepción: la consulta duró {time.perf_counter() - t0:.2f} s",
        )

# Ningún préstamo se quedó sin devolver.
comprobar("el pool no tiene conexiones prestadas al terminar", len(db._pool._used) == 0, f"({len(db._pool._used)})")
db.close_pool()

total = ok + len(fallos)
print(f"\nRESULTADO: {ok}/{total} comprobaciones OK")
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
