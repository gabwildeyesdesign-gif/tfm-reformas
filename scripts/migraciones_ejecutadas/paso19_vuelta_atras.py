"""
VUELTA ATRÁS DE paso19 (D56, categoría 'pintura'). ESCRITA, SIN EJECUTAR.

Deshace paso19 en UNA transacción:
  1. Borra la fila de pintura de umbrales_gate y las 3 de tarifas_base
     (solo las de tipo_reforma = 'pintura': nada más).
  2. Vuelve a dejar los tres CHECK con las 4 categorías de antes.

Si alguna oportunidad tiene tipo_reforma = 'pintura', PARA antes de tocar
nada: el CHECK de 4 categorías no se podría volver a poner sin cambiar o
borrar esas oportunidades, y eso lo decide Gabi, no este script.

Igual que la migración: primero se para el código que lee 'pintura'
(TipoReforma.PINTURA) o se acepta que get_business_rules ya no la devuelva.

Dos modos:
  --ensayo                       hace todo y DESHACE (ROLLBACK); su conexión
                                 no puede confirmar (ConexionSinCommit).
  --confirmo-vuelta-atras-paso19 la vuelta atrás real (commit), solo tras la
                                 frase de Gabi.
Sin ninguno de los dos, o con los dos, no hace nada (código 2).
"""

# os: leer DATABASE_URL del entorno.
import os
# sys: argumentos, salida en UTF-8 y código de salida.
import sys
# Path: la ruta del .env a partir de este archivo.
from pathlib import Path

# psycopg2: la conexión a PostgreSQL.
import psycopg2
# La clase base de las conexiones, para la del ensayo.
import psycopg2.extensions
# load_dotenv: carga el .env sin enseñar ningún valor.
from dotenv import load_dotenv

# Salida en UTF-8 (tildes en Windows).
sys.stdout.reconfigure(encoding="utf-8")

# El ensayo.
ENSAYO = "--ensayo" in sys.argv
# La vuelta atrás real.
REAL = "--confirmo-vuelta-atras-paso19" in sys.argv
# Exactamente uno de los dos.
if ENSAYO == REAL:
    # Por qué no hace nada.
    print("No se ejecuta: hace falta UNO de los dos argumentos, --ensayo o --confirmo-vuelta-atras-paso19.")
    # Código 2: nada hecho a propósito.
    sys.exit(2)

# La raíz del repositorio (dos niveles por encima de este archivo).
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env.
load_dotenv(RAIZ_REPO / ".env")

# Las cuatro categorías de antes de D56.
LISTA_ANTES = "'bano', 'cocina', 'integral_vivienda', 'parcial_acabados'"
# Los tres CHECK: (tabla, nombre, condición de antes de D56).
CHECKS = (
    # oportunidades admite NULL.
    ("oportunidades", "chk_oportunidades_tipo_reforma", f"tipo_reforma IN ({LISTA_ANTES}) OR tipo_reforma IS NULL"),
    # tarifas_base.
    ("tarifas_base", "tarifas_base_tipo_reforma_check", f"tipo_reforma IN ({LISTA_ANTES})"),
    # umbrales_gate.
    ("umbrales_gate", "chk_umbrales_gate_tipo_reforma", f"tipo_reforma IN ({LISTA_ANTES})"),
)


class CommitProhibido(Exception):
    """Un commit() en el ensayo: nunca debe pasar."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """La conexión del ensayo: su commit() lanza CommitProhibido."""

    def commit(self):
        # En vez de confirmar, el error.
        raise CommitProhibido("commit() prohibido en el ensayo de la vuelta atrás de paso19")


# La conexión (la del ensayo no puede confirmar).
cn = psycopg2.connect(os.getenv("DATABASE_URL"), connection_factory=ConexionSinCommit if ENSAYO else None)
# Su cursor.
cur = cn.cursor()
# El modo, en la primera línea.
print(f"=== vuelta atrás de paso19 en modo {'ENSAYO (se deshace todo)' if ENSAYO else 'REAL (commit)'} ===")

# try / except: o todo o nada.
try:
    # SQL: esperar como mucho 5 s un bloqueo de otro proceso.
    cur.execute("SET LOCAL lock_timeout = '5s';")
    # SQL: cuántas oportunidades usan 'pintura'.
    cur.execute("SELECT COUNT(*) FROM oportunidades WHERE tipo_reforma = 'pintura';")
    # El número.
    en_uso = cur.fetchone()[0]
    # Si alguna la usa, se para sin tocar nada.
    if en_uso:
        # Lo decide Gabi.
        raise RuntimeError(f"{en_uso} oportunidades tienen tipo_reforma = 'pintura': no se puede volver a 4 categorías")
    # SQL: borra SOLO la fila de pintura de umbrales_gate.
    cur.execute("DELETE FROM umbrales_gate WHERE tipo_reforma = 'pintura';")
    # Cuántas (0 o 1).
    print(f"  umbrales_gate: {cur.rowcount} fila(s) de pintura borrada(s)")
    # SQL: borra SOLO las filas de pintura de tarifas_base.
    cur.execute("DELETE FROM tarifas_base WHERE tipo_reforma = 'pintura';")
    # Cuántas (0 a 3).
    print(f"  tarifas_base: {cur.rowcount} fila(s) de pintura borrada(s)")
    # Los tres CHECK, a su forma de antes.
    for tabla, nombre, condicion in CHECKS:
        # SQL: quitar y volver a poner, en UNA orden.
        cur.execute(f"ALTER TABLE {tabla} DROP CONSTRAINT {nombre}, ADD CONSTRAINT {nombre} CHECK ({condicion});")
        # Constancia.
        print(f"  {nombre}: vuelve a las 4 categorías")
    # Ensayo: se deshace.
    if ENSAYO:
        # ROLLBACK.
        cn.rollback()
        # Constancia.
        print("  ENSAYO: rollback() ejecutado, no se ha cambiado nada")
    # Real: un solo commit.
    else:
        # Definitivo.
        cn.commit()
        # Constancia.
        print("  commit() ejecutado")
# Cualquier error: deshacer y enseñarlo.
except Exception:
    # Nada cambiado.
    cn.rollback()
    # Constancia.
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    # El error original.
    raise
# Siempre se cierra.
finally:
    # El cursor.
    cur.close()
    # Y la conexión.
    cn.close()
