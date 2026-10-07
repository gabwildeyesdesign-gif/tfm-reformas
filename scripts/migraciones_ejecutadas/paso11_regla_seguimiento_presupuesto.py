"""
MIGRACIÓN DE DATOS DE CONFIGURACIÓN - NO ES UN SCRIPT DE VERIFICACIÓN.

Añade a reglas_negocio la regla del seguimiento de 48 h (rama
feat/n0-listado-wf3, plan en docs/Plan_Endpoint_Listado_WF3.txt, sección 2;
decisiones D13 y D25.7, P6 del plan):

  horas_seguimiento_presupuesto = 48

La leerá GET /llamadas-del-dia en cada llamada, junto con
horas_recordatorio_gate (paso10): una oportunidad en presupuesto_enviado,
sin visita y sin ningún contacto registrado, entra en la lista de WF3
como "seguimiento por abrir" cuando su presupuesto se creó hace más de
estas horas. Al estar en la tabla y no en el código, se puede cambiar sin
tocar el programa.

No cambia el esquema (ni tablas, ni columnas, ni restricciones): solo
inserta UNA fila. Es puramente ADITIVA, así que no hace falta partirla en
expandir y contraer, y docs/schema_actual.sql no cambia (no guarda filas).

Es idempotente: si la fila ya existe, no se inserta ni se sobrescribe, así
que ejecutarla dos veces deja la base de datos igual que una.

Enseña una FOTO de todas las reglas antes y después, y comprueba que
ninguna de las que ya existían ha cambiado.
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
load_dotenv(RAIZ_REPO / ".env")

# ----------------------------------------------------------------------
# La regla, en un solo sitio
# ----------------------------------------------------------------------
# Nombre decidido en P6 del plan: mismo patrón que horas_recordatorio_gate
# (la unidad delante) y dice desde qué se cuenta.
CLAVE_REGLA = "horas_seguimiento_presupuesto"
# 48 horas (D13, D25.7). reglas_negocio.valor es NUMERIC(10,2): se
# guardará como 48.00.
VALOR_REGLA = 48
# La descripción va en UNA sola línea, sin saltos ni sangría. Varios textos
# seguidos entre paréntesis, sin comas entre ellos, Python los PEGA en uno
# solo al leer el archivo (cada trozo termina en un espacio para que las
# palabras no queden pegadas). Así el código se lee cómodo sin meter ningún
# salto de línea en el texto que se guarda.
DESCRIPCION_REGLA = (
    "Horas desde la creación del presupuesto (presupuestos.created_at) "
    "que puede estar una oportunidad en presupuesto_enviado, sin visita y "
    "sin ningún contacto registrado (fecha_ultimo_contacto NULL), antes de "
    "que la lista diaria de llamadas de WF3 (GET /llamadas-del-dia) la "
    "incluya como seguimiento por abrir (D13, D25.7). Se lee en cada "
    "llamada; no está escrita en el código."
)

# Una sola conexión, y un cursor: el objeto con el que se envían órdenes
# SQL y se leen sus resultados.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
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
    """Imprime la foto de las reglas y la descripción de la regla nueva."""
    # Cuántas reglas hay y cada una con su valor.
    print(f"    reglas_negocio: {len(foto)} filas")
    for clave, valor in foto.items():
        print(f"      {clave:35s} {valor}")
    # SQL: la descripción de la regla nueva (None si todavía no existe).
    cur.execute("SELECT descripcion FROM reglas_negocio WHERE clave = %s;", (CLAVE_REGLA,))
    fila = cur.fetchone()
    # repr() enseña el texto con sus caracteres especiales visibles: si
    # hubiera un salto de línea, se vería como \n.
    print(f"    descripción de {CLAVE_REGLA}: {fila[0]!r}" if fila else f"    {CLAVE_REGLA}: no existe")


# Foto del estado ANTES de cambiar nada, para compararla con la del final.
print("=== ANTES ===")
antes = foto_reglas()
mostrar_estado(antes)

# try / except: se intenta todo el bloque; si CUALQUIER paso lanza un
# error, el except deshace lo hecho (rollback) y vuelve a lanzar el error
# para que se vea.
try:
    # ON CONFLICT (clave) DO NOTHING: si ya existe una fila con esa clave
    # (reglas_negocio.clave es UNIQUE), no se inserta ni se sobrescribe. Un
    # valor cambiado a mano por administración (por ejemplo, 72) no vuelve
    # a 48 al repetir este script.
    # La descripción viaja como PARÁMETRO (%s), no escrita dentro del SQL:
    # psycopg2 la envía tal cual, sin saltos de línea añadidos.
    # fecha_actualizacion no se nombra: su DEFAULT CURRENT_DATE pone la
    # fecha de hoy.
    cur.execute(
        """
        INSERT INTO reglas_negocio (clave, valor, descripcion)
        VALUES (%s, %s, %s)
        ON CONFLICT (clave) DO NOTHING;
        """,
        (CLAVE_REGLA, VALOR_REGLA, DESCRIPCION_REGLA),
    )
    # rowcount: filas escritas por la última orden. 1 = insertada, 0 = ya existía.
    print(f"\n  1. {CLAVE_REGLA} = {VALOR_REGLA} -> "
          f"{'insertada' if cur.rowcount == 1 else 'ya existía, no se toca'}")

    # commit: hace definitivo el cambio.
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    # rollback: deshace TODO lo de este try, como si no se hubiera
    # ejecutado nada. raise vuelve a lanzar el mismo error para verlo.
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

# Foto del estado DESPUÉS, para compararla con la de ANTES.
print("\n=== DESPUÉS ===")
despues = foto_reglas()
mostrar_estado(despues)

# Comparación automática: ninguna regla que ya existía ha cambiado de
# valor ni ha desaparecido. Se recorren las claves de ANTES.
cambiadas = [clave for clave, valor in antes.items() if despues.get(clave) != valor]
# Claves nuevas: las que están DESPUÉS y no estaban ANTES (una sola, la
# nueva, en la primera ejecución; ninguna en las siguientes).
nuevas = sorted(set(despues) - set(antes))
print(f"\n  reglas que ya existían y han cambiado o desaparecido: {cambiadas or 'ninguna'}")
print(f"  reglas nuevas: {nuevas or 'ninguna'}")

# Se cierran el cursor y la conexión con la base de datos.
cur.close()
cn.close()
