"""
PASO 3d - Verificacion real de get_transactional_connection() y del
rollback anadido a get_db_connection().

No se acepta "no dio error": cada caso comprueba el ESTADO REAL de la
tabla usando una conexion APARTE, fuera del pool. Es importante que sea
aparte: desde dentro de la misma transaccion se verian filas que todavia
no estan confirmadas, y la prueba mentiria.
"""

import sys
from pathlib import Path

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

from app.config import DATABASE_URL
from app.db import connection as db

MARCA = "prueba_paso3d"
ok = 0
fallos = []


def contar_desde_fuera(cur_externo, accion=None):
    """Cuenta filas de prueba con una conexion ajena al pool."""
    if accion is None:
        cur_externo.execute(
            "SELECT COUNT(*) FROM logs WHERE entity_type = %s;", (MARCA,)
        )
    else:
        cur_externo.execute(
            "SELECT COUNT(*) FROM logs WHERE entity_type = %s AND accion = %s;",
            (MARCA, accion),
        )
    # commit() para cerrar la transaccion de lectura del observador: sin
    # esto, la conexion externa se quedaria viendo una foto congelada de
    # la tabla y no veria los cambios confirmados despues.
    cn_externa.commit()
    return cur_externo.fetchone()[0]


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}   {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"  [FALLO] {titulo}   {detalle}")


db.init_pool()
cn_externa = psycopg2.connect(DATABASE_URL)
cur_externa = cn_externa.cursor()

# Limpieza previa, por si una ejecucion anterior dejo restos.
cur_externa.execute("DELETE FROM logs WHERE entity_type = %s;", (MARCA,))
cn_externa.commit()
print(f"Estado inicial: {contar_desde_fuera(cur_externa)} filas de prueba\n")

# ==================================================================
print("=" * 78)
print("CASO 1 - get_transactional_connection() con escritura CORRECTA")
print("=" * 78)
with db.get_transactional_connection() as conn:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logs (entity_type, entity_id, accion) VALUES (%s,%s,%s) RETURNING id;",
        (MARCA, 1, "caso1_commit"),
    )
    id_nuevo = cur.fetchone()[0]
    print(f"  INSERT ejecutado dentro del with, id devuelto = {id_nuevo}")
    visibles_dentro = contar_desde_fuera(cur_externa, "caso1_commit")
    print(f"  Visible desde FUERA mientras el with sigue abierto: {visibles_dentro}")
    comprobar("Antes del commit la fila NO es visible desde fuera",
              visibles_dentro == 0, f"(contadas={visibles_dentro})")
    cur.close()

visibles_despues = contar_desde_fuera(cur_externa, "caso1_commit")
print(f"  Visible desde FUERA tras salir del with: {visibles_despues}")
comprobar("COMMIT: la fila SI quedo guardada de verdad",
          visibles_despues == 1, f"(contadas={visibles_despues})")

# ==================================================================
print("\n" + "=" * 78)
print("CASO 2 - Excepcion a MITAD de una escritura de dos filas")
print("=" * 78)
print("  Se insertan 2 filas y se lanza un error de Python entre medias.")
try:
    with db.get_transactional_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs (entity_type, entity_id, accion) VALUES (%s,%s,%s);",
            (MARCA, 2, "caso2_primera"),
        )
        print("    fila 1 insertada (aun sin confirmar)")
        cur.execute(
            "INSERT INTO logs (entity_type, entity_id, accion) VALUES (%s,%s,%s);",
            (MARCA, 3, "caso2_segunda"),
        )
        print("    fila 2 insertada (aun sin confirmar)")
        raise ValueError("fallo simulado del codigo de negocio")
except ValueError as e:
    print(f"  Excepcion recibida por quien llamo: {type(e).__name__}: {e}")
    comprobar("La excepcion SIGUE subiendo (el 'raise' funciona)", True)
else:
    comprobar("La excepcion SIGUE subiendo (el 'raise' funciona)", False,
              "-> se trago la excepcion")

quedan_1 = contar_desde_fuera(cur_externa, "caso2_primera")
quedan_2 = contar_desde_fuera(cur_externa, "caso2_segunda")
print(f"  Filas 'caso2_primera' en la tabla: {quedan_1}")
print(f"  Filas 'caso2_segunda' en la tabla: {quedan_2}")
comprobar("ROLLBACK: no quedo NINGUNA de las dos filas a medias",
          quedan_1 == 0 and quedan_2 == 0, f"({quedan_1} + {quedan_2})")

# ==================================================================
print("\n" + "=" * 78)
print("CASO 3 - Error de la propia base de datos (violacion de NOT NULL)")
print("=" * 78)
try:
    with db.get_transactional_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs (entity_type, entity_id, accion) VALUES (%s,%s,%s);",
            (MARCA, 4, "caso3_previa"),
        )
        print("    fila previa insertada (aun sin confirmar)")
        # accion es NOT NULL: este INSERT lo viola a proposito.
        cur.execute(
            "INSERT INTO logs (entity_type, entity_id, accion) VALUES (%s,%s,NULL);",
            (MARCA, 5),
        )
except psycopg2.Error as e:
    print(f"  Excepcion de Postgres: {type(e).__name__}")
    comprobar("El error de Postgres sube hasta quien llamo", True)

quedan_3 = contar_desde_fuera(cur_externa, "caso3_previa")
print(f"  Filas 'caso3_previa' en la tabla: {quedan_3}")
comprobar("ROLLBACK tambien con un error de SQL: no quedo la fila previa",
          quedan_3 == 0, f"(contadas={quedan_3})")

# ==================================================================
print("\n" + "=" * 78)
print("CASO 4 - La conexion vuelve al pool utilizable tras un rollback")
print("=" * 78)
with db.get_transactional_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT 1;")
    valor = cur.fetchone()[0]
    print(f"  SELECT 1 -> {valor}")
comprobar("El pool sigue sirviendo conexiones sanas despues de 2 rollbacks",
          valor == 1)

# ==================================================================
print("\n" + "=" * 78)
print("CASO 5 - 3c: get_db_connection() deja la conexion IDLE, no 'idle in transaction'")
print("=" * 78)
with db.get_db_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM logs;")
    cur.fetchone()
    estado_dentro = conn.get_transaction_status()
    print(f"  Estado DENTRO del with tras el SELECT: {estado_dentro} "
          f"({'INTRANS' if estado_dentro == ext.TRANSACTION_STATUS_INTRANS else 'otro'})")
    cur.close()
    conn_guardada = conn

estado_fuera = conn_guardada.get_transaction_status()
print(f"  Estado FUERA del with (tras el rollback anadido): {estado_fuera} "
      f"({'IDLE' if estado_fuera == ext.TRANSACTION_STATUS_IDLE else 'NO IDLE'})")
comprobar("Un SELECT abre transaccion implicita (INTRANS dentro del with)",
          estado_dentro == ext.TRANSACTION_STATUS_INTRANS)
comprobar("3c: al salir queda en IDLE, no 'idle in transaction'",
          estado_fuera == ext.TRANSACTION_STATUS_IDLE)

# ==================================================================
print("\n" + "=" * 78)
print("CASO 6 - get_db_connection() NO guarda escrituras (comportamiento esperado)")
print("=" * 78)
with db.get_db_connection() as conn:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logs (entity_type, entity_id, accion) VALUES (%s,%s,%s);",
        (MARCA, 6, "caso6_lectura"),
    )
    print("  INSERT hecho a traves de la funcion de SOLO LECTURA")
quedan_6 = contar_desde_fuera(cur_externa, "caso6_lectura")
print(f"  Filas 'caso6_lectura' en la tabla: {quedan_6}")
comprobar("La funcion de lectura descarta la escritura (por eso existe la otra)",
          quedan_6 == 0, f"(contadas={quedan_6})")

# ==================================================================
print("\n" + "=" * 78)
print("LIMPIEZA - se borran las filas de prueba de la tabla logs")
print("=" * 78)
cur_externa.execute("DELETE FROM logs WHERE entity_type = %s;", (MARCA,))
cn_externa.commit()
restantes = contar_desde_fuera(cur_externa)
print(f"  Filas de prueba restantes: {restantes}")
cur_externa.execute("SELECT COUNT(*) FROM logs;")
cn_externa.commit()
print(f"  Filas totales en logs tras la limpieza: {cur_externa.fetchone()[0]}")
comprobar("Tabla logs limpia", restantes == 0)

db.close_pool()
cur_externa.close()
cn_externa.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for f in fallos:
        print("  - " + f)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
