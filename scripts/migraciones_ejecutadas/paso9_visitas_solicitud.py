"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN.

Prepara la tabla visitas para POST /visits (rama feat/n0-visits, plan en
docs/Plan_Endpoint_Visits.txt). Una visita es una SOLICITUD del cliente
desde el chat, no una reserva: administración llama siempre para
confirmar. Todo en UNA transacción: o se aplica entero o nada.

  1. Estados: 'reservada' se sustituye por 'solicitada' en el CHECK
     visitas_estado_check y en el DEFAULT de la columna estado. Estados
     finales: solicitada, confirmada, completada, cancelada.
  2. Columnas nuevas:
       texto_cliente TEXT NOT NULL, con CHECK de 1 a 1000 caracteres
       (chk_visitas_texto_cliente_longitud; defensa doble con
       MAX_TEXTO_VISITA de Pydantic, decisión P3 del plan).
       updated_at TIMESTAMPTZ NOT NULL DEFAULT now() (como oportunidades).
  3. fecha_propuesta pasa a NOT NULL (P3): una visita sin fecha no tiene
     sentido.
  4. Índice único PARCIAL visitas_una_activa_por_oportunidad: como mucho
     una visita activa (solicitada o confirmada) por oportunidad.
  5. Cinco filas nuevas en reglas_negocio, en minutos desde medianoche,
     hora de Madrid: franjas de mañana (08:30-13:30) y tarde (17:00-20:00)
     y duración de la visita (60 min).

ESTADO DE LOS DATOS AL ESCRIBIR ESTE SCRIPT (consultado antes, 2026-09-26):
  - visitas: 0 filas.
  - El código de main no usa 'reservada' en ningún sitio. Por eso esta
    migración, que RETIRA un valor del CHECK, no se parte en expandir y
    contraer: la regla del proyecto solo lo exige cuando main usa lo que
    se retira.
  - Ninguna de las cinco claves nuevas existe en reglas_negocio.

Es idempotente: cada paso comprueba primero si ya está hecho, así que
ejecutarlo dos veces deja la base de datos igual que una.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/, dos
# niveles por debajo de la raíz del repositorio.
RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

RESTRICCION_ESTADO = "visitas_estado_check"
RESTRICCION_TEXTO = "chk_visitas_texto_cliente_longitud"
INDICE_ACTIVA = "visitas_una_activa_por_oportunidad"
MAX_TEXTO_VISITA = 1000  # el mismo valor que app/schemas/common.py

# Las cinco reglas nuevas: (clave, valor, descripción). Los valores van en
# MINUTOS DESDE MEDIANOCHE para que quepan en reglas_negocio.valor, que es
# un número (NUMERIC(10,2)), no una hora. 510 = 8*60 + 30 = las 08:30.
REGLAS_VISITA = [
    ("visita_manana_inicio_min", 510, "Inicio de la franja de visitas de mañana, en minutos desde medianoche, hora de Madrid (510 = 08:30)"),
    ("visita_manana_fin_min", 810, "Fin de la franja de visitas de mañana, en minutos desde medianoche, hora de Madrid (810 = 13:30). La visita debe TERMINAR como muy tarde a esta hora"),
    ("visita_tarde_inicio_min", 1020, "Inicio de la franja de visitas de tarde, en minutos desde medianoche, hora de Madrid (1020 = 17:00)"),
    ("visita_tarde_fin_min", 1200, "Fin de la franja de visitas de tarde, en minutos desde medianoche, hora de Madrid (1200 = 20:00). La visita debe TERMINAR como muy tarde a esta hora"),
    ("duracion_visita_min", 60, "Duración de una visita técnica, en minutos. Con las franjas decide qué horas de inicio son válidas"),
]

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def definicion_restriccion(nombre):
    """Devuelve la definición de una restricción (su CHECK), o None si no existe."""
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'visitas'::regclass AND conname = %s;",
        (nombre,),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


def definicion_indice(nombre):
    """Devuelve el CREATE INDEX completo de un índice, o None si no existe."""
    cur.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s;",
        (nombre,),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


def columnas_visitas():
    """Columnas de visitas: nombre -> (tipo, admite NULL, valor por defecto)."""
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'visitas'
        ORDER BY ordinal_position;
        """
    )
    return {f[0]: f[1:] for f in cur.fetchall()}


def mostrar_estado():
    for nombre, datos in columnas_visitas().items():
        print(f"    {nombre:16} {datos}")
    print(f"    {RESTRICCION_ESTADO}: {definicion_restriccion(RESTRICCION_ESTADO)}")
    print(f"    {RESTRICCION_TEXTO}: {definicion_restriccion(RESTRICCION_TEXTO)}")
    print(f"    {INDICE_ACTIVA}: {definicion_indice(INDICE_ACTIVA)}")
    cur.execute(
        "SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s) ORDER BY clave;",
        ([r[0] for r in REGLAS_VISITA],),
    )
    print(f"    reglas de visita: {cur.fetchall()}")


print("=== ANTES ===")
mostrar_estado()

try:
    # ------------------------------------------------------------------
    # 0. Comprobación de seguridad: la tabla debe estar vacía
    # ------------------------------------------------------------------
    # Solo importa si la migración NO se ha aplicado todavía (el CHECK
    # sigue admitiendo 'reservada'). Si ya se aplicó, puede haber visitas
    # legítimas creadas por POST /visits, y volver a ejecutar el script no
    # debe bloquearse por ellas: todos los pasos siguientes son "si falta,
    # créalo", así que no las tocan.
    cur.execute("SELECT count(*) FROM visitas;")
    filas = cur.fetchone()[0]
    check_actual = definicion_restriccion(RESTRICCION_ESTADO) or ""
    if filas > 0 and "reservada" in check_actual:
        print(f"\n  ABORTADO: visitas tiene {filas} filas y la migración no está aplicada.")
        print("  Qué hacer con esas filas (¿'reservada' pasa a 'solicitada'?) se decide antes, no aquí.")
        raise RuntimeError("visitas no está vacía")
    print(f"\n  0. visitas: {filas} filas; CHECK con 'reservada': {'reservada' in check_actual} -> se puede continuar")

    # ------------------------------------------------------------------
    # 1a. CHECK de estados: 'reservada' -> 'solicitada'
    # ------------------------------------------------------------------
    # Un CHECK no se puede editar: se borra y se vuelve a crear con el
    # mismo nombre. Solo se hace si todavía no admite 'solicitada', para
    # que una segunda ejecución no toque nada.
    if "solicitada" not in check_actual:
        cur.execute(f"ALTER TABLE visitas DROP CONSTRAINT IF EXISTS {RESTRICCION_ESTADO};")
        cur.execute(
            f"""
            ALTER TABLE visitas ADD CONSTRAINT {RESTRICCION_ESTADO}
            CHECK (estado IN ('solicitada', 'confirmada', 'completada', 'cancelada'));
            """
        )
        print(f"  1a. {RESTRICCION_ESTADO} -> recreado con 'solicitada'")
    else:
        print(f"  1a. {RESTRICCION_ESTADO} ya admite 'solicitada': no se toca")

    # ------------------------------------------------------------------
    # 1b. DEFAULT de estado
    # ------------------------------------------------------------------
    # SET DEFAULT sustituye el valor por defecto; repetirlo no cambia nada.
    # Sin este paso, un INSERT que no diga el estado intentaría escribir
    # 'reservada', que el CHECK nuevo ya no admite, y fallaría.
    cur.execute("ALTER TABLE visitas ALTER COLUMN estado SET DEFAULT 'solicitada';")
    print("  1b. DEFAULT de estado -> 'solicitada'")

    # ------------------------------------------------------------------
    # 2a. texto_cliente TEXT NOT NULL
    # ------------------------------------------------------------------
    # NOT NULL sin valor por defecto: con la tabla vacía no hay filas a las
    # que dar un valor. Si hubiera filas, Postgres rechazaría el ALTER, y
    # el paso 0 ya lo habría impedido antes.
    cur.execute("ALTER TABLE visitas ADD COLUMN IF NOT EXISTS texto_cliente TEXT NOT NULL;")
    print("  2a. texto_cliente -> columna asegurada")

    # ------------------------------------------------------------------
    # 2b. CHECK de longitud de texto_cliente
    # ------------------------------------------------------------------
    # char_length cuenta CARACTERES, no bytes: "á" cuenta 1. Es la misma
    # medida que usa Pydantic con max_length, así que las dos defensas
    # dicen exactamente lo mismo.
    if definicion_restriccion(RESTRICCION_TEXTO) is None:
        cur.execute(
            f"""
            ALTER TABLE visitas ADD CONSTRAINT {RESTRICCION_TEXTO}
            CHECK (char_length(texto_cliente) BETWEEN 1 AND {MAX_TEXTO_VISITA});
            """
        )
        print(f"  2b. {RESTRICCION_TEXTO} -> creado")
    else:
        print(f"  2b. {RESTRICCION_TEXTO} ya existía: no se toca")

    # ------------------------------------------------------------------
    # 2c. updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    # ------------------------------------------------------------------
    # Igual que oportunidades.updated_at. POST /visits la actualiza al
    # cancelar una visita sustituida.
    cur.execute(
        "ALTER TABLE visitas ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();"
    )
    print("  2c. updated_at -> columna asegurada")

    # ------------------------------------------------------------------
    # 3. fecha_propuesta NOT NULL
    # ------------------------------------------------------------------
    # SET NOT NULL sobre una columna que ya lo es no hace nada.
    cur.execute("ALTER TABLE visitas ALTER COLUMN fecha_propuesta SET NOT NULL;")
    print("  3.  fecha_propuesta -> NOT NULL")

    # ------------------------------------------------------------------
    # 4. Índice único PARCIAL: una sola visita activa por oportunidad
    # ------------------------------------------------------------------
    # "Parcial" = el índice solo contiene las filas que cumplen el WHERE.
    # Al ser ÚNICO, dos filas de la MISMA oportunidad no pueden estar a la
    # vez en él, es decir, no puede haber dos visitas activas. Las
    # canceladas y completadas no entran en el índice, así que una
    # oportunidad puede acumular todas las que haga falta.
    #
    # Es la SEGUNDA barrera contra dos peticiones simultáneas (la primera
    # es el bloqueo de la oportunidad en services/, ver el plan, 2.4).
    cur.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_ACTIVA}
        ON visitas (oportunidad_id)
        WHERE estado IN ('solicitada', 'confirmada');
        """
    )
    print(f"  4.  {INDICE_ACTIVA} -> índice asegurado")

    # ------------------------------------------------------------------
    # 5. Reglas de negocio de las visitas
    # ------------------------------------------------------------------
    # ON CONFLICT (clave) DO NOTHING: si la clave ya existe, no se
    # sobrescribe. Un valor cambiado a mano por administración NO debe
    # volver al de este script al re-ejecutarlo. El valor real se enseña
    # en el bloque DESPUÉS.
    for clave, valor, descripcion in REGLAS_VISITA:
        cur.execute(
            """
            INSERT INTO reglas_negocio (clave, valor, descripcion)
            VALUES (%s, %s, %s)
            ON CONFLICT (clave) DO NOTHING;
            """,
            (clave, valor, descripcion),
        )
        # rowcount dice cuántas filas ha escrito la última sentencia: 1 si
        # la ha insertado, 0 si ya existía.
        print(f"  5.  {clave} = {valor} -> {'insertada' if cur.rowcount == 1 else 'ya existía, no se toca'}")

    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
mostrar_estado()

cur.close()
cn.close()
