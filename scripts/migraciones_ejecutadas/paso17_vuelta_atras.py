"""
VUELTA ATRÁS de paso17 - NO SE EJECUTA salvo que haga falta deshacer la
migración, y solo con la confirmación explícita de Gabi.

Borra de reglas_negocio las dos filas que añadió paso17
(dias_antelacion_visita_chat y dias_antelacion_visita_admin), por su CLAVE
exacta. No toca ninguna otra regla.

OJO: solo es segura ANTES del merge de feat/n0-antelacion-visitas. Desde ese
merge, el código las exige (leer_reglas_visita, nueve claves): borrarlas
haría que POST /visits, POST /resultados-llamada, GET /huecos-libres y las
tools get_business_rules y consultar_huecos_libres den 503
configuracion_incompleta. Después del merge, para quitar la antelación sin
tocar código basta con poner las dos a 0 (P5 del plan D45).

Una sola transacción: o se borran las dos o ninguna.

Freno: sin el argumento --confirmo-vuelta-atras no hace nada (código 2).
"""

# os: el entorno.
import os
# sys: argumentos, consola y código de salida.
import sys
# Path: rutas de archivos.
from pathlib import Path

# psycopg2: hablar con PostgreSQL.
import psycopg2
# load_dotenv: leer el .env.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# El freno: sin la bandera, se para aquí sin conectarse a nada.
if "--confirmo-vuelta-atras" not in sys.argv:
    # Se explica por qué no hace nada.
    print("No se ejecuta: falta --confirmo-vuelta-atras (solo con la confirmación explícita de Gabi).")
    # Código 2: "no se ha hecho nada a propósito".
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# Las dos claves de paso17, escritas aquí a propósito (no importadas): la
# vuelta atrás tiene que funcionar aunque alguien cambie la migración.
CLAVES_PASO17 = ["dias_antelacion_visita_chat", "dias_antelacion_visita_admin"]

# Conexión y cursor.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor.
cur = cn.cursor()

# try / except: si algo falla, rollback y no se borra nada.
try:
    # SQL: las filas de esas claves que existen ahora, con su valor.
    cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s) ORDER BY clave;",
                (CLAVES_PASO17,))
    # Se enseñan antes de borrar, para que quede constancia.
    for clave, valor in cur.fetchall():
        # Una línea por fila.
        print(f"  se borrará: {clave} = {valor}")
    # SQL: DELETE solo por esas claves exactas.
    cur.execute("DELETE FROM reglas_negocio WHERE clave = ANY(%s);", (CLAVES_PASO17,))
    # Cuántas se han borrado (2 si paso17 se aplicó entera).
    print(f"  filas borradas: {cur.rowcount}")
    # Definitivo.
    cn.commit()
    # Constancia en la salida.
    print("  commit() ejecutado")
# Cualquier error: deshacer y volver a lanzarlo.
except Exception:
    # Nada borrado.
    cn.rollback()
    # Se dice.
    print("  ERROR: rollback() ejecutado, no se ha borrado nada")
    # Y se ve el error.
    raise
# Pase lo que pase, se cierra la conexión.
finally:
    # Cerrada.
    cn.close()
