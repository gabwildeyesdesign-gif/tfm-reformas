"""
Verificación real de la migración paso9 (tabla visitas preparada para
POST /visits). Plan: docs/Plan_Endpoint_Visits.txt, sección 4.

DOS PARTES:
  A) Estructura: cómo ha quedado la tabla, leído de information_schema,
     pg_constraint y pg_indexes, y las cinco reglas de reglas_negocio.
  B) Comportamiento: se intenta escribir lo que las restricciones deben
     impedir, y lo que deben permitir, con inserciones reales.

Todo ocurre dentro de UNA transacción que se deshace al final: no queda
ninguna fila de prueba. Cada intento va en su propio SAVEPOINT, porque en
Postgres una sentencia fallida aborta la transacción entera, y sin el
punto de guardado las comprobaciones siguientes darían
InFailedSqlTransaction (gotcha de CLAUDE.md).

Una comprobación que ESPERA un error y no lo recibe se marca FALLO de
forma explícita ("la excepción se ha tragado"), no se da por buena.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

sys.stdout.reconfigure(encoding="utf-8")

RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

NOMBRE_SAVEPOINT = "asercion"
RESTRICCION_ESTADO = "visitas_estado_check"
RESTRICCION_TEXTO = "chk_visitas_texto_cliente_longitud"
INDICE_ACTIVA = "visitas_una_activa_por_oportunidad"
REGLAS_ESPERADAS = {
    "visita_manana_inicio_min": 510,
    "visita_manana_fin_min": 810,
    "visita_tarde_inicio_min": 1020,
    "visita_tarde_fin_min": 1200,
    "duracion_visita_min": 60,
}
EMAIL_PRUEBA = "check.paso9@example.com"

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


def ejecutar_en_savepoint(cur, sentencias):
    """
    Ejecuta una LISTA de sentencias dentro de un mismo punto de guardado y
    devuelve (error, filas_de_la_última). Al terminar lo deshace todo,
    falle o no, sin dejar la transacción abortada. Es una lista porque
    varias pruebas necesitan que la segunda inserción vea la primera (dos
    visitas activas de la misma oportunidad). Mismo patrón que
    check_migracion_email_lower_y_lead_token.py.
    """
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        filas = None
        for sql, params in sentencias:
            cur.execute(sql, params)
            filas = cur.fetchall() if cur.description is not None else None
        return None, filas
    except psycopg2.Error as error:
        return error, None
    finally:
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


def debe_fallar(cur, titulo, sentencias, clase, restriccion):
    """
    Comprueba que las sentencias fallan con esa CLASE de error de psycopg2
    y, si se indica, por esa RESTRICCIÓN concreta. Si no sale ningún
    error, es FALLO explícito: la excepción se ha tragado.
    """
    error, _ = ejecutar_en_savepoint(cur, sentencias)
    if error is None:
        comprobar(titulo, False, "(sin error: la excepción se ha tragado)")
        return
    nombre = getattr(error.diag, "constraint_name", None)
    comprobar(
        titulo,
        isinstance(error, clase) and (restriccion is None or nombre == restriccion),
        f"({type(error).__name__}, restricción={nombre})",
    )


def debe_aceptar(cur, titulo, sentencias):
    """Comprueba que las sentencias se ejecutan sin error; devuelve sus filas."""
    error, filas = ejecutar_en_savepoint(cur, sentencias)
    comprobar(titulo, error is None, f"({type(error).__name__}: {error})" if error else f"-> {filas}")
    return filas


def visita(estado=None, fecha="2030-01-07 10:00+01", texto="texto de prueba", op="op1"):
    """
    INSERT de una visita de prueba. op elige una de las dos oportunidades
    de prueba (se buscan por su tipo_reforma, distinto en cada una). Si
    estado es None, el INSERT no lo menciona, para probar el DEFAULT.
    """
    tipo = "bano" if op == "op1" else "cocina"
    sub_op = "(SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id JOIN clientes c ON c.id = l.cliente_id WHERE c.email = %s AND o.tipo_reforma = %s)"
    if estado is None:
        return (
            f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, texto_cliente) VALUES ({sub_op}, %s, %s) RETURNING estado, updated_at IS NOT NULL;",
            (EMAIL_PRUEBA, tipo, fecha, texto),
        )
    return (
        f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) VALUES ({sub_op}, %s, %s, %s) RETURNING estado;",
        (EMAIL_PRUEBA, tipo, fecha, estado, texto),
    )


cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()

try:
    # ==================================================================
    print("=" * 78)
    print("A) ESTRUCTURA")
    print("=" * 78)
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'visitas'::regclass AND conname = %s;",
        (RESTRICCION_ESTADO,),
    )
    check_estado = cur.fetchone()[0]
    print(f"  {RESTRICCION_ESTADO}: {check_estado}")
    comprobar("el CHECK de estado admite 'solicitada'", "'solicitada'" in check_estado)
    comprobar("el CHECK de estado ya no admite 'reservada'", "reservada" not in check_estado)

    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns WHERE table_name = 'visitas';
        """
    )
    columnas = {f[0]: f[1:] for f in cur.fetchall()}
    comprobar("DEFAULT de estado = 'solicitada'", "'solicitada'" in (columnas["estado"][2] or ""), f"({columnas['estado'][2]})")
    comprobar("fecha_propuesta NOT NULL", columnas["fecha_propuesta"][1] == "NO")
    comprobar(
        "texto_cliente TEXT NOT NULL",
        columnas.get("texto_cliente", (None, None))[:2] == ("text", "NO"),
        f"({columnas.get('texto_cliente')})",
    )
    comprobar(
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        columnas.get("updated_at") == ("timestamp with time zone", "NO", "now()"),
        f"({columnas.get('updated_at')})",
    )

    cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s;", (INDICE_ACTIVA,))
    fila = cur.fetchone()
    indice = fila[0] if fila else ""
    print(f"  {INDICE_ACTIVA}: {indice}")
    comprobar(
        "índice único parcial sobre oportunidad_id, solo solicitada/confirmada",
        "UNIQUE" in indice and "(oportunidad_id)" in indice and "WHERE" in indice
        and "'solicitada'" in indice and "'confirmada'" in indice and "cancelada" not in indice,
    )

    cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);", (list(REGLAS_ESPERADAS),))
    reglas = {clave: valor for clave, valor in cur.fetchall()}
    for clave, esperado in REGLAS_ESPERADAS.items():
        comprobar(f"reglas_negocio.{clave} = {esperado}", reglas.get(clave) == esperado, f"({reglas.get(clave)})")

    # ==================================================================
    print("\n" + "=" * 78)
    print("B) COMPORTAMIENTO (todo se deshace al final)")
    print("=" * 78)
    # Datos de partida, FUERA de los savepoints para que todas las pruebas
    # los vean: un cliente, un lead y DOS oportunidades (bano y cocina).
    cur.execute(
        "INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
        ("Prueba paso9", EMAIL_PRUEBA, "600000000"),
    )
    cliente_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, %s, %s) RETURNING id;",
        (cliente_id, "check_paso9", Json({"m2": 10})),
    )
    lead_id = cur.fetchone()[0]
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano'), (%s, 'cocina');", (lead_id, lead_id))
    print(f"  datos de prueba: cliente {cliente_id}, lead {lead_id}, dos oportunidades")

    print("\n  -- Estados --")
    debe_fallar(cur, "estado 'reservada' -> rechazado por el CHECK", [visita("reservada")],
                psycopg2.errors.CheckViolation, RESTRICCION_ESTADO)
    debe_aceptar(cur, "estado 'solicitada' -> aceptado", [visita("solicitada")])
    filas = debe_aceptar(cur, "INSERT sin estado -> DEFAULT 'solicitada' y updated_at relleno", [visita()])
    comprobar("  ...el DEFAULT ha escrito 'solicitada' y updated_at no es NULL", filas == [("solicitada", True)])

    print("\n  -- Una sola visita activa por oportunidad (índice parcial) --")
    debe_fallar(cur, "dos 'solicitada' en la misma oportunidad -> rechazado",
                [visita("solicitada"), visita("solicitada", fecha="2030-01-08 10:00+01")],
                psycopg2.errors.UniqueViolation, INDICE_ACTIVA)
    debe_fallar(cur, "'solicitada' + 'confirmada' en la misma oportunidad -> rechazado",
                [visita("solicitada"), visita("confirmada", fecha="2030-01-08 10:00+01")],
                psycopg2.errors.UniqueViolation, INDICE_ACTIVA)
    debe_aceptar(cur, "'cancelada' + 'cancelada' + 'solicitada' en la misma oportunidad -> aceptado",
                 [visita("cancelada"), visita("cancelada", fecha="2030-01-08 10:00+01"),
                  visita("solicitada", fecha="2030-01-09 10:00+01")])
    debe_aceptar(cur, "'completada' + 'solicitada' en la misma oportunidad -> aceptado",
                 [visita("completada"), visita("solicitada", fecha="2030-01-08 10:00+01")])
    debe_aceptar(cur, "una 'solicitada' en cada una de dos oportunidades -> aceptado",
                 [visita("solicitada", op="op1"), visita("solicitada", op="op2")])

    print("\n  -- NOT NULL y longitud del texto --")
    debe_fallar(cur, "fecha_propuesta NULL -> rechazado", [visita("solicitada", fecha=None)],
                psycopg2.errors.NotNullViolation, None)
    debe_fallar(cur, "texto_cliente NULL -> rechazado", [visita("solicitada", texto=None)],
                psycopg2.errors.NotNullViolation, None)
    debe_fallar(cur, "texto_cliente vacío -> rechazado por el CHECK", [visita("solicitada", texto="")],
                psycopg2.errors.CheckViolation, RESTRICCION_TEXTO)
    debe_fallar(cur, "texto_cliente de 1001 caracteres -> rechazado", [visita("solicitada", texto="x" * 1001)],
                psycopg2.errors.CheckViolation, RESTRICCION_TEXTO)
    debe_aceptar(cur, "texto_cliente de 1000 caracteres -> aceptado", [visita("solicitada", texto="x" * 1000)])
    # "á" ocupa 2 bytes en UTF-8: si el CHECK contara bytes, 1000 "á"
    # serían 2000 y se rechazarían. Cuenta caracteres, como Pydantic.
    debe_aceptar(cur, "1000 caracteres 'á' (2000 bytes) -> aceptado: cuenta caracteres", [visita("solicitada", texto="á" * 1000)])
finally:
    # Deshace TODO: los datos de partida y cualquier resto. Va en finally
    # para que se ejecute aunque una comprobación reviente a mitad.
    cn.rollback()

cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
restos_clientes = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM visitas;")
visitas_total = cur.fetchone()[0]
cn.commit()
cn.close()
print(f"\n  Tras el rollback: clientes de prueba = {restos_clientes}, visitas en la tabla = {visitas_total}")
comprobar("no queda ningún dato de prueba", restos_clientes == 0)

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
