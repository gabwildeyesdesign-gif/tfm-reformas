"""
MIGRACIÓN DE DATOS DE CONFIGURACIÓN - NO ES UN SCRIPT DE VERIFICACIÓN.
PREPARADA, SIN APLICAR (PASO 1 del plan D45).

Añade a reglas_negocio DOS reglas (rama feat/n0-antelacion-visitas, plan en
docs/Plan_Antelacion_Visitas_D45.txt, secciones 2 y 4):

  dias_antelacion_visita_chat  = 2   el chat (Agente 2: tool
                                     consultar_huecos_libres y POST /visits)
                                     ofrece y acepta visitas desde el 2.º día
                                     laborable después de hoy.
  dias_antelacion_visita_admin = 1   administración (GET /huecos-libres y
                                     POST /resultados-llamada con
                                     visita_acordada u otra_hora), desde el
                                     1.er día laborable después de hoy.

main no las lee (todo el código lee reglas_negocio por clave), así que dos
filas nuevas no le cambian nada: se aplica ANTES del merge (expand). Solo
inserta filas: no cambia el esquema, y docs/schema_actual.sql no cambia.
Sin CHECK (C3 del plan: reglas_negocio no tiene ninguno; el rango 0..10 lo
valida leer_reglas_visita, con 503 si no se cumple).

Idempotente: si una fila ya existe, no se inserta ni se sobrescribe.

Enseña una FOTO de todas las reglas antes y después, y comprueba que
ninguna de las que ya existían ha cambiado.

Dos modos (P7 del plan D45; paso14 solo tenía el segundo):
  --ensayo          hace los INSERT, enseña el después y DESHACE todo
                    (ROLLBACK). Su conexión no puede confirmar: commit()
                    lanza CommitProhibido (regla de CLAUDE.md de las
                    conexiones de prueba sin commit).
  --confirmo-paso17 la migración real (commit). Solo tras la frase de Gabi
                    "confirmo la migración paso17".
Sin ninguno de los dos, no hace nada (código 2). Con los dos, tampoco: hay
que elegir.

Vuelta atrás (escrita, sin ejecutar): paso17_vuelta_atras.py.
Comprobación: scripts/check_migracion_d45.py (FALLA antes de migrar).
"""

# os: para leer variables de entorno (os.getenv).
import os
# sys: argumentos, codificación de la consola y código de salida.
import sys
# Path: para construir rutas de archivos sin escribirlas a mano.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# La clase base de las conexiones de psycopg2 (para la del ensayo).
import psycopg2.extensions
# load_dotenv: lee el archivo .env y mete sus valores como variables de
# entorno, para que os.getenv("DATABASE_URL") los encuentre.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# Qué modo se ha pedido.
ENSAYO = "--ensayo" in sys.argv
# La migración real.
REAL = "--confirmo-paso17" in sys.argv
# El freno: exactamente uno de los dos; si no, se para sin conectarse a nada.
if ENSAYO == REAL:
    # Se explica por qué no hace nada.
    print("No se ejecuta: hace falta UNO de los dos argumentos, --ensayo (deshace todo) o "
          "--confirmo-paso17 (solo tras la frase de Gabi \"confirmo la migración paso17\").")
    # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/, dos
# niveles por debajo de la raíz del repositorio, donde está el .env.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# Las dos reglas: (clave, valor, descripción). reglas_negocio.valor es
# NUMERIC(10,2): 2 se guardará como 2.00. Cada descripción va en una sola
# línea (textos seguidos entre paréntesis: Python los pega en uno).
REGLAS = [
    # D45: la antelación del chat.
    ("dias_antelacion_visita_chat", 2,
     "Antelación mínima de una visita pedida desde el chat (D45): n.º del día laborable "
     "después de hoy que es la primera fecha posible (2 = el 2.º laborable). Entre 0 y 10."),
    # D45: la antelación de administración.
    ("dias_antelacion_visita_admin", 1,
     "Antelación mínima de una visita que acuerda administración (D45): n.º del día laborable "
     "después de hoy que es la primera fecha posible (1 = el 1.er laborable). Entre 0 y 10."),
]


class CommitProhibido(Exception):
    """Un commit() en el ensayo: nunca debe pasar."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """La conexión del ensayo: su commit() lanza CommitProhibido en vez de
    confirmar (regla de CLAUDE.md desde el 2026-10-08)."""

    def commit(self):
        # En vez de confirmar, el error.
        raise CommitProhibido("commit() prohibido en el ensayo de paso17")


# Una sola conexión: la del ensayo no puede confirmar.
cn = psycopg2.connect(os.getenv("DATABASE_URL"), connection_factory=ConexionSinCommit if ENSAYO else None)
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


def comparar(antes, despues):
    """Imprime qué reglas existentes han cambiado y cuáles son nuevas."""
    # Ninguna regla que ya existía ha cambiado de valor ni ha desaparecido.
    cambiadas = [clave for clave, valor in antes.items() if despues.get(clave) != valor]
    # Las claves nuevas (las dos la primera vez; ninguna en las siguientes).
    nuevas = sorted(set(despues) - set(antes))
    # Las dos listas, o "ninguna".
    print(f"\n  reglas que ya existían y han cambiado o desaparecido: {cambiadas or 'ninguna'}")
    # Las nuevas.
    print(f"  reglas nuevas: {nuevas or 'ninguna'}")


# El modo, en la primera línea de la salida.
print(f"=== paso17 en modo {'ENSAYO (se deshace todo)' if ENSAYO else 'REAL (commit)'} ===")
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
        # SQL: INSERT de la regla. ON CONFLICT (clave) DO NOTHING: si la
        # clave ya existe (UNIQUE), no se inserta ni se sobrescribe. Los
        # datos van como parámetros (%s).
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
    # Ensayo: lo que quedaría, visto DENTRO de la transacción, y ROLLBACK.
    if ENSAYO:
        # La foto con los INSERT aún sin confirmar.
        print("\n=== DESPUÉS (dentro del ensayo, sin confirmar) ===")
        # Se guarda.
        despues = foto_reglas()
        # Se enseña.
        mostrar_estado(despues)
        # Y se compara.
        comparar(antes, despues)
        # Se deshace todo.
        cn.rollback()
        # Constancia en la salida.
        print("\n  ENSAYO: rollback() ejecutado, no se ha cambiado nada")
    # Real: commit único.
    else:
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

# Foto final, fuera de la transacción: en el ensayo tiene que ser igual a
# la de antes; en la real, con las dos filas nuevas.
print("\n=== FINAL (fuera de la transacción) ===")
# Se guarda para la comparación.
final = foto_reglas()
# Y se enseña.
mostrar_estado(final)
# La comparación con el antes.
comparar(antes, final)
# En el ensayo, nada puede haber quedado escrito.
if ENSAYO and final != antes:
    # FALLO explícito.
    print("\n  FALLO: el ensayo ha dejado cambios en reglas_negocio")
    # Se cierra la conexión.
    cn.close()
    # Código 1.
    sys.exit(1)
# Se cierra la lectura (rollback de la transacción de la foto: no hay commit).
cn.rollback()
# Se cierra el cursor.
cur.close()
# Y la conexión.
cn.close()
