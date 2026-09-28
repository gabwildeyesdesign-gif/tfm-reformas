"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN.

Reescribe el CHECK chk_decisiones_gate_coherencia de la tabla
decisiones_gate (creada en paso10), para que cada CHECK tenga UNA sola
responsabilidad (opción d de Gabi, 2026-09-28):
  - chk_decisiones_gate_decision (sin cambios): QUÉ valores existen.
  - chk_decisiones_gate_coherencia (este script): QUÉ ACOMPAÑA a cada
    decisión, sin repetir cuáles existen.

Por qué: la versión de paso10 exigía
    (decision = 'visita_acordada' AND ...) OR (decision = 'descartar' AND ...)
y así, además de la coherencia, enumeraba los valores válidos. Un valor
inválido ('continuar') incumplía las DOS restricciones, y PostgreSQL
informa de la primera por orden alfabético de nombre: "coherencia" antes
que "decision". El error nombraba una causa engañosa y
chk_decisiones_gate_decision no podía saltar nunca (comprobado con
check_migracion_decisiones_gate --quitar chk_decisiones_gate_decision:
mismo 36/37 con y sin ella).

La versión nueva:
    (decision <> 'visita_acordada' OR (visita_id IS NOT NULL AND motivo IS NULL))
    AND
    (decision <> 'descartar' OR (motivo IS NOT NULL AND visita_id IS NULL))
"A <> X OR B" se lee "si es X, entonces B". Con un valor que no es ninguno
de los dos, las dos partes se cumplen y la coherencia no protesta: solo lo
para chk_decisiones_gate_decision.

paso10 NO se modifica: es el registro de lo que se ejecutó. Una sola
transacción; idempotente comparando ANTES la definición actual.
"""

# os, sys, Path, psycopg2 y load_dotenv: los mismos usos que en paso10
# (leer el .env, tildes en la consola, hablar con PostgreSQL).
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

TABLA = "decisiones_gate"
CHECK_COHERENCIA = "chk_decisiones_gate_coherencia"

# La condición nueva, escrita UNA vez y usada en dos sitios: para saber
# cómo la normaliza Postgres (paso 1) y para crearla de verdad (paso 2).
# Solo contiene nombres fijos de este script, nunca datos de fuera.
CONDICION_NUEVA = (
    "(decision <> 'visita_acordada' OR (visita_id IS NOT NULL AND motivo IS NULL)) "
    "AND "
    "(decision <> 'descartar' OR (motivo IS NOT NULL AND visita_id IS NULL))"
)

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def definicion_actual():
    """La definición de chk_decisiones_gate_coherencia tal como la guarda Postgres, o None."""
    # pg_get_constraintdef devuelve el CHECK ya NORMALIZADO por Postgres
    # (con sus paréntesis y conversiones ::text), no el texto original.
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s;",
        (TABLA, CHECK_COHERENCIA),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


def definicion_normalizada_de_la_nueva():
    """
    Cómo escribiría Postgres la condición nueva, sin tocar decisiones_gate.

    Problema: Postgres no guarda el CHECK con el texto exacto que se le
    da; lo reescribe (añade paréntesis y ::text). Para comparar con
    fiabilidad hay que obtener la versión NORMALIZADA de la nueva.

    Solución: dentro de un SAVEPOINT (punto de guardado), se crea una
    tabla TEMPORAL con las mismas columnas y tipos que usa la condición
    (decision VARCHAR(20), motivo VARCHAR(30), visita_id INTEGER) y con
    ese CHECK; se lee cómo lo ha escrito Postgres, y se vuelve al punto de
    guardado, lo que borra la tabla temporal como si nunca hubiera
    existido. decisiones_gate no se toca.
    """
    cur.execute("SAVEPOINT normalizar;")
    try:
        # TEMP: la tabla solo existe en esta conexión y desaparece sola.
        # Aun así se deshace con el ROLLBACK TO del finally.
        cur.execute(
            f"""
            CREATE TEMP TABLE normalizar_coherencia (
                visita_id INTEGER NULL,
                decision  VARCHAR(20) NOT NULL,
                motivo    VARCHAR(30) NULL,
                CONSTRAINT c CHECK ({CONDICION_NUEVA})
            );
            """
        )
        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'normalizar_coherencia'::regclass AND conname = 'c';"
        )
        return cur.fetchone()[0]
    finally:
        # Siempre: deshacer la tabla temporal y quitar el punto de guardado.
        cur.execute("ROLLBACK TO SAVEPOINT normalizar;")
        cur.execute("RELEASE SAVEPOINT normalizar;")


print("=== ANTES ===")
antes = definicion_actual()
print(f"    {CHECK_COHERENCIA}: {antes}")

# try / except: si cualquier paso falla, rollback de TODO y se vuelve a
# lanzar el error para verlo (mismo patrón que paso10).
try:
    # ------------------------------------------------------------------
    # 0. La tabla y la restricción deben existir (paso10 aplicado)
    # ------------------------------------------------------------------
    if antes is None:
        raise RuntimeError(f"{CHECK_COHERENCIA} no existe: ¿se aplicó paso10?")

    # ------------------------------------------------------------------
    # 1. ¿Ya es la definición nueva? (idempotencia)
    # ------------------------------------------------------------------
    objetivo = definicion_normalizada_de_la_nueva()
    print(f"\n  1. definición nueva, normalizada por Postgres: {objetivo}")

    if antes == objetivo:
        print(f"  2. {CHECK_COHERENCIA} ya tiene la definición nueva: no se toca")
    else:
        # --------------------------------------------------------------
        # 2. Sustituir la restricción, con el MISMO nombre
        # --------------------------------------------------------------
        # Un CHECK no se puede editar: se borra y se vuelve a crear. Las
        # dos órdenes van en la misma transacción, así que en ningún
        # momento visible la tabla queda sin regla de coherencia.
        # ADD CONSTRAINT ... CHECK comprueba también las filas que ya
        # existen: si alguna incumpliera la regla nueva, fallaría, y el
        # except desharía también el DROP (hoy la tabla está vacía).
        cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT {CHECK_COHERENCIA};")
        cur.execute(f"ALTER TABLE {TABLA} ADD CONSTRAINT {CHECK_COHERENCIA} CHECK ({CONDICION_NUEVA});")
        print(f"  2. {CHECK_COHERENCIA} -> sustituida por la definición nueva")

    # commit: hace definitivo el cambio (si lo hubo).
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
despues = definicion_actual()
print(f"    {CHECK_COHERENCIA}: {despues}")
print(f"    ¿igual a la definición nueva? {despues == objetivo}")

cur.close()
cn.close()
