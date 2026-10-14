"""
MIGRACIÓN DE DATOS DE CONFIGURACIÓN - NO ES UN SCRIPT DE VERIFICACIÓN.
PREPARADA, SIN APLICAR (Bloque D del plan D32).

Borra de reglas_negocio la fila paso_huecos_min (D31: el paso de 30 min de
las horas que ofrecía el 409 hueco_ocupado). D32 la sustituye por la rejilla
de D23.5 (duración + desplazamiento) y el código de la rama
feat/n0-horario-visitas ya NO la lee (plan docs/Plan_Horario_Visitas_D32.txt,
2.6): es la parte "contract" del expand/contract.

CUÁNDO: DESPUÉS del merge de feat/n0-horario-visitas en main y con el
uvicorn del 8000 ya reiniciado con el código nuevo. Antes, main la lee
(leer_reglas_visita la exige) y borrarla haría que POST /visits, POST
/resultados-llamada, el alias y get_business_rules dieran 503. Y SOLO tras
la frase de Gabi "confirmo la migración paso14b".

Antes de borrar enseña el valor (para la vuelta atrás). Una sola
transacción. Idempotente: si la fila ya no existe, lo dice y no toca nada.

Freno: sin el argumento --confirmo-paso14b no hace nada (código 2).

Vuelta atrás (escrita, sin ejecutar): paso14b_vuelta_atras.py.
Comprobación: scripts/check_migracion_paso14b.py (FALLA antes de migrar).
OJO, en el mismo paso: check_migracion_agente2 exige hoy esa fila (su punto
3); se cambia junto con la migración (sección 13 del plan).
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
if "--confirmo-paso14b" not in sys.argv:
    # Se explica por qué no hace nada.
    print("No se ejecuta: falta --confirmo-paso14b (solo tras el merge y la frase de Gabi "
          "\"confirmo la migración paso14b\").")
    # Código 2: "no se ha hecho nada a propósito".
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# La clave que se borra, escrita aquí a propósito (no importada de app/).
CLAVE = "paso_huecos_min"

# Conexión y cursor (una sola transacción).
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor.
cur = cn.cursor()
# try / except: si algo falla, rollback y no se borra nada.
try:
    # SQL: la fila y su valor, antes de borrar.
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (CLAVE,))
    # La fila o None.
    fila = cur.fetchone()
    # Ya no existe: no se toca nada (idempotente).
    if fila is None:
        # Se dice.
        print(f"  {CLAVE} ya no existe: no se toca nada")
        # Se cierra la transacción de lectura.
        cn.rollback()
        # Código 0: nada que hacer.
        sys.exit(0)
    # Constancia del valor (para la vuelta atrás).
    print(f"  se borrará: {CLAVE} = {fila[0]}")
    # SQL: solo esa clave exacta.
    cur.execute("DELETE FROM reglas_negocio WHERE clave = %s;", (CLAVE,))
    # Cuántas (1).
    print(f"  filas borradas: {cur.rowcount}")
    # Definitivo.
    cn.commit()
    # Constancia.
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
