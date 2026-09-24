"""
Verificación de la migración paso8 (email sin distinguir mayúsculas y
leads.lead_token con UNIQUE).

Comprueba que el esquema quedó como debía y que se COMPORTA como debe:

  E1. La UNIQUE antigua clientes_email_key ya no existe.
  E2. Existe el índice ÚNICO clientes_email_lower_key sobre lower(email).
  E3. Dos emails que solo difieren en mayúsculas se rechazan EN LA BASE
      DE DATOS (defensa doble: aunque se salten Pydantic).
  E4. Dos emails idénticos se siguen rechazando.
  E5. ON CONFLICT ((lower(email))), el de create_lead, devuelve el MISMO
      cliente para "X@..." y "x@...".
  E6. ON CONFLICT (email), el del código antiguo, ahora FALLA: el código
      que no se haya actualizado falla de forma visible, no en silencio.
  T1. leads.lead_token existe, VARCHAR(100), admite NULL.
  T2. Existe la UNIQUE leads_lead_token_key sobre (lead_token).
  T3. Las filas anteriores a la migración tienen lead_token NULL.
  T4. Dos leads con lead_token NULL conviven (UNIQUE admite varios NULL).
  T5. Dos leads con el MISMO token: el segundo se rechaza, y la
      restricción que salta es exactamente leads_lead_token_key (es el
      nombre que compara create_lead).
  T6. Un token de 101 caracteres se rechaza (ancho de la columna).

Todo ocurre dentro de UNA transacción que termina en rollback: el script
no deja nada escrito. Cada prueba que puede fallar va en su propio
SAVEPOINT, porque en Postgres una sentencia fallida aborta la transacción
entera. Mismo patrón que check_migracion_m2_leads.py.
"""

import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.errors
from dotenv import load_dotenv
from psycopg2.extras import Json

sys.stdout.reconfigure(encoding="utf-8")

RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

NOMBRE_SAVEPOINT = "asercion"
INDICE_EMAIL = "clientes_email_lower_key"
RESTRICCION_EMAIL_ANTIGUA = "clientes_email_key"
RESTRICCION_TOKEN = "leads_lead_token_key"
# Emails de prueba. Nunca llegan a guardarse (todo acaba en rollback),
# pero se usan dominios de ejemplo por si algo fallara a mitad.
EMAIL_MAYUS = "Check.Paso8@Example.com"
EMAIL_MINUS = "check.paso8@example.com"

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(titulo)
        print(f"  [FALLO] {titulo}  {detalle}")


def describir(error):
    """
    Texto corto para el detalle de una comprobación que ESPERA un error.
    Si no hubo error (error es None), lo dice sin rodeos: es exactamente
    el fallo silencioso que la regla D16 obliga a detectar.
    """
    if error is None:
        return "(sin error: la excepción se ha tragado)"
    restriccion = getattr(error.diag, "constraint_name", None)
    return f"({type(error).__name__}, restricción={restriccion})"


def ejecutar_en_savepoint(cur, sentencias):
    """
    Ejecuta una LISTA de sentencias dentro de un mismo punto de guardado
    y devuelve (error, filas_de_la_última). Al terminar, deshace todo lo
    que hicieron, falle o no, sin dejar la transacción abortada.

    Es una lista, y no una sola sentencia como en check_migracion_m2_leads,
    porque aquí varias pruebas necesitan que la SEGUNDA inserción vea la
    PRIMERA (por ejemplo, dos leads con el mismo token): si cada una fuera
    en su propio savepoint, la primera se desharía antes de la segunda y
    nunca chocarían.

    sentencias: lista de tuplas (sql, parámetros).
    """
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        filas = None
        for sql, params in sentencias:
            cur.execute(sql, params)
            # cur.description es None cuando la sentencia no devuelve
            # filas (un INSERT sin RETURNING); solo se leen si las hay.
            filas = cur.fetchall() if cur.description is not None else None
        return None, filas
    except psycopg2.Error as error:
        return error, None
    finally:
        # finally se ejecuta SIEMPRE, haya habido error o no: la prueba
        # nunca deja rastro dentro de la transacción.
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


def sql_cliente(email):
    """INSERT de un cliente de prueba, saltándose Pydantic a propósito."""
    return (
        "INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
        ("Prueba paso8", email, "600000000"),
    )


def sql_lead(token):
    """
    INSERT de un lead de prueba colgado del cliente con EMAIL_MINUS (que
    se crea en la misma lista de sentencias). La subconsulta evita tener
    que pasar el id del cliente de una sentencia a otra.
    """
    return (
        """
        INSERT INTO leads (cliente_id, canal, lead_token, datos_estructurados)
        VALUES ((SELECT id FROM clientes WHERE email = %s), %s, %s, %s)
        RETURNING id;
        """,
        (EMAIL_MINUS, "check_paso8", token, Json({"m2": 10})),
    )


cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()

try:
    print("=" * 78)
    print("E - Unicidad del email sin distinguir mayúsculas")
    print("=" * 78)

    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conrelid = 'clientes'::regclass AND conname = %s;",
        (RESTRICCION_EMAIL_ANTIGUA,),
    )
    comprobar(f"E1 {RESTRICCION_EMAIL_ANTIGUA} ya NO existe", cur.fetchone() is None)

    # pg_index guarda las propiedades del índice (indisunique = es único);
    # pg_get_indexdef devuelve su definición como texto.
    cur.execute(
        """
        SELECT i.indisunique, pg_get_indexdef(i.indexrelid)
        FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relname = %s;
        """,
        (INDICE_EMAIL,),
    )
    fila = cur.fetchone()
    print(f"  definición: {fila[1] if fila else '(no existe)'}")
    comprobar(f"E2 {INDICE_EMAIL} existe, es ÚNICO y es sobre lower(email)",
              fila is not None and fila[0] is True and "lower" in fila[1] and "email" in fila[1])

    error, _ = ejecutar_en_savepoint(cur, [sql_cliente(EMAIL_MAYUS), sql_cliente(EMAIL_MINUS)])
    comprobar("E3 'Check.Paso8@Example.com' + 'check.paso8@example.com' -> rechazado",
              isinstance(error, psycopg2.errors.UniqueViolation)
              and error.diag.constraint_name == INDICE_EMAIL,
              describir(error))

    error, _ = ejecutar_en_savepoint(cur, [sql_cliente(EMAIL_MINUS), sql_cliente(EMAIL_MINUS)])
    comprobar("E4 el mismo email dos veces -> rechazado",
              isinstance(error, psycopg2.errors.UniqueViolation), describir(error))

    # E5: el mismo upsert que create_lead, con el email en dos formas. Las
    # dos sentencias van en la misma lista para que la segunda vea la
    # primera; RETURNING id de cada una se lee con una tercera sentencia.
    upsert = """
        INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s)
        ON CONFLICT ((lower(email))) DO UPDATE SET email = EXCLUDED.email
        RETURNING id;
    """
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        cur.execute(upsert, ("Uno", EMAIL_MAYUS, "600000000"))
        id_1 = cur.fetchone()[0]
        cur.execute(upsert, ("Dos", EMAIL_MINUS, "600000001"))
        id_2 = cur.fetchone()[0]
        comprobar("E5 ON CONFLICT ((lower(email))) -> MISMO cliente_id",
                  id_1 == id_2, f"(ids {id_1} y {id_2})")
    except psycopg2.Error as error:
        comprobar("E5 ON CONFLICT ((lower(email))) -> MISMO cliente_id", False, describir(error))
    finally:
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")

    error, _ = ejecutar_en_savepoint(cur, [(
        """
        INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id;
        """,
        ("Viejo", EMAIL_MINUS, "600000000"),
    )])
    # InvalidColumnReference (42P10) es el error de "no hay ninguna
    # restricción única que encaje con este ON CONFLICT".
    comprobar("E6 ON CONFLICT (email) del código antiguo -> FALLA",
              isinstance(error, psycopg2.errors.InvalidColumnReference),
              describir(error) if error is None else f"({type(error).__name__})")

    print("\n" + "=" * 78)
    print("T - leads.lead_token")
    print("=" * 78)

    cur.execute(
        """
        SELECT data_type, character_maximum_length, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'leads' AND column_name = 'lead_token';
        """
    )
    fila = cur.fetchone()
    comprobar("T1 lead_token existe, VARCHAR(100), admite NULL",
              fila == ("character varying", 100, "YES"), f"({fila})")

    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s;",
        (RESTRICCION_TOKEN,),
    )
    fila = cur.fetchone()
    comprobar(f"T2 {RESTRICCION_TOKEN} existe y es UNIQUE (lead_token)",
              fila is not None and fila[0] == "UNIQUE (lead_token)", f"({fila})")

    # Las 17 filas que existían al migrar no tenían token: ninguna puede
    # tenerlo ahora. Se identifican por canal = 'formulario_web', el valor
    # que tienen todas y que el código ya no escribe desde la Fase 1 (los
    # leads nuevos llevan 'chat_web').
    cur.execute(
        "SELECT count(*), count(lead_token) FROM leads WHERE canal = 'formulario_web';"
    )
    total, con_token = cur.fetchone()
    comprobar("T3 las filas antiguas (canal 'formulario_web') tienen lead_token NULL",
              total > 0 and con_token == 0, f"({total} filas, {con_token} con token)")

    error, filas = ejecutar_en_savepoint(cur, [
        sql_cliente(EMAIL_MINUS), sql_lead(None), sql_lead(None),
    ])
    comprobar("T4 dos leads con lead_token NULL conviven",
              error is None and filas is not None,
              f"({type(error).__name__})" if error else "(los dos insertados)")

    error, _ = ejecutar_en_savepoint(cur, [
        sql_cliente(EMAIL_MINUS), sql_lead("tok-paso8"), sql_lead("tok-paso8"),
    ])
    comprobar(f"T5 mismo token dos veces -> rechazado por {RESTRICCION_TOKEN}",
              isinstance(error, psycopg2.errors.UniqueViolation)
              and error.diag.constraint_name == RESTRICCION_TOKEN,
              describir(error))

    error, _ = ejecutar_en_savepoint(cur, [sql_cliente(EMAIL_MINUS), sql_lead("t" * 101)])
    # StringDataRightTruncation (22001): "value too long for type
    # character varying(100)".
    comprobar("T6 token de 101 caracteres -> rechazado por el ancho de la columna",
              isinstance(error, psycopg2.errors.StringDataRightTruncation),
              describir(error) if error is None else f"({type(error).__name__})")
    error, _ = ejecutar_en_savepoint(cur, [sql_cliente(EMAIL_MINUS), sql_lead("t" * 100)])
    comprobar("T6 token de 100 caracteres exactos -> aceptado",
              error is None, f"({type(error).__name__})" if error else "")
finally:
    # Todo lo de arriba se deshace: el script no deja nada escrito.
    cn.rollback()
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
if fallos:
    for f in fallos:
        print("  - " + f)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
