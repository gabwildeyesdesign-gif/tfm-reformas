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

# argparse: lee los parámetros de la línea de comandos (--archivo ...).
import argparse
# os: para leer variables de entorno (os.getenv).
import os
# sys: para la codificación de la consola y para sys.exit con el resultado.
import sys
# Path: para construir rutas y leer archivos.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# load_dotenv: lee el .env y mete sus valores como variables de entorno.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# Este script vive en scripts/: la raíz del repositorio está un nivel arriba.
RAIZ = Path(__file__).resolve().parents[1]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ / ".env")

# Nombre del esquema temporal donde se recrea todo.
ESQUEMA_PRUEBA = "prueba_dump"

# --archivo es opcional; por defecto, docs/schema_actual.sql.
lector = argparse.ArgumentParser(description="¿Se puede ejecutar schema_actual.sql?")
lector.add_argument("--archivo", default=str(RAIZ / "docs" / "schema_actual.sql"))
ARGS = lector.parse_args()

# Contador de comprobaciones correctas y lista con el título de las fallidas.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: esta función cambia la variable ok del archivo, no una copia.
    global ok
    # Verdadera: se suma una correcta y se enseña [OK].
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    # Falsa: se apunta el título en fallos y se enseña [FALLO].
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
    # El diccionario de resultados, que se va rellenando.
    r = {}
    # Tablas normales (relkind 'r'), de pg_class, el catálogo de tablas.
    # SQL: dos recuentos a la vez, todas las tablas del esquema y, con
    # FILTER, solo las que tienen RLS activado.
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE c.relrowsecurity) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r';",
        (esquema,),
    )
    # Los dos números de la fila, a dos claves del diccionario.
    r["tablas"], r["tablas con RLS"] = cur.fetchone()
    # SQL: número de columnas de todas las tablas del esquema.
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = %s;", (esquema,))
    r["columnas"] = cur.fetchone()[0]
    # contype: 'p' clave primaria, 'u' unique, 'f' clave foránea, 'c' check.
    # SQL: cuántas restricciones hay de cada tipo (una fila por tipo).
    cur.execute(
        "SELECT c.contype, count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = %s GROUP BY c.contype;",
        (esquema,),
    )
    # Letra de contype -> nombre legible para la tabla de resultados.
    nombres = {"p": "claves primarias", "u": "unique", "f": "claves foráneas", "c": "check"}
    # Diccionario letra -> recuento.
    por_tipo = dict(cur.fetchall())
    # Un tipo sin ninguna restricción no sale en la consulta: .get(..., 0).
    for letra, nombre in nombres.items():
        r[nombre] = por_tipo.get(letra, 0)
    # Índices que no respaldan ninguna restricción (el mismo criterio que
    # dump_schema.py, sección 3b).
    # SQL: cuenta los índices del esquema para los que NO existe ninguna
    # restricción con el mismo nombre (las restricciones crean su índice
    # con su nombre; esos ya se contaron arriba).
    cur.execute(
        "SELECT count(*) FROM pg_indexes i WHERE i.schemaname = %s AND NOT EXISTS "
        "(SELECT 1 FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace "
        " WHERE k.conname = i.indexname AND n.nspname = %s);",
        (esquema, esquema),
    )
    r["índices sueltos"] = cur.fetchone()[0]
    # El diccionario completo, para comparar los dos esquemas.
    return r


# Se lee el archivo .sql entero, como texto.
archivo = Path(ARGS.archivo)
sql = archivo.read_text(encoding="utf-8")
print(f"Archivo: {archivo}  ({len(sql)} caracteres)")
# La adaptación descrita arriba, solo en esta copia en memoria.
sql_prueba = sql.replace("ON public.", f"ON {ESQUEMA_PRUEBA}.")

# Una sola conexión y su cursor. Todo lo que sigue es UNA transacción.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()
# try / finally: el rollback se ejecuta pase lo que pase.
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
        # FALLO explícito: el archivo no se pudo ejecutar.
        comprobar("el archivo se ejecuta entero sin errores", False)
    else:
        # Rama else del try: solo si el archivo se ejecutó sin error.
        comprobar("el archivo se ejecuta entero sin errores", True)
        # El recuento del esquema recreado y el del real, con la misma función.
        prueba = recuento(cur, ESQUEMA_PRUEBA)
        real = recuento(cur, "public")
        # Tabla de dos columnas: ":>10" alinea a la derecha en 10 caracteres.
        print(f"\n  {'':22}{'recreado':>10}{'real':>8}")
        for clave in real:
            print(f"  {clave:22}{prueba[clave]:>10}{real[clave]:>8}")
        # Una comprobación por cada cifra: recreado tiene que ser igual a real.
        for clave in real:
            comprobar(f"{clave}: recreado = real", prueba[clave] == real[clave], f"({prueba[clave]} / {real[clave]})")
finally:
    # SIEMPRE: deshacer el esquema de prueba y todo lo creado en él.
    cn.rollback()

# SQL: ¿existe todavía un esquema con ese nombre? (pg_namespace es el
# catálogo de esquemas). Tras el rollback tiene que dar 0.
cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s;", (ESQUEMA_PRUEBA,))
comprobar("tras el ROLLBACK el esquema de prueba no existe", cur.fetchone()[0] == 0)
# Cierra la transacción de lectura y la conexión.
cn.commit()
cn.close()

# Resumen: correctas sobre el total.
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Si hubo fallos, se listan y el script termina con código 1 (error).
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
