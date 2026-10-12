"""
MIGRACIÓN DE DATOS DE CONFIGURACIÓN - NO ES UN SCRIPT DE VERIFICACIÓN.

Añade a reglas_negocio SIETE reglas (rama feat/n0-agente2, plan en
docs/Plan_Agente2_Calendar.txt, secciones 3.4, 7 (B1) y 10):

  Plazos orientativos de ejecución de D18.7, en semanas (mínimo y máximo):
    semanas_obra_bano_min = 2            semanas_obra_bano_max = 3
    semanas_obra_cocina_min = 2          semanas_obra_cocina_max = 4
    semanas_fabricacion_cocina_min = 3   semanas_fabricacion_cocina_max = 6
  Paso de la rejilla con la que se OFRECEN huecos libres de visita (D31,
  P19 = C del plan): paso_huecos_min = 30.

Nadie las lee todavía: las leerán GET /leads/session (plazo_orientativo,
Bloque B2) y la comprobación de huecos de visita (Bloque B5), con su
validación y su 503 si faltan o son incoherentes. Todo el código de main
lee reglas_negocio por clave, así que siete filas nuevas no le cambian
nada.

No cambia el esquema (ni tablas, ni columnas, ni restricciones): solo
inserta filas. Es puramente ADITIVA, así que no hace falta partirla en
expandir y contraer, y docs/schema_actual.sql no cambia (no guarda filas).

Es idempotente: si una fila ya existe, no se inserta ni se sobrescribe, así
que ejecutarla dos veces deja la base de datos igual que una.

Enseña una FOTO de todas las reglas antes y después, y comprueba que
ninguna de las que ya existían ha cambiado.

Vuelta atrás (escrita, sin ejecutar): paso13_vuelta_atras.py.
"""

# os: para leer variables de entorno (os.getenv).
import os
# sys: para cambiar la codificación de la salida de la consola.
import sys
# Path: para construir rutas de archivos sin escribirlas a mano.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# load_dotenv: lee el archivo .env y mete sus valores como variables de
# entorno, para que os.getenv("DATABASE_URL") los encuentre.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/, dos
# niveles por debajo de la raíz del repositorio, donde está el .env.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# ----------------------------------------------------------------------
# Las siete reglas, en un solo sitio
# ----------------------------------------------------------------------
# Cada elemento es (clave, valor, descripción). reglas_negocio.valor es
# NUMERIC(10,2): 2 se guardará como 2.00. Las claves llevan la unidad
# delante (semanas_...), como horas_recordatorio_gate; paso_huecos_min la
# lleva detrás, como duracion_visita_min, porque es el nombre fijado en el
# plan (P19). Cada descripción va en UNA sola línea: varios textos seguidos
# entre paréntesis, sin comas entre ellos, Python los PEGA en uno solo.
REGLAS = [
    # Baño: 2-3 semanas de obra (D18.7).
    ("semanas_obra_bano_min", 2,
     "Plazo orientativo de obra de un baño, mínimo en semanas (D18.7). Solo se "
     "da al cliente si no hay Gate ni cambios estructurales."),
    ("semanas_obra_bano_max", 3,
     "Plazo orientativo de obra de un baño, máximo en semanas (D18.7). Solo se "
     "da al cliente si no hay Gate ni cambios estructurales."),
    # Cocina: 2-4 semanas de obra (D18.7).
    ("semanas_obra_cocina_min", 2,
     "Plazo orientativo de obra de una cocina, mínimo en semanas (D18.7). Solo "
     "se da al cliente si no hay Gate ni cambios estructurales."),
    ("semanas_obra_cocina_max", 4,
     "Plazo orientativo de obra de una cocina, máximo en semanas (D18.7). Solo "
     "se da al cliente si no hay Gate ni cambios estructurales."),
    # Cocina: 3-6 semanas de fabricación de muebles ANTES del montaje (D18.7).
    ("semanas_fabricacion_cocina_min", 3,
     "Plazo orientativo de fabricación de los muebles de una cocina antes del "
     "montaje, mínimo en semanas (D18.7)."),
    ("semanas_fabricacion_cocina_max", 6,
     "Plazo orientativo de fabricación de los muebles de una cocina antes del "
     "montaje, máximo en semanas (D18.7)."),
    # Rejilla de huecos libres que se ofrecen ante un 409 hueco_ocupado (D31).
    ("paso_huecos_min", 30,
     "Minutos entre dos inicios de visita que se OFRECEN como huecos libres "
     "cuando el pedido está ocupado (D31). Solo para ofrecer: se puede pedir "
     "cualquier hora válida dentro de una franja."),
]

# Una sola conexión, y un cursor: el objeto con el que se envían órdenes
# SQL y se leen sus resultados.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor de esa conexión.
cur = cn.cursor()


def foto_reglas():
    """Diccionario clave -> valor de TODAS las filas de reglas_negocio."""
    # SQL: la clave y el valor de cada regla, ordenadas por clave para que
    # la foto se lea siempre en el mismo orden. Son datos de configuración
    # (números), no datos personales.
    cur.execute("SELECT clave, valor FROM reglas_negocio ORDER BY clave;")
    # dict sobre pares (clave, valor) da el diccionario clave -> valor.
    return dict(cur.fetchall())


def mostrar_estado(foto):
    """Imprime la foto de las reglas."""
    # Cuántas reglas hay.
    print(f"    reglas_negocio: {len(foto)} filas")
    # Y cada una con su valor, alineadas en columna.
    for clave, valor in foto.items():
        print(f"      {clave:35s} {valor}")


# Foto del estado ANTES de cambiar nada, para compararla con la del final.
print("=== ANTES ===")
# Se guarda para la comparación final.
antes = foto_reglas()
# Y se enseña.
mostrar_estado(antes)

# try / except: se intenta todo el bloque; si CUALQUIER paso lanza un
# error, el except deshace lo hecho (rollback) y vuelve a lanzar el error
# para que se vea. Las siete filas entran juntas o ninguna.
try:
    # enumerate(..., 1): numera las reglas desde 1 para la salida.
    for numero, (clave, valor, descripcion) in enumerate(REGLAS, 1):
        # ON CONFLICT (clave) DO NOTHING: si ya existe una fila con esa
        # clave (reglas_negocio.clave es UNIQUE), no se inserta ni se
        # sobrescribe; un valor cambiado a mano no vuelve al de aquí.
        # Los tres datos viajan como PARÁMETROS (%s), no dentro del SQL.
        # fecha_actualizacion no se nombra: su DEFAULT pone la fecha de hoy.
        cur.execute(
            """
            INSERT INTO reglas_negocio (clave, valor, descripcion)
            VALUES (%s, %s, %s)
            ON CONFLICT (clave) DO NOTHING;
            """,
            (clave, valor, descripcion),
        )
        # rowcount: filas escritas por la última orden. 1 = insertada,
        # 0 = ya existía.
        estado = "insertada" if cur.rowcount == 1 else "ya existía, no se toca"
        # Una línea por regla.
        print(f"\n  {numero}. {clave} = {valor} -> {estado}")

    # commit: hace definitivos los siete cambios a la vez.
    cn.commit()
    # Constancia en la salida.
    print("\n  commit() ejecutado")
except Exception:
    # rollback: deshace TODO lo de este try, como si no se hubiera
    # ejecutado nada.
    cn.rollback()
    # Constancia en la salida.
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    # raise vuelve a lanzar el mismo error para verlo.
    raise

# Foto del estado DESPUÉS, para compararla con la de ANTES.
print("\n=== DESPUÉS ===")
# Se guarda para la comparación.
despues = foto_reglas()
# Y se enseña.
mostrar_estado(despues)

# Comparación automática: ninguna regla que ya existía ha cambiado de
# valor ni ha desaparecido. Se recorren las claves de ANTES.
cambiadas = [clave for clave, valor in antes.items() if despues.get(clave) != valor]
# Claves nuevas: las que están DESPUÉS y no estaban ANTES (las siete en la
# primera ejecución; ninguna en las siguientes).
nuevas = sorted(set(despues) - set(antes))
# Las dos listas, o "ninguna" si están vacías.
print(f"\n  reglas que ya existían y han cambiado o desaparecido: {cambiadas or 'ninguna'}")
print(f"  reglas nuevas: {nuevas or 'ninguna'}")

# Se cierra el cursor.
cur.close()
# Y la conexión con la base de datos.
cn.close()
