"""
MIGRACIÓN DE ESQUEMA YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACIÓN.

Ejecutada el 2026-09-24 contra la base de datos real (rama
feat/n0-sesion-y-contacto, Fase 2). Dos cambios independientes, en UNA
transacción: o se aplican los dos o ninguno.

  1. clientes: la unicidad del email pasa a ignorar mayúsculas.
     Se crea el índice único clientes_email_lower_key sobre lower(email)
     y se elimina la restricción clientes_email_key (UNIQUE (email)).
     Motivo: "Pepe@gmail.com" y "pepe@gmail.com" eran dos clientes
     distintos. Es la segunda mitad de una defensa doble; la primera es
     el validador email_en_minusculas de LeadCreate.

  2. leads.lead_token VARCHAR(100) NULL + UNIQUE (leads_lead_token_key).
     Es la clave de idempotencia de POST /leads: hasta ahora lead_token
     era obligatorio en LeadCreate pero no se guardaba en ningún sitio,
     así que un reintento de n8n creaba un segundo lead.

ESTADO DE LOS DATOS AL MIGRAR (consultado antes de ejecutar):
  - SELECT lower(email), count(*) FROM clientes GROUP BY 1
    HAVING count(*) > 1  ->  0 filas. Ningún email tenía mayúsculas.
    Si hubiera habido duplicados, la decisión de cómo fusionarlos NO se
    toma dentro de un script: se para y se decide antes. Por eso el paso
    0 vuelve a comprobarlo y aborta si encuentra alguno.
  - leads tenía 17 filas: quedan con lead_token = NULL, porque su token
    nunca se guardó y no se puede reconstruir.

Es idempotente: cada paso comprueba primero si ya está hecho.

Verificación: scripts/check_migracion_email_lower_y_lead_token.py.
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

INDICE_EMAIL = "clientes_email_lower_key"
RESTRICCION_EMAIL_ANTIGUA = "clientes_email_key"
RESTRICCION_TOKEN = "leads_lead_token_key"

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def existe_restriccion(tabla, nombre):
    """Dice si una restricción con ese nombre existe en esa tabla."""
    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s;",
        (tabla, nombre),
    )
    return cur.fetchone() is not None


def definicion_indice(nombre):
    """Devuelve el CREATE INDEX completo de un índice, o None si no existe."""
    cur.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s;",
        (nombre,),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


def existe_columna(tabla, columna):
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s;
        """,
        (tabla, columna),
    )
    return cur.fetchone() is not None


print("=== ANTES ===")
print(f"  {RESTRICCION_EMAIL_ANTIGUA} existe: {existe_restriccion('clientes', RESTRICCION_EMAIL_ANTIGUA)}")
print(f"  {INDICE_EMAIL}: {definicion_indice(INDICE_EMAIL)}")
print(f"  leads.lead_token existe: {existe_columna('leads', 'lead_token')}")
print(f"  {RESTRICCION_TOKEN} existe: {existe_restriccion('leads', RESTRICCION_TOKEN)}")

try:
    # ------------------------------------------------------------------
    # 0. Comprobación de seguridad: emails que solo difieren en mayúsculas
    # ------------------------------------------------------------------
    # Ya se consultó antes de escribir este script (0 filas), pero se
    # repite aquí, dentro de la transacción, por si entró alguno entretanto.
    # Si los hubiera, CREATE UNIQUE INDEX fallaría de todos modos; se
    # comprueba antes para que el mensaje diga QUÉ emails son, no solo
    # "could not create unique index".
    cur.execute(
        "SELECT lower(email), count(*) FROM clientes GROUP BY 1 HAVING count(*) > 1;"
    )
    duplicados = cur.fetchall()
    if duplicados:
        print(f"\n  ABORTADO: emails duplicados por mayúsculas: {duplicados}")
        print("  No se fusionan aquí: hay que decidir antes qué ficha se conserva.")
        # raise dentro del try: lo recoge el except de abajo, que hace
        # rollback y vuelve a lanzar el error.
        raise RuntimeError("hay emails duplicados por mayúsculas")
    print("\n  0. emails duplicados por mayúsculas: 0 -> se puede continuar")

    # ------------------------------------------------------------------
    # 1a. Índice único sobre lower(email)
    # ------------------------------------------------------------------
    # Un índice ÚNICO sobre una EXPRESIÓN: Postgres calcula lower(email)
    # de cada fila y exige que ese resultado no se repita. Por eso
    # "Pepe@gmail.com" choca con "pepe@gmail.com": los dos dan lo mismo.
    #
    # Por qué un índice y no una restricción UNIQUE: la sintaxis
    # "CONSTRAINT ... UNIQUE (...)" solo admite nombres de columna, no
    # expresiones. Para exigir unicidad sobre lower(email), la única forma
    # en Postgres es CREATE UNIQUE INDEX. En la práctica es lo mismo (una
    # UNIQUE también se implementa con un índice único por debajo), y
    # ON CONFLICT lo reconoce igual: create_lead usa
    # ON CONFLICT ((lower(email))), y Postgres busca un índice único cuya
    # expresión sea exactamente esa.
    #
    # Se crea ANTES de borrar la restricción antigua para que la columna
    # nunca quede sin protección, ni siquiera dentro de la transacción.
    #
    # IF NOT EXISTS: si ya existe (segunda ejecución), no hace nada.
    #
    # Sin CONCURRENTLY: CONCURRENTLY evita bloquear la tabla mientras se
    # construye el índice, pero no puede ejecutarse dentro de una
    # transacción. Con 12 clientes el bloqueo dura milisegundos, y
    # mantener la migración en una sola transacción vale más.
    cur.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_EMAIL} ON clientes (lower(email));"
    )
    print(f"  1a. {INDICE_EMAIL} -> índice asegurado")

    # ------------------------------------------------------------------
    # 1b. Fuera la UNIQUE (email) antigua
    # ------------------------------------------------------------------
    # Con el índice de lower(email) ya creado, la UNIQUE antigua sobra: todo
    # lo que ella rechaza (dos emails idénticos) también lo rechaza el índice
    # nuevo. Mantenerla no haría daño a los datos, pero dejaría dos reglas
    # de unicidad para el mismo dato, y un ON CONFLICT (email) antiguo que
    # alguien copiara seguiría "funcionando" contra la regla equivocada.
    # Así, cualquier código que siga usando ON CONFLICT (email) falla en
    # voz alta ("there is no unique or exclusion constraint matching...")
    # en vez de comportarse distinto en silencio.
    #
    # Ninguna FK depende de ella (comprobado en pg_constraint: la única FK
    # hacia clientes, leads_cliente_id_fkey, apunta a clientes_pkey).
    cur.execute(
        f"ALTER TABLE clientes DROP CONSTRAINT IF EXISTS {RESTRICCION_EMAIL_ANTIGUA};"
    )
    print(f"  1b. {RESTRICCION_EMAIL_ANTIGUA} -> eliminada (o ya no existía)")

    # ------------------------------------------------------------------
    # 2a. Columna leads.lead_token
    # ------------------------------------------------------------------
    # NULL permitido a propósito: las 17 filas que ya existen no tienen
    # token (nunca se guardó). Exigir NOT NULL obligaría a inventarles uno,
    # y un token inventado haría creer que esos leads entraron con
    # idempotencia, cosa que no es verdad.
    #
    # VARCHAR(100) y no VARCHAR sin límite: un índice btree (el que crea
    # la UNIQUE) tiene un tamaño máximo por entrada, de unos 2.700 bytes.
    # Un token más largo haría fallar el INSERT con un error de Postgres,
    # es decir, un 500. Con el límite en la columna y el mismo
    # max_length=100 en LeadCreate, un token demasiado largo es un 422.
    cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_token VARCHAR(100) NULL;")
    print("  2a. leads.lead_token -> columna asegurada")

    # ------------------------------------------------------------------
    # 2b. UNIQUE (lead_token)
    # ------------------------------------------------------------------
    # Por qué admite varios NULL: en Postgres, por defecto, una UNIQUE
    # considera que dos NULL son DISTINTOS entre sí (NULL significa "valor
    # desconocido", y dos desconocidos no se pueden afirmar iguales). Así
    # que las 17 filas antiguas con NULL no chocan entre ellas. Postgres 15
    # añadió la opción NULLS NOT DISTINCT para el comportamiento contrario;
    # aquí NO se usa, a propósito. El script de verificación lo comprueba
    # con inserciones reales.
    #
    # ADD CONSTRAINT no admite IF NOT EXISTS: la repetibilidad se resuelve
    # comprobando antes en pg_constraint, como en paso7.
    if not existe_restriccion("leads", RESTRICCION_TOKEN):
        cur.execute(
            f"ALTER TABLE leads ADD CONSTRAINT {RESTRICCION_TOKEN} UNIQUE (lead_token);"
        )
        print(f"  2b. {RESTRICCION_TOKEN} -> creada")
    else:
        print(f"  2b. {RESTRICCION_TOKEN} ya existía: no se toca")

    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
print(f"  {RESTRICCION_EMAIL_ANTIGUA} existe: {existe_restriccion('clientes', RESTRICCION_EMAIL_ANTIGUA)}")
print(f"  {INDICE_EMAIL}: {definicion_indice(INDICE_EMAIL)}")
cur.execute(
    """
    SELECT column_name, data_type, character_maximum_length, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'leads' AND column_name = 'lead_token';
    """
)
print(f"  leads.lead_token: {cur.fetchone()}")
cur.execute(
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s;",
    (RESTRICCION_TOKEN,),
)
print(f"  {RESTRICCION_TOKEN}: {cur.fetchone()}")
cur.execute("SELECT count(*), count(lead_token) FROM leads;")
total, con_token = cur.fetchone()
print(f"  leads: {total} filas, {con_token} con lead_token (el resto NULL)")

cur.close()
cn.close()
