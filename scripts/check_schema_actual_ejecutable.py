"""
Verificación real de que docs/schema_actual.sql se puede EJECUTAR para
recrear el esquema, y de que lo que recrea coincide con la base de datos.

CLAUDE.md dice que schema_actual.sql es la única fuente fiable del esquema
y que se puede ejecutar para recrearlo. Este script lo comprueba de verdad:

  1. Lee el archivo .sql (por defecto docs/schema_actual.sql; con
     --archivo se puede probar otro, por ejemplo una versión antigua para
     las pruebas en negativo).
  2. En UNA transacción: crea un esquema vacío (prueba_dump), hace que las
     órdenes sin prefijo vayan a él (SET LOCAL search_path) y ejecuta el
     archivo entero.
  3. Si falla, enseña el error REAL de PostgreSQL y termina con FALLO.
  4. Si funciona, compara el esquema de prueba con public: número de
     tablas, de columnas, de restricciones por tipo (clave primaria,
     unique, clave foránea, check), de índices que no son restricciones y
     de tablas con RLS activado.
  5. Termina SIEMPRE con ROLLBACK: en PostgreSQL el DDL (CREATE SCHEMA,
     CREATE TABLE...) también es transaccional, así que no queda rastro.
     Al final se comprueba que el esquema de prueba ya no existe.

Una adaptación, SOLO en la copia en memoria (el archivo no se toca): las
líneas de índices vienen de pg_indexes con el esquema escrito
("CREATE UNIQUE INDEX ... ON public.clientes ..."). Ejecutadas tal cual,
crearían el índice sobre la tabla REAL. Se cambia "ON public." por
"ON prueba_dump.". En una base de datos vacía, que es para lo que existe
el archivo, el "public." es correcto.

No modifica nada de la base de datos real.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# Este script vive en scripts/: la raíz del repositorio está un nivel arriba.
RAIZ = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ / ".env")

ESQUEMA_PRUEBA = "prueba_dump"

lector = argparse.ArgumentParser(description="¿Se puede ejecutar schema_actual.sql?")
lector.add_argument("--archivo", default=str(RAIZ / "docs" / "schema_actual.sql"))
ARGS = lector.parse_args()

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(titulo)
        print(f"  [FALLO] {titulo}  {detalle}")


def recuento(cur, esquema):
    """
    Diccionario con lo que hay en ese esquema: tablas, columnas,
    restricciones por tipo, índices que no son restricciones y tablas con
    RLS. Se usa igual para public y para el esquema de prueba, así que las
    dos cifras se comparan con la misma vara.
    """
    r = {}
    # Tablas normales (relkind 'r'), de pg_class, el catálogo de tablas.
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE c.relrowsecurity) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r';",
        (esquema,),
    )
    r["tablas"], r["tablas con RLS"] = cur.fetchone()
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = %s;", (esquema,))
    r["columnas"] = cur.fetchone()[0]
    # contype: 'p' clave primaria, 'u' unique, 'f' clave foránea, 'c' check.
    cur.execute(
        "SELECT c.contype, count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = %s GROUP BY c.contype;",
        (esquema,),
    )
    nombres = {"p": "claves primarias", "u": "unique", "f": "claves foráneas", "c": "check"}
    por_tipo = dict(cur.fetchall())
    for letra, nombre in nombres.items():
        r[nombre] = por_tipo.get(letra, 0)
    # Índices que no respaldan ninguna restricción (el mismo criterio que
    # dump_schema.py, sección 3b).
    cur.execute(
        "SELECT count(*) FROM pg_indexes i WHERE i.schemaname = %s AND NOT EXISTS "
        "(SELECT 1 FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace "
        " WHERE k.conname = i.indexname AND n.nspname = %s);",
        (esquema, esquema),
    )
    r["índices sueltos"] = cur.fetchone()[0]
    return r


archivo = Path(ARGS.archivo)
sql = archivo.read_text(encoding="utf-8")
print(f"Archivo: {archivo}  ({len(sql)} caracteres)")
# La adaptación descrita arriba, solo en esta copia en memoria.
sql_prueba = sql.replace("ON public.", f"ON {ESQUEMA_PRUEBA}.")

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()
try:
    # CREATE SCHEMA: un espacio de nombres vacío dentro de la misma base de
    # datos, para que nada choque con las tablas reales de public.
    cur.execute(f"CREATE SCHEMA {ESQUEMA_PRUEBA};")
    # SET LOCAL search_path: dentro de ESTA transacción, un nombre sin
    # prefijo (CREATE TABLE clientes, REFERENCES oportunidades) se busca y
    # se crea SOLO en el esquema de prueba. No se incluye public a
    # propósito: si una tabla aún no existe en la prueba, debe dar error,
    # no encontrar la real.
    cur.execute(f"SET LOCAL search_path TO {ESQUEMA_PRUEBA};")
    try:
        # psycopg2 admite varias órdenes separadas por ; en una sola llamada.
        cur.execute(sql_prueba)
    except psycopg2.Error as error:
        # El error REAL de PostgreSQL: código (pgcode) y mensaje.
        print(f"\n  ERROR al ejecutar el archivo: {type(error).__name__} (código {error.pgcode})")
        print(f"    {str(error).strip()}")
        comprobar("el archivo se ejecuta entero sin errores", False)
    else:
        # Rama else del try: solo si el archivo se ejecutó sin error.
        comprobar("el archivo se ejecuta entero sin errores", True)
        prueba = recuento(cur, ESQUEMA_PRUEBA)
        real = recuento(cur, "public")
        print(f"\n  {'':22}{'recreado':>10}{'real':>8}")
        for clave in real:
            print(f"  {clave:22}{prueba[clave]:>10}{real[clave]:>8}")
        for clave in real:
            comprobar(f"{clave}: recreado = real", prueba[clave] == real[clave], f"({prueba[clave]} / {real[clave]})")
finally:
    # SIEMPRE: deshacer el esquema de prueba y todo lo creado en él.
    cn.rollback()

cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s;", (ESQUEMA_PRUEBA,))
comprobar("tras el ROLLBACK el esquema de prueba no existe", cur.fetchone()[0] == 0)
cn.commit()
cn.close()

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
