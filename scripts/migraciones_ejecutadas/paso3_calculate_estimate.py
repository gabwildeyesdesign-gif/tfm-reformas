"""
MIGRACIÓN DE ESQUEMA YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACIÓN.

Ejecutada el 2026-09-19 contra la base de datos real, como Bloque 1 de
POST /calculate-estimate. Se conserva aquí como EVIDENCIA de cómo cambió
el esquema, no para lanzarla de forma rutinaria. (Mismo criterio que
paso2b_insert.py: scripts/ es verificación repetible, y esta carpeta
guarda cambios de un solo uso.)

Qué cambia, y por qué:

  1. oportunidades_estado_check pasa de 6 a 7 valores: se añade
     'pendiente_aprobacion'. Es el estado en el que queda una
     oportunidad cuyo presupuesto ha activado el Gate HITL y espera la
     decisión de una persona. Antes no existía, y 'presupuesto_enviado'
     no servía: ese estado significa que el cliente YA tiene su
     presupuesto, y con el Gate activo todavía no lo tiene. Decisión D8
     en docs/TFM_Decisiones_Modelo_Datos_Leads.txt.

  2. presupuestos gana UNIQUE (oportunidad_id): como mucho un
     presupuesto por oportunidad. Lo exige la idempotencia del endpoint.
     Si el agente de IA llama dos veces a la tool, o n8n reintenta tras
     un error, la segunda llamada no puede crear un segundo presupuesto
     (ni un segundo aviso de aprobación). La sentencia del servicio,
     INSERT ... ON CONFLICT (oportunidad_id) DO NOTHING, ni siquiera se
     puede ejecutar sin esta restricción: Postgres responde
     InvalidColumnReference (comprobado ejecutando
     scripts/check_migracion_calculate_estimate.py antes de migrar).

Es idempotente: si ya se aplicó, detecta que no hay nada que hacer y
termina sin tocar nada.

Verificación: scripts/check_migracion_calculate_estimate.py (antes de
migrar: 9/14; después: debe dar 14/14).
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# Este archivo vive DOS niveles por debajo de la raíz del repositorio
# (scripts/migraciones_ejecutadas/), así que se sube con parents[2],
# igual que paso2b_insert.py.
RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

NOMBRE_CHECK_ESTADO = "oportunidades_estado_check"
NOMBRE_UNIQUE_PRESUPUESTO = "presupuestos_oportunidad_id_key"
ESTADO_NUEVO = "pendiente_aprobacion"


def definicion_restriccion(cur, tabla, nombre):
    """
    Devuelve la definición de una restricción tal como la guarda
    Postgres, o None si no existe.

    pg_constraint es el catálogo interno donde Postgres apunta todas las
    restricciones (CHECK, UNIQUE, claves primarias y foráneas).
    '%s'::regclass convierte el nombre de la tabla en su identificador
    interno, que es lo que guarda la columna conrelid.
    pg_get_constraintdef() reconstruye el texto de la restricción, por
    ejemplo "UNIQUE (oportunidad_id)".
    """
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = %s::regclass AND conname = %s;
        """,
        (tabla, nombre),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

print("=== ANTES ===")
check_antes = definicion_restriccion(cur, "oportunidades", NOMBRE_CHECK_ESTADO)
unique_antes = definicion_restriccion(cur, "presupuestos", NOMBRE_UNIQUE_PRESUPUESTO)
print(f"  {NOMBRE_CHECK_ESTADO}: {check_antes}")
print(f"  {NOMBRE_UNIQUE_PRESUPUESTO}: {unique_antes}")

# Cada cambio se aplica solo si hace falta. Postgres no tiene
# "ADD CONSTRAINT IF NOT EXISTS" para restricciones de tabla (ya se
# comprobó en check_tipo_reforma_constraint.py), así que la comprobación
# se hace aquí, en Python, leyendo el catálogo.
falta_estado = check_antes is None or f"'{ESTADO_NUEVO}'" not in check_antes
falta_unique = unique_antes is None

try:
    if falta_estado:
        # POR QUÉ DROP + ADD, Y LAS DOS EN LA MISMA SENTENCIA
        #
        # Postgres no permite editar un CHECK existente: hay que borrarlo
        # (DROP CONSTRAINT) y crearlo de nuevo con la lista completa
        # (ADD CONSTRAINT). Es el mismo patrón con el que se creó
        # chk_oportunidades_tipo_reforma.
        #
        # Las dos acciones van en UN solo ALTER TABLE, separadas por una
        # coma, en vez de en dos sentencias. Postgres las aplica juntas,
        # así que no existe ni un instante en que la columna estado se
        # quede sin restricción y otro proceso pueda colar un valor
        # inválido.
        #
        # Se conserva el mismo nombre (oportunidades_estado_check) para
        # que el esquema volcado siga siendo comparable con el anterior,
        # y para que ningún script que lo busque por nombre se rompa.
        #
        # Al crear el CHECK, Postgres comprueba TODAS las filas que ya
        # existen. Si alguna tuviera un estado fuera de la lista, el
        # ALTER TABLE fallaría entero. Aquí no puede ocurrir: la lista
        # nueva contiene todos los valores de la antigua.
        cur.execute(
            """
            ALTER TABLE oportunidades
                DROP CONSTRAINT oportunidades_estado_check,
                ADD CONSTRAINT oportunidades_estado_check CHECK (estado IN (
                    'nueva',
                    'cualificada',
                    'pendiente_aprobacion',
                    'visita_agendada',
                    'presupuesto_enviado',
                    'ganada',
                    'perdida'
                ));
            """
        )
        print(f"\n  ALTER TABLE oportunidades: CHECK rehecho con '{ESTADO_NUEVO}'")
    else:
        print(f"\n  El CHECK ya incluía '{ESTADO_NUEVO}': no se toca")

    if falta_unique:
        # UNIQUE (oportunidad_id) prohíbe que dos filas de presupuestos
        # tengan la misma oportunidad_id. Para poder comprobarlo rápido,
        # Postgres crea él solo un índice sobre esa columna (se verá en
        # el esquema como presupuestos_oportunidad_id_key). Ese índice es
        # el que usa ON CONFLICT (oportunidad_id) para detectar el
        # conflicto.
        #
        # Si ya hubiera dos presupuestos con la misma oportunidad, el
        # ALTER TABLE fallaría. Comprobado antes de escribir esta
        # migración: presupuestos tenía 0 filas.
        cur.execute(
            f"""
            ALTER TABLE presupuestos
                ADD CONSTRAINT {NOMBRE_UNIQUE_PRESUPUESTO} UNIQUE (oportunidad_id);
            """
        )
        print("  ALTER TABLE presupuestos: UNIQUE (oportunidad_id) añadido")
    else:
        print("  El UNIQUE ya existía: no se toca")

    # POR QUÉ UN SOLO commit() PARA LAS DOS
    #
    # En Postgres, a diferencia de otros motores como MySQL u Oracle,
    # los ALTER TABLE forman parte de la transacción igual que un INSERT:
    # no se confirman solos. Con un único commit al final, se aplican los
    # dos cambios o ninguno. No puede quedar el esquema a medias (el
    # estado nuevo creado pero sin UNIQUE), que dejaría el endpoint roto
    # de una forma difícil de ver.
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    # Si cualquiera de los dos cambios falla, se deshacen los dos y se
    # relanza el error para que se vea en la consola.
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
print(f"  {NOMBRE_CHECK_ESTADO}: {definicion_restriccion(cur, 'oportunidades', NOMBRE_CHECK_ESTADO)}")
print(f"  {NOMBRE_UNIQUE_PRESUPUESTO}: {definicion_restriccion(cur, 'presupuestos', NOMBRE_UNIQUE_PRESUPUESTO)}")

cur.close()
cn.close()
