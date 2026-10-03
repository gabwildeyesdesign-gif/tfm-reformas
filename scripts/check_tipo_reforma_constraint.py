"""
PASO 1 - Anade el CHECK a oportunidades.tipo_reforma y lo prueba de
verdad intentando meter 'bano' con enie.

La prueba entera ocurre dentro de UNA transaccion que termina en
rollback: las filas descartables (cliente, lead, oportunidad) nunca
llegan a existir de forma permanente. El ALTER TABLE se confirma aparte,
antes, con su propio commit.
"""

# os: para leer variables de entorno (os.getenv).
import os
# sys: para la codificación de la consola y para sys.exit con el resultado.
import sys
# Path: para construir la ruta del .env sin escribirla a mano.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# load_dotenv: lee el .env y mete sus valores como variables de entorno.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")
# La raiz del repositorio, calculada desde la ubicacion de este archivo
# (scripts/ -> raiz) en vez de con una ruta absoluta escrita a mano, para
# que funcione en cualquier maquina. Misma tecnica que
# scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Carga el .env de la raíz; después, una conexión y su cursor (el objeto
# que envía el SQL y lee los resultados).
load_dotenv(RAIZ_REPO / ".env")
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

# El nombre de la restricción que se comprueba, en un solo sitio.
NOMBRE_CHECK = "chk_oportunidades_tipo_reforma"

# ------------------------------------------------------------------
# "=" * 80 repite el carácter 80 veces: una raya para separar secciones.
print("=" * 80)
print("1. ESTADO ANTES: constraints de oportunidades")
print("=" * 80)
# SQL: nombre y definición de todos los CHECK (contype 'c') de la tabla
# oportunidades. pg_constraint es el catálogo de restricciones y pg_class
# el de tablas; el JOIN los une para filtrar por el nombre de la tabla.
cur.execute(
    """
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'oportunidades' AND con.contype = 'c'
    ORDER BY con.conname;
    """
)
# Lista de parejas (nombre, definición); se enseña cada una.
antes = cur.fetchall()
for n, d in antes:
    print(f"  {n}: {d}")
# any(...) es True si alguna de las restricciones se llama como la buscada.
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
    # SQL: añade el CHECK que solo admite los cuatro tipos sin tildes (o NULL).
    cur.execute(
        """
        ALTER TABLE oportunidades
        ADD CONSTRAINT chk_oportunidades_tipo_reforma
        CHECK (tipo_reforma IN ('bano', 'cocina', 'integral_vivienda', 'parcial_acabados')
               OR tipo_reforma IS NULL);
        """
    )
    # commit: el ALTER TABLE queda guardado de forma permanente.
    cn.commit()
    print("  ALTER TABLE ejecutado y confirmado (commit).")

# SQL: la definición del CHECK por su nombre, para confirmar que existe.
cur.execute(
    """
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'oportunidades' AND con.conname = %s;
    """,
    (NOMBRE_CHECK,),
)
# La fila (nombre, definición); "\n" en el texto es un salto de línea.
fila = cur.fetchone()
print(f"  Confirmado en pg_constraint:\n    {fila[0]}\n    {fila[1]}")

# ------------------------------------------------------------------
print("\n" + "=" * 80)
print("3. PRUEBA REAL con filas descartables (todo dentro de una transaccion")
print("   que se deshace al final)")
print("=" * 80)

# La marca propia del script: un email del dominio reservado .invalid.
EMAIL_PRUEBA = "descartable-paso1@example.invalid"

# SQL: el cliente de prueba, con el email marca; RETURNING id devuelve el
# id que le ha dado Postgres.
cur.execute(
    "INSERT INTO clientes (nombre, email, telefono) VALUES (%s,%s,%s) RETURNING id;",
    ("Fila descartable Paso 1", EMAIL_PRUEBA, "000000000"),
)
cliente_id = cur.fetchone()[0]
print(f"  cliente descartable creado -> id={cliente_id}")

# SQL: un lead de ese cliente, con el canal de prueba.
cur.execute(
    "INSERT INTO leads (cliente_id, canal) VALUES (%s,%s) RETURNING id;",
    (cliente_id, "prueba_paso1"),
)
lead_id = cur.fetchone()[0]
print(f"  lead descartable creado    -> id={lead_id}")

# --- 3a. valor CORRECTO: debe entrar ---
print("\n  --- 3a. tipo_reforma = 'bano' (correcto) ---")
# SQL: una oportunidad con un tipo válido; si el CHECK la rechazara, el
# script se pararía aquí con el error.
cur.execute(
    "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s,%s) RETURNING id;",
    (lead_id, "bano"),
)
print(f"     ACEPTADO, id={cur.fetchone()[0]}  <- el CHECK no estorba a lo valido")

# --- 3b. NULL: debe entrar (la columna es nullable) ---
print("\n  --- 3b. tipo_reforma = NULL ---")
# SQL: una oportunidad sin tipo (NULL escrito directamente en el SQL).
cur.execute(
    "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, NULL) RETURNING id;",
    (lead_id,),
)
print(f"     ACEPTADO, id={cur.fetchone()[0]}  <- NULL sigue permitido, como se pedia")

# --- 3c. valor CON ENIE: debe ser RECHAZADO ---
print("\n  --- 3c. tipo_reforma = 'ba\u00f1o' (CON ENIE, el caso del bug) ---")
# try / except: se espera que el INSERT falle con CheckViolation.
try:
    # SQL: una oportunidad con 'ba\u00f1o' (\u00f1 es la \u00f1 escrita por su c\u00f3digo).
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s,%s) RETURNING id;",
        (lead_id, "ba\u00f1o"),
    )
    # Si se llega aqu\u00ed, Postgres lo acept\u00f3: la prueba falla.
    print("     *** FALLO DE LA PRUEBA: Postgres lo ACEPTO ***")
    resultado_3c = False
# El error esperado: se ense\u00f1a su texto, l\u00ednea a l\u00ednea.
except psycopg2.errors.CheckViolation as e:
    print(f"     RECHAZADO por Postgres. Error literal:")
    for linea in str(e).strip().split("\n"):
        print(f"       {linea}")
    resultado_3c = True

# --- 3d. otro valor invalido cualquiera ---
cn.rollback()  # la transaccion quedo rota por el error anterior
print("\n  --- 3d. tipo_reforma = 'tejado' (valor inventado) ---")
cur.execute("SELECT 1;")  # transaccion nueva
# Mismo esquema que 3c, con otro valor inventado.
try:
    # SQL: una oportunidad con 'tejado', que no está en la lista del CHECK.
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s,%s);",
        (lead_id, "tejado"),
    )
    # Si se llega aquí, Postgres lo aceptó: la prueba falla.
    print("     *** FALLO: lo acepto ***")
    resultado_3d = False
# El error esperado.
except psycopg2.errors.CheckViolation:
    print("     RECHAZADO por Postgres (CheckViolation)")
    resultado_3d = True

# ------------------------------------------------------------------
print("\n" + "=" * 80)
print("4. DESHACER las filas de prueba")
print("=" * 80)
# rollback: deshace todo lo de la transacción en curso (aquí, la
# transacción rota por 3d; el cliente y el lead ya los deshizo el rollback
# de antes de 3d).
cn.rollback()
print("  rollback() ejecutado.")

# Comprobacion desde una transaccion nueva: nada debe quedar.
# SQL: clientes con el email marca (debe ser 0).
cur.execute("SELECT COUNT(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
c1 = cur.fetchone()[0]
# SQL: leads con el canal de prueba (debe ser 0).
cur.execute("SELECT COUNT(*) FROM leads WHERE canal = 'prueba_paso1';")
c2 = cur.fetchone()[0]
# Solo se cuentan las oportunidades PROPIAS de este script, no la tabla
# entera: antes era "SELECT COUNT(*) FROM oportunidades", que contaba
# tambien las oportunidades reales y daba "HAY FALLOS" siempre que la tabla
# tuviera filas ajenas. La marca es EMAIL_PRUEBA: el indice unico
# clientes_email_lower_key garantiza que como mucho un cliente lo tiene, y
# el dominio .invalid esta reservado, asi que ningun cliente real puede
# tenerlo. (leads.canal no sirve igual de bien: es texto libre, sin unico.)
# La consulta salta oportunidad -> su lead (lead_id) -> su cliente
# (cliente_id) y se queda con las que acaban en el cliente de prueba.
cur.execute(
    """
    SELECT COUNT(*)
    FROM oportunidades o
    JOIN leads l    ON l.id = o.lead_id
    JOIN clientes c ON c.id = l.cliente_id
    WHERE c.email = %s;
    """,
    (EMAIL_PRUEBA,),  # el email se pasa como parametro, nunca pegado en el SQL
)
c3 = cur.fetchone()[0]  # numero de oportunidades de prueba que han sobrevivido
# commit: cierra la transacción de lectura (no hay nada que guardar).
cn.commit()
print(f"  clientes descartables restantes    : {c1}")
print(f"  leads descartables restantes       : {c2}")
print(f"  oportunidades descartables restantes: {c3}")

print("\n" + "=" * 80)
# Correcto solo si 3c y 3d rechazaron y no quedó ninguna fila de prueba.
todo_ok = resultado_3c and resultado_3d and c1 == 0 and c2 == 0 and c3 == 0
print("RESULTADO:", "CORRECTO" if todo_ok else "HAY FALLOS")
print("=" * 80)

# Se cierran el cursor y la conexión; el código de salida (0 bien, 1 mal)
# es lo que mira la suite.
cur.close()
cn.close()
sys.exit(0 if todo_ok else 1)
