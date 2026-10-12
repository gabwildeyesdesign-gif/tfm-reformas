"""
VUELTA ATRÁS de paso13 - NO SE EJECUTA salvo que haga falta deshacer la
migración, y solo con la confirmación explícita de Gabi.

Borra de reglas_negocio las siete filas que añadió paso13 (plan
docs/Plan_Agente2_Calendar.txt, sección 7, B1), por su CLAVE exacta: son
claves propias de esa migración, no las usa ninguna otra fila. Antes enseña
cuáles existen y con qué valor. No toca ninguna otra regla.

Solo es segura mientras ningún código las lea: hoy (B1) nadie las lee. Desde
B2 (plazos) y B5 (paso_huecos_min), borrarlas haría que esos endpoints den
503 configuracion_incompleta, que es el comportamiento previsto ante una
regla que falta, pero un 503 al fin y al cabo.

Una sola transacción: o se borran todas o ninguna.

Freno: sin el argumento --confirmo-vuelta-atras no hace nada.
"""

# os: el entorno. sys: argumentos, consola y salida. Path: rutas.
import os
import sys
from pathlib import Path

# psycopg2: hablar con PostgreSQL. load_dotenv: leer el .env.
import psycopg2
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# El freno: sin la bandera, se para aquí sin conectarse a nada.
if "--confirmo-vuelta-atras" not in sys.argv:
    # Se explica por qué no hace nada.
    print("No se ejecuta: falta --confirmo-vuelta-atras (solo con la confirmación explícita de Gabi).")
    # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# Las siete claves de paso13, escritas aquí a propósito (no importadas de
# paso13): la vuelta atrás tiene que seguir funcionando aunque alguien
# cambie aquel archivo.
CLAVES_PASO13 = [
    "semanas_obra_bano_min", "semanas_obra_bano_max",
    "semanas_obra_cocina_min", "semanas_obra_cocina_max",
    "semanas_fabricacion_cocina_min", "semanas_fabricacion_cocina_max",
    "paso_huecos_min",
]

# Conexión y cursor.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

# try / except: si algo falla, rollback y no se borra nada.
try:
    # SQL: las filas de esas claves que existen ahora, con su valor.
    cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s) ORDER BY clave;",
                (CLAVES_PASO13,))
    # Se enseñan antes de borrar, para que quede constancia.
    for clave, valor in cur.fetchall():
        print(f"  se borrará: {clave} = {valor}")
    # DELETE solo por esas claves exactas.
    cur.execute("DELETE FROM reglas_negocio WHERE clave = ANY(%s);", (CLAVES_PASO13,))
    # Cuántas se han borrado (7 si paso13 se aplicó entera).
    print(f"  filas borradas: {cur.rowcount}")
    # Definitivo.
    cn.commit()
    # Constancia en la salida.
    print("  commit() ejecutado")
except Exception:
    # Deshace todo y vuelve a lanzar el error para verlo.
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha borrado nada")
    raise
finally:
    # Se cierra la conexión pase lo que pase.
    cn.close()
