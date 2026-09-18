"""
PASO 1 - Anade el CHECK a oportunidades.tipo_reforma y lo prueba de
verdad intentando meter 'bano' con enie.

La prueba entera ocurre dentro de UNA transaccion que termina en
rollback: las filas descartables (cliente, lead, oportunidad) nunca
llegan a existir de forma permanente. El ALTER TABLE se confirma aparte,
antes, con su propio commit.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
# La raiz del repositorio, calculada desde la ubicacion de este archivo
# (scripts/ -> raiz) en vez de con una ruta absoluta escrita a mano, para
# que funcione en cualquier maquina. Misma tecnica que
# scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

NOMBRE_CHECK = "chk_oportunidades_tipo_reforma"

# ------------------------------------------------------------------
print("=" * 80)
print("1. ESTADO ANTES: constraints de oportunidades")
print("=" * 80)
cur.execute(
    """
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'oportunidades' AND con.contype = 'c'
    ORDER BY con.conname;
    """
)
antes = cur.fetchall()
for n, d in antes:
    print(f"  {n}: {d}")
ya_existia = any(n == NOMBRE_CHECK for n, _ in antes)
print(f"  ¿Existe ya {NOMBRE_CHECK}? -> {'SI' if ya_existia else 'NO'}")

# ------------------------------------------------------------------
print("\n" + "=" * 80)
print("2. ALTER TABLE - se anade el CHECK si falta")
print("=" * 80)
# La restriccion se anadio una sola vez, pero este script debe poder
# ejecutarse las veces que haga falta como verificacion permanente. Un
# ALTER TABLE ADD CONSTRAINT sobre una restriccion que ya existe falla
# con DuplicateObject, asi que se comprueba antes. Postgres no admite
# "ADD CONSTRAINT IF NOT EXISTS" para restricciones de tabla, por eso la
# comprobacion se hace desde Python y no en el propio SQL.
if ya_existia:
    print("  Ya existia: no se ejecuta el ALTER TABLE (script repetible).")
else:
    cur.execute(
        """
        ALTER TABLE oportunidades
        ADD CONSTRAINT chk_oportunidades_tipo_reforma
        CHECK (tipo_reforma IN ('bano', 'cocina', 'integral_vivienda', 'parcial_acabados')
               OR tipo_reforma IS NULL);
        """
    )
    cn.commit()
    print("  ALTER TABLE ejecutado y confirmado (commit).")

cur.execute(
    """
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'oportunidades' AND con.conname = %s;
    """,
    (NOMBRE_CHECK,),
)
fila = cur.fetchone()
print(f"  Confirmado en pg_constraint:\n    {fila[0]}\n    {fila[1]}")

# ------------------------------------------------------------------
print("\n" + "=" * 80)
print("3. PRUEBA REAL con filas descartables (todo dentro de una transaccion")
print("   que se deshace al final)")
print("=" * 80)

EMAIL_PRUEBA = "descartable-paso1@example.invalid"

cur.execute(
    "INSERT INTO clientes (nombre, email, telefono) VALUES (%s,%s,%s) RETURNING id;",
    ("Fila descartable Paso 1", EMAIL_PRUEBA, "000000000"),
)
cliente_id = cur.fetchone()[0]
print(f"  cliente descartable creado -> id={cliente_id}")

cur.execute(
    "INSERT INTO leads (cliente_id, canal) VALUES (%s,%s) RETURNING id;",
    (cliente_id, "prueba_paso1"),
)
lead_id = cur.fetchone()[0]
print(f"  lead descartable creado    -> id={lead_id}")

# --- 3a. valor CORRECTO: debe entrar ---
print("\n  --- 3a. tipo_reforma = 'bano' (correcto) ---")
cur.execute(
    "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s,%s) RETURNING id;",
    (lead_id, "bano"),
)
print(f"     ACEPTADO, id={cur.fetchone()[0]}  <- el CHECK no estorba a lo valido")

# --- 3b. NULL: debe entrar (la columna es nullable) ---
print("\n  --- 3b. tipo_reforma = NULL ---")
cur.execute(
    "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, NULL) RETURNING id;",
    (lead_id,),
)
print(f"     ACEPTADO, id={cur.fetchone()[0]}  <- NULL sigue permitido, como se pedia")

# --- 3c. valor CON ENIE: debe ser RECHAZADO ---
print("\n  --- 3c. tipo_reforma = 'ba\u00f1o' (CON ENIE, el caso del bug) ---")
try:
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s,%s) RETURNING id;",
        (lead_id, "ba\u00f1o"),
    )
    print("     *** FALLO DE LA PRUEBA: Postgres lo ACEPTO ***")
    resultado_3c = False
except psycopg2.errors.CheckViolation as e:
    print(f"     RECHAZADO por Postgres. Error literal:")
    for linea in str(e).strip().split("\n"):
        print(f"       {linea}")
    resultado_3c = True

# --- 3d. otro valor invalido cualquiera ---
cn.rollback()  # la transaccion quedo rota por el error anterior
print("\n  --- 3d. tipo_reforma = 'tejado' (valor inventado) ---")
cur.execute("SELECT 1;")  # transaccion nueva
try:
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s,%s);",
        (lead_id, "tejado"),
    )
    print("     *** FALLO: lo acepto ***")
    resultado_3d = False
except psycopg2.errors.CheckViolation:
    print("     RECHAZADO por Postgres (CheckViolation)")
    resultado_3d = True

# ------------------------------------------------------------------
print("\n" + "=" * 80)
print("4. DESHACER las filas de prueba")
print("=" * 80)
cn.rollback()
print("  rollback() ejecutado.")

# Comprobacion desde una transaccion nueva: nada debe quedar.
cur.execute("SELECT COUNT(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
c1 = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM leads WHERE canal = 'prueba_paso1';")
c2 = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM oportunidades;")
c3 = cur.fetchone()[0]
cn.commit()
print(f"  clientes descartables restantes    : {c1}")
print(f"  leads descartables restantes       : {c2}")
print(f"  filas totales en oportunidades     : {c3}")

print("\n" + "=" * 80)
todo_ok = resultado_3c and resultado_3d and c1 == 0 and c2 == 0 and c3 == 0
print("RESULTADO:", "CORRECTO" if todo_ok else "HAY FALLOS")
print("=" * 80)

cur.close()
cn.close()
sys.exit(0 if todo_ok else 1)
