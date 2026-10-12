"""
VUELTA ATRÁS de paso13b - NO SE EJECUTA salvo que haga falta deshacer la
migración, y solo con la confirmación explícita de Gabi.

Devuelve el CHECK chk_visitas_texto_cliente_longitud de visitas a 1..1000
caracteres (el de paso9). Es siempre segura con los datos: relajar un CHECK
no puede hacer que una fila existente lo incumpla. Pydantic sigue exigiendo
300 (MAX_TEXTO_VISITA), así que deshacer paso13b solo quita la segunda
barrera.

Una sola transacción. Freno: sin el argumento --confirmo-vuelta-atras no hace
nada (código 2).
"""

# os: el entorno. sys: argumentos, consola y salida.
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

# El nombre del CHECK y el máximo de paso9, escritos aquí a propósito.
RESTRICCION = "chk_visitas_texto_cliente_longitud"
# El máximo de antes de paso13b.
MAXIMO_PASO9 = 1000

# Conexión y cursor.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor.
cur = cn.cursor()
# try / except: si algo falla, rollback y no se cambia nada.
try:
    # SQL: la definición actual, para que quede constancia.
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s;", (RESTRICCION,))
    # Se enseña.
    print(f"  antes: {cur.fetchone()}")
    # SQL: el CHECK de paso9 otra vez, con el mismo nombre.
    cur.execute(f"ALTER TABLE visitas DROP CONSTRAINT {RESTRICCION}, "
                f"ADD CONSTRAINT {RESTRICCION} CHECK (char_length(texto_cliente) BETWEEN 1 AND {MAXIMO_PASO9});")
    # Definitivo.
    cn.commit()
    # Constancia en la salida.
    print(f"  commit() ejecutado: {RESTRICCION} = 1..{MAXIMO_PASO9}")
# Cualquier error: deshacer y volver a lanzarlo.
except Exception:
    # Nada cambiado.
    cn.rollback()
    # Se dice.
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    # Y se ve el error.
    raise
# Pase lo que pase, se cierra la conexión.
finally:
    # Cerrada.
    cn.close()
