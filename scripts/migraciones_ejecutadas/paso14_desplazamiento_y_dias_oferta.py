"""
MIGRACIÓN DE DATOS DE CONFIGURACIÓN - NO ES UN SCRIPT DE VERIFICACIÓN.
PREPARADA, SIN APLICAR (Bloque B1 del plan D32).

Añade a reglas_negocio DOS reglas (rama feat/n0-horario-visitas, plan en
docs/Plan_Horario_Visitas_D32.txt, secciones 2.1, 5 y 11):

  desplazamiento_min = 15   minutos entre el fin de una visita y el inicio
                            de la siguiente (D23.5). Con la duración (60),
                            da la rejilla 08:30, 09:45, 11:00, 12:15, 17:00
                            y 18:15.
  dias_oferta_visita = 10   días laborables CON hueco que ofrece el modo
                            días de GET /huecos-libres (punto 7a del plan).

main no las lee (todo el código lee reglas_negocio por clave), así que dos
filas nuevas no le cambian nada: se aplica en la rama, como paso13. Solo
inserta filas: no cambia el esquema, y docs/schema_actual.sql no cambia.

Idempotente: si una fila ya existe, no se inserta ni se sobrescribe.

Enseña una FOTO de todas las reglas antes y después, y comprueba que
ninguna de las que ya existían ha cambiado.

Freno: sin el argumento --confirmo-paso14 no hace nada (código 2). Solo tras
la frase de Gabi "confirmo la migración paso14".

Vuelta atrás (escrita, sin ejecutar): paso14_vuelta_atras.py.
Comprobación: scripts/check_migracion_d32.py (FALLA antes de migrar).
"""

# os: para leer variables de entorno (os.getenv).
import os
# sys: argumentos, codificación de la consola y código de salida.
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

# El freno: sin la bandera, se para aquí sin conectarse a nada.
if "--confirmo-paso14" not in sys.argv:
    # Se explica por qué no hace nada.
    print("No se ejecuta: falta --confirmo-paso14 (solo tras la frase de Gabi "
          "\"confirmo la migración paso14\").")
    # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/, dos
# niveles por debajo de la raíz del repositorio, donde está el .env.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# Las dos reglas: (clave, valor, descripción). reglas_negocio.valor es
# NUMERIC(10,2): 15 se guardará como 15.00. Cada descripción va en una sola
# línea (textos seguidos entre paréntesis: Python los pega en uno).
REGLAS = [
    # D23.5: el desplazamiento entre dos visitas.
    ("desplazamiento_min", 15,
     "Minutos de desplazamiento entre el fin de una visita y el inicio de la "
     "siguiente (D23.5, D32). Con duracion_visita_min da la rejilla de horas de inicio."),
    # D32: cuántos días con hueco ofrece la consulta.
    ("dias_oferta_visita", 10,
     "Días laborables con al menos una hora libre que ofrece GET /huecos-libres "
     "en su modo días (D32), desde hoy."),
]

# Una sola conexión y su cursor.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor de esa conexión.
cur = cn.cursor()


def foto_reglas():
    """Diccionario clave -> valor de TODAS las filas de reglas_negocio."""
    # SQL: la clave y el valor de cada regla, ordenadas por clave.
    cur.execute("SELECT clave, valor FROM reglas_negocio ORDER BY clave;")
    # dict sobre pares (clave, valor) da el diccionario clave -> valor.
    return dict(cur.fetchall())


def mostrar_estado(foto):
    """Imprime la foto de las reglas."""
    # Cuántas reglas hay.
    print(f"    reglas_negocio: {len(foto)} filas")
    # Y cada una con su valor, alineadas en columna.
    for clave, valor in foto.items():
        # Una línea por regla.
        print(f"      {clave:35s} {valor}")


# Foto ANTES de cambiar nada.
print("=== ANTES ===")
# Se guarda para la comparación final.
antes = foto_reglas()
# Y se enseña.
mostrar_estado(antes)

# try / except: las dos filas entran juntas o ninguna.
try:
    # enumerate(..., 1): numera las reglas desde 1 para la salida.
    for numero, (clave, valor, descripcion) in enumerate(REGLAS, 1):
        # ON CONFLICT (clave) DO NOTHING: si la clave ya existe (UNIQUE), no
        # se inserta ni se sobrescribe. Los datos van como parámetros (%s).
        cur.execute(
            """
            INSERT INTO reglas_negocio (clave, valor, descripcion)
            VALUES (%s, %s, %s)
            ON CONFLICT (clave) DO NOTHING;
            """,
            (clave, valor, descripcion),
        )
        # rowcount: 1 = insertada, 0 = ya existía.
        estado = "insertada" if cur.rowcount == 1 else "ya existía, no se toca"
        # Una línea por regla.
        print(f"\n  {numero}. {clave} = {valor} -> {estado}")
    # commit: hace definitivos los cambios a la vez.
    cn.commit()
    # Constancia en la salida.
    print("\n  commit() ejecutado")
# Cualquier error: deshacer todo y volver a lanzarlo para verlo.
except Exception:
    # Nada cambiado.
    cn.rollback()
    # Constancia en la salida.
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    # Y se ve el error original.
    raise

# Foto DESPUÉS.
print("\n=== DESPUÉS ===")
# Se guarda para la comparación.
despues = foto_reglas()
# Y se enseña.
mostrar_estado(despues)

# Ninguna regla que ya existía ha cambiado de valor ni ha desaparecido.
cambiadas = [clave for clave, valor in antes.items() if despues.get(clave) != valor]
# Las claves nuevas (las dos la primera vez; ninguna en las siguientes).
nuevas = sorted(set(despues) - set(antes))
# Las dos listas, o "ninguna".
print(f"\n  reglas que ya existían y han cambiado o desaparecido: {cambiadas or 'ninguna'}")
# Las nuevas.
print(f"  reglas nuevas: {nuevas or 'ninguna'}")

# Se cierra el cursor.
cur.close()
# Y la conexión.
cn.close()
