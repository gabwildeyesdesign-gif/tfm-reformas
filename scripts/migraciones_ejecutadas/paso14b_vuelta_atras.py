"""
VUELTA ATRÁS de paso14b - NO SE EJECUTA salvo que haga falta deshacer la
migración, y solo con la confirmación explícita de Gabi.

Vuelve a insertar en reglas_negocio la fila paso_huecos_min = 30 con la
descripción de paso13. Nadie la lee desde D32, así que reinsertarla no cambia
nada del comportamiento: solo deja la tabla como antes de paso14b (por
ejemplo, para volver a un main anterior a D32, que sí la exige).

Idempotente (ON CONFLICT DO NOTHING). Freno: sin el argumento
--confirmo-vuelta-atras no hace nada (código 2).
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
    # Código 2.
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# La fila de paso13, escrita aquí a propósito (no importada).
CLAVE, VALOR = "paso_huecos_min", 30
# Su descripción original (una sola línea).
DESCRIPCION = ("Minutos entre dos inicios de visita que se OFRECEN como huecos libres "
               "cuando el pedido está ocupado (D31). Solo para ofrecer: se puede pedir "
               "cualquier hora válida dentro de una franja.")

# Conexión y cursor.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor.
cur = cn.cursor()
# try / except: si algo falla, rollback.
try:
    # SQL: la fila otra vez; si ya existe, no se toca.
    cur.execute("INSERT INTO reglas_negocio (clave, valor, descripcion) VALUES (%s, %s, %s) "
                "ON CONFLICT (clave) DO NOTHING;", (CLAVE, VALOR, DESCRIPCION))
    # 1 = insertada, 0 = ya existía.
    print(f"  {CLAVE} = {VALOR} -> {'insertada' if cur.rowcount == 1 else 'ya existía, no se toca'}")
    # Definitivo.
    cn.commit()
    # Constancia.
    print("  commit() ejecutado")
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
