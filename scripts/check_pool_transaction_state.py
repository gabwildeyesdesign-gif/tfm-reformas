"""
PASO 3a - ¿putconn() deja limpia una conexion devuelta con una
transaccion abierta sin commitear, o esa transaccion se queda pegada
para el siguiente que tome la conexion prestada?

Esto NO se responde leyendo documentacion: se responde ejecutando.
La diferencia practica es enorme:
  - Si el pool limpia -> el bug es "solo" perdida de datos.
  - Si NO limpia      -> una peticion HTTP podria heredar la transaccion
                         a medias de otra peticion anterior, que es un
                         problema de corrupcion de datos entre usuarios.
"""

import sys
from pathlib import Path

# Se anade la raiz del repo a sys.path para poder importar app.*, igual
# que hace scripts/check_graceful_shutdown.py. Se calcula relativa a
# este archivo, sin rutas absolutas de una maquina concreta.
# Este script vive en scripts/, pero el paquete "app" esta en la raiz del
# repositorio. Al ejecutar "python scripts/<archivo>.py", Python solo anade
# la carpeta del archivo (scripts/) a sys.path, asi que "import app.algo"
# fallaria con ModuleNotFoundError. Se calcula la raiz a partir de la
# ubicacion de este mismo archivo, en vez de escribir una ruta absoluta,
# para que funcione en cualquier maquina y desde cualquier directorio.
# Misma tecnica que scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
from psycopg2 import extensions as ext

from app.config import DATABASE_URL, DB_POOL_MIN, DB_POOL_MAX
from app.db import connection as db

# Traduccion de los codigos numericos de estado a texto legible.
ESTADOS = {
    ext.TRANSACTION_STATUS_IDLE: "IDLE (0) - sin transaccion abierta, limpia",
    ext.TRANSACTION_STATUS_ACTIVE: "ACTIVE (1) - ejecutando una consulta ahora",
    ext.TRANSACTION_STATUS_INTRANS: "INTRANS (2) - DENTRO de una transaccion abierta",
    ext.TRANSACTION_STATUS_INERROR: "INERROR (3) - transaccion abierta y rota por un error",
    ext.TRANSACTION_STATUS_UNKNOWN: "UNKNOWN (4) - conexion perdida",
}


def estado(conn):
    return ESTADOS.get(conn.get_transaction_status(), "???")


print("=" * 78)
print(f"Pool configurado: DB_POOL_MIN={DB_POOL_MIN}, DB_POOL_MAX={DB_POOL_MAX}")
print("=" * 78)

db.init_pool()
pool = db._pool
print(f"Tras init_pool(): libres={len(pool._pool)}  prestadas={len(pool._used)}")

# ------------------------------------------------------------------
# ESCENARIO 1: INSERT sin commit, se devuelve la conexion al pool
# ------------------------------------------------------------------
print("\n" + "=" * 78)
print("ESCENARIO 1 - INSERT sin commit(), y se devuelve la conexion")
print("=" * 78)

with db.get_db_connection() as conn:
    id_conn_1 = id(conn)
    backend_1 = conn.info.backend_pid
    print(f"  Conexion prestada: id_python={id_conn_1}  backend_pid={backend_1}")
    print(f"  Estado al recibirla        : {estado(conn)}")

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logs (entity_type, entity_id, accion, detalle) "
        "VALUES (%s, %s, %s, %s) RETURNING id;",
        ("prueba_paso3a", 999999, "insert_sin_commit", None),
    )
    id_insertado = cur.fetchone()[0]
    cur.close()
    print(f"  INSERT ejecutado, RETURNING id = {id_insertado}  (SIN commit)")
    print(f"  Estado despues del INSERT  : {estado(conn)}")

print("  -> Salimos del 'with': get_db_connection() ha llamado a putconn()")
print(f"  Pool ahora: libres={len(pool._pool)}  prestadas={len(pool._used)}")

print("\n  --- Pedimos OTRA conexion al pool y miramos como llega ---")
with db.get_db_connection() as conn2:
    id_conn_2 = id(conn2)
    backend_2 = conn2.info.backend_pid
    misma = "SI, ES LA MISMA" if id_conn_2 == id_conn_1 else "no, es otra distinta"
    print(f"  Conexion prestada: id_python={id_conn_2}  backend_pid={backend_2}")
    print(f"  ¿Es la misma conexion de antes? {misma}")
    print(f"  ESTADO CON EL QUE LLEGA    : {estado(conn2)}")
    if conn2.get_transaction_status() == ext.TRANSACTION_STATUS_IDLE:
        print("  >>> VEREDICTO: el pool SI limpia la transaccion al devolverla.")
    else:
        print("  >>> VEREDICTO: la transaccion SE QUEDA PEGADA. Problema grave.")

# ------------------------------------------------------------------
# ¿Se guardo la fila? Se comprueba con una conexion APARTE, fuera del
# pool: si preguntaramos desde dentro de la misma transaccion, veriamos
# la fila aunque no estuviera confirmada.
# ------------------------------------------------------------------
print("\n  --- ¿Persistio la fila del INSERT sin commit? ---")
cn_aparte = psycopg2.connect(DATABASE_URL)
cur_aparte = cn_aparte.cursor()
cur_aparte.execute("SELECT COUNT(*) FROM logs WHERE entity_type = 'prueba_paso3a';")
cuantas = cur_aparte.fetchone()[0]
print(f"  Filas 'prueba_paso3a' visibles desde fuera: {cuantas}")
print("  ->", "PERDIDA (lo esperado sin commit)" if cuantas == 0 else "PERSISTIO (inesperado)")

# ------------------------------------------------------------------
# ESCENARIO 2: una consulta que FALLA deja la conexion en INERROR.
# ------------------------------------------------------------------
print("\n" + "=" * 78)
print("ESCENARIO 2 - Una consulta que falla (conexion rota) se devuelve al pool")
print("=" * 78)

try:
    with db.get_db_connection() as conn3:
        print(f"  Estado al recibirla        : {estado(conn3)}")
        cur = conn3.cursor()
        cur.execute("SELECT * FROM tabla_que_no_existe;")
except psycopg2.Error as e:
    print(f"  Excepcion capturada: {type(e).__name__}")

print("  -> Salimos del 'with' por una EXCEPCION; putconn() se ejecuto igual")
print(f"  Pool ahora: libres={len(pool._pool)}  prestadas={len(pool._used)}")

with db.get_db_connection() as conn4:
    print(f"  ESTADO CON EL QUE LLEGA    : {estado(conn4)}")
    if conn4.get_transaction_status() == ext.TRANSACTION_STATUS_IDLE:
        print("  >>> El pool tambien limpia la conexion rota.")
    else:
        print("  >>> La conexion llega ROTA al siguiente que la pida.")
    # Se comprueba que ademas sirve para trabajar de verdad.
    cur = conn4.cursor()
    cur.execute("SELECT 1;")
    print(f"  SELECT 1 sobre esa conexion -> {cur.fetchone()[0]} (utilizable)")
    cur.close()

# ------------------------------------------------------------------
# ESCENARIO 3: ¿y si el pool ya tiene sus minconn libres? psycopg2 toma
# otro camino distinto al devolver la conexion. Se fuerza pidiendo 3 a
# la vez y devolviendolas.
# ------------------------------------------------------------------
print("\n" + "=" * 78)
print("ESCENARIO 3 - Devolver una conexion cuando el pool ya tiene minconn libres")
print("=" * 78)
c_a = pool.getconn()
c_b = pool.getconn()
c_c = pool.getconn()
print(f"  3 prestadas -> libres={len(pool._pool)}  prestadas={len(pool._used)}")
cur = c_c.cursor()
cur.execute("INSERT INTO logs (entity_type, entity_id, accion) VALUES ('prueba_paso3a','1','x');")
print(f"  c_c con INSERT sin commit -> {estado(c_c)}")
pool.putconn(c_a)
pool.putconn(c_b)
print(f"  Devueltas a y b -> libres={len(pool._pool)} (ya en el minimo {DB_POOL_MIN})")
pool.putconn(c_c)
print(f"  Devuelta c_c    -> libres={len(pool._pool)}  prestadas={len(pool._used)}")
print(f"  ¿Esta cerrada c_c? conn.closed = {c_c.closed}  (0=abierta, distinto de 0=cerrada)")
if c_c.closed:
    print("  >>> El pool la CIERRA en vez de guardarla: la transaccion muere con ella.")
else:
    print(f"  >>> La guarda; estado: {estado(c_c)}")

cur_aparte.execute("SELECT COUNT(*) FROM logs WHERE entity_type = 'prueba_paso3a';")
print(f"\n  Filas de prueba persistidas en total: {cur_aparte.fetchone()[0]} (deben ser 0)")

db.close_pool()
cur_aparte.close()
cn_aparte.close()
print("\nFIN - pool cerrado.")
