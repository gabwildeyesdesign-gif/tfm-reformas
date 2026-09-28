"""
Verificación real de la migración paso10 (tabla decisiones_gate para POST
/gate-decisions). Plan: docs/Plan_Endpoint_Gate_Decisions.txt, sección 3.

DOS PARTES:
  A) Estructura: columnas, restricciones (UNIQUE, 3 claves foráneas y 4
     CHECK), RLS activado y la fila horas_recordatorio_gate de
     reglas_negocio (valor 24 y descripción en UNA línea).
  B) Comportamiento: se intenta escribir lo que las restricciones deben
     impedir, y lo que deben permitir, con inserciones reales.

Todo ocurre dentro de UNA transacción que se deshace al final (rollback):
no queda ninguna fila de prueba. Cada intento va en su propio SAVEPOINT
(punto de guardado), porque en Postgres una sentencia fallida deja la
transacción entera inutilizada, y sin volver al punto de guardado las
comprobaciones siguientes darían InFailedSqlTransaction (gotcha de
CLAUDE.md).

Una comprobación que ESPERA un error y no lo recibe se marca FALLO de
forma explícita ("la excepción se ha tragado").

PRUEBAS EN NEGATIVO:
  python scripts/check_migracion_decisiones_gate.py --quitar <restricción>
borra esa restricción DENTRO de la transacción, antes de la parte B. Las
comprobaciones que dependen de ella deben FALLAR. Como la transacción se
deshace al final, la tabla real conserva la restricción (se comprueba al
terminar). Restricciones que se pueden quitar: las de RESTRICCIONES_QUITABLES.
"""

# argparse: lee los parámetros de la línea de comandos (--quitar ...).
import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
# Json: le dice a psycopg2 que un diccionario de Python va a una columna JSONB.
from psycopg2.extras import Json

sys.stdout.reconfigure(encoding="utf-8")

# Este script vive en scripts/: la raíz del repositorio está un nivel arriba.
RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

# ----------------------------------------------------------------------
# Nombres esperados (los mismos que crea paso10)
# ----------------------------------------------------------------------
TABLA = "decisiones_gate"
RESTRICCION_UNIQUE = "decisiones_gate_oportunidad_id_key"
CHECK_DECISION = "chk_decisiones_gate_decision"
CHECK_MOTIVO = "chk_decisiones_gate_motivo"
CHECK_INFORME = "chk_decisiones_gate_informe_longitud"
CHECK_COHERENCIA = "chk_decisiones_gate_coherencia"
FK_OPORTUNIDAD = "decisiones_gate_oportunidad_id_fkey"
FK_PRESUPUESTO = "decisiones_gate_presupuesto_id_fkey"
FK_VISITA = "decisiones_gate_visita_id_fkey"
CLAVE_REGLA = "horas_recordatorio_gate"

# Las cinco que se pueden quitar para las pruebas en negativo (el plan
# pide cada CHECK y el UNIQUE).
RESTRICCIONES_QUITABLES = [CHECK_DECISION, CHECK_MOTIVO, CHECK_INFORME, CHECK_COHERENCIA, RESTRICCION_UNIQUE]

NOMBRE_SAVEPOINT = "asercion"
EMAIL_PRUEBA = "check.paso10@example.com"  # dominio reservado: nunca es real

# --quitar es opcional; choices limita los valores a la lista de arriba.
lector = argparse.ArgumentParser(description="Verificación de la migración paso10")
lector.add_argument("--quitar", choices=RESTRICCIONES_QUITABLES, default=None)
ARGS = lector.parse_args()

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: esta función cambia la variable ok del archivo, no una copia.
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
    falle o no, sin dejar la transacción inutilizada. Es una lista porque
    algunas pruebas necesitan que la segunda inserción vea la primera (dos
    decisiones para la misma oportunidad).
    Mismo patrón que check_migracion_visitas.py.
    """
    # SAVEPOINT: marca un punto dentro de la transacción al que se puede volver.
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        filas = None
        for sql, params in sentencias:
            cur.execute(sql, params)
            # cur.description es None si la sentencia no devuelve filas.
            filas = cur.fetchall() if cur.description is not None else None
        return None, filas
    except psycopg2.Error as error:
        return error, None
    finally:
        # finally se ejecuta SIEMPRE, haya habido error o no: se vuelve al
        # punto de guardado (deshaciendo lo de este intento) y se libera.
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


def debe_fallar(cur, titulo, sentencias, clase, restriccion):
    """
    Comprueba que las sentencias fallan con esa CLASE de error y, si se
    indica, por esa RESTRICCIÓN concreta. Si no sale ningún error, es FALLO
    explícito: la excepción se ha tragado.
    """
    error, _ = ejecutar_en_savepoint(cur, sentencias)
    if error is None:
        comprobar(titulo, False, "(sin error: la excepción se ha tragado)")
        return
    # diag.constraint_name: el nombre de la restricción que ha saltado.
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


# Subconsultas que encuentran los datos de partida de la parte B por su
# marca propia (el email de prueba y el tipo de reforma), para no tener
# que pasar ids de una sentencia a otra.
OP_1 = ("(SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
        "JOIN clientes c ON c.id = l.cliente_id WHERE c.email = %s AND o.tipo_reforma = 'bano')")
OP_2 = OP_1.replace("'bano'", "'cocina'")
PRESUPUESTO_1 = f"(SELECT id FROM presupuestos WHERE oportunidad_id = {OP_1})"
PRESUPUESTO_2 = f"(SELECT id FROM presupuestos WHERE oportunidad_id = {OP_2})"
VISITA_1 = f"(SELECT id FROM visitas WHERE oportunidad_id = {OP_1})"


def decision(dec, motivo=None, informe="Informe de prueba", con_visita=False, op=1, visita_id=None):
    """
    INSERT de una decisión de prueba, saltándose Pydantic a propósito (lo
    que se prueba es la base de datos). op elige la oportunidad 1 o 2;
    con_visita usa la visita de la oportunidad 1; visita_id permite forzar
    un id concreto (para probar la clave foránea).
    """
    op_sql, pres_sql = (OP_1, PRESUPUESTO_1) if op == 1 else (OP_2, PRESUPUESTO_2)
    if visita_id is not None:
        visita_sql, params_visita = "%s", (visita_id,)
    elif con_visita:
        visita_sql, params_visita = VISITA_1, (EMAIL_PRUEBA,)
    else:
        visita_sql, params_visita = "NULL", ()
    sql = (f"INSERT INTO {TABLA} (oportunidad_id, presupuesto_id, visita_id, decision, motivo, informe) "
           f"VALUES ({op_sql}, {pres_sql}, {visita_sql}, %s, %s, %s) RETURNING decision;")
    # Los parámetros van en el mismo orden que los %s del texto.
    return sql, (EMAIL_PRUEBA, EMAIL_PRUEBA) + params_visita + (dec, motivo, informe)


cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()

try:
    # ==================================================================
    print("=" * 78)
    print("A) ESTRUCTURA")
    print("=" * 78)
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position;
        """,
        (TABLA,),
    )
    columnas = {f[0]: f[1:] for f in cur.fetchall()}
    for nombre, datos in columnas.items():
        print(f"    {nombre:16} {datos}")
    # (tipo, ¿admite NULL?) de cada columna, según el plan.
    esperadas = {
        "id": ("integer", "NO"),
        "oportunidad_id": ("integer", "NO"),
        "presupuesto_id": ("integer", "NO"),
        "visita_id": ("integer", "YES"),
        "decision": ("character varying", "NO"),
        "motivo": ("character varying", "YES"),
        "informe": ("text", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    }
    comprobar("las 8 columnas, con su tipo y NULL/NOT NULL",
              {n: d[:2] for n, d in columnas.items()} == esperadas)
    comprobar("created_at tiene DEFAULT now()", columnas.get("created_at", (None,) * 3)[2] == "now()")

    cur.execute(
        "SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass;",
        (TABLA,),
    )
    # contype: 'p' clave primaria, 'u' unique, 'f' foreign key, 'c' check.
    restr = {f[0]: (f[1], f[2]) for f in cur.fetchall()}
    for nombre, (tipo, definicion) in sorted(restr.items()):
        print(f"    {nombre} [{tipo}]: {definicion}")
    comprobar("UNIQUE (oportunidad_id)", restr.get(RESTRICCION_UNIQUE, ("", ""))[0] == "u"
              and "(oportunidad_id)" in restr[RESTRICCION_UNIQUE][1])
    for fk, destino in [(FK_OPORTUNIDAD, "oportunidades(id)"), (FK_PRESUPUESTO, "presupuestos(id)"), (FK_VISITA, "visitas(id)")]:
        comprobar(f"clave foránea {fk} -> {destino}", restr.get(fk, ("", ""))[0] == "f" and destino in restr[fk][1])
    for chk in (CHECK_DECISION, CHECK_MOTIVO, CHECK_INFORME, CHECK_COHERENCIA):
        comprobar(f"CHECK {chk}", restr.get(chk, ("", ""))[0] == "c")

    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (TABLA,))
    comprobar("RLS activado en decisiones_gate", cur.fetchone()[0] is True)

    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (CLAVE_REGLA,))
    fila = cur.fetchone()
    print(f"    {CLAVE_REGLA}: {fila[0] if fila else None}  {fila[1]!r}" if fila else f"    {CLAVE_REGLA}: no existe")
    comprobar("horas_recordatorio_gate = 24", fila is not None and fila[0] == 24)
    comprobar("su descripción es UNA línea, sin saltos ni sangría",
              fila is not None and "\n" not in fila[1] and "  " not in fila[1] and fila[1] == fila[1].strip())

    # ==================================================================
    print("\n" + "=" * 78)
    print("B) COMPORTAMIENTO (todo se deshace al final)")
    print("=" * 78)
    # Datos de partida, FUERA de los savepoints para que todas las pruebas
    # los vean: un cliente, un lead, dos oportunidades (bano y cocina),
    # un presupuesto para cada una y una visita en la primera.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Prueba paso10", EMAIL_PRUEBA, "600000000"))
    cliente_id = cur.fetchone()[0]
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, %s, %s) RETURNING id;",
                (cliente_id, "check_paso10", Json({"m2": 10})))
    lead_id = cur.fetchone()[0]
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano'), (%s, 'cocina');", (lead_id, lead_id))
    cur.execute(f"INSERT INTO presupuestos (oportunidad_id, requiere_aprobacion) VALUES ({OP_1}, true), ({OP_2}, true);",
                (EMAIL_PRUEBA, EMAIL_PRUEBA))
    cur.execute(f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                f"VALUES ({OP_1}, '2030-01-07 08:30+01', 'confirmada', 'prueba paso10');", (EMAIL_PRUEBA,))
    print(f"  datos de prueba: cliente {cliente_id}, lead {lead_id}, dos oportunidades con presupuesto y una visita")

    # Prueba en negativo: se quita la restricción pedida DENTRO de la
    # transacción. DROP CONSTRAINT también se deshace con el rollback final.
    if ARGS.quitar:
        cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT {ARGS.quitar};")
        print(f"  PRUEBA EN NEGATIVO: {ARGS.quitar} borrada DENTRO de la transacción")

    print("\n  -- Valores de las listas cerradas --")
    debe_fallar(cur, "decision 'continuar' -> rechazada", [decision("continuar", motivo="precio")],
                psycopg2.errors.CheckViolation, CHECK_DECISION)
    debe_fallar(cur, "motivo 'caro' -> rechazado", [decision("descartar", motivo="caro")],
                psycopg2.errors.CheckViolation, CHECK_MOTIVO)

    print("\n  -- Informe --")
    debe_fallar(cur, "informe vacío -> rechazado", [decision("descartar", motivo="precio", informe="")],
                psycopg2.errors.CheckViolation, CHECK_INFORME)
    debe_fallar(cur, "informe de 2001 caracteres -> rechazado", [decision("descartar", motivo="precio", informe="x" * 2001)],
                psycopg2.errors.CheckViolation, CHECK_INFORME)
    debe_fallar(cur, "informe NULL -> rechazado", [decision("descartar", motivo="precio", informe=None)],
                psycopg2.errors.NotNullViolation, None)
    debe_aceptar(cur, "informe de 2000 caracteres -> aceptado", [decision("descartar", motivo="precio", informe="x" * 2000)])
    # "á" ocupa 2 bytes: si el CHECK contara bytes, se rechazaría.
    debe_aceptar(cur, "informe de 2000 'á' (4000 bytes) -> aceptado: cuenta caracteres",
                 [decision("descartar", motivo="precio", informe="á" * 2000)])

    print("\n  -- Coherencia entre decision, motivo y visita_id --")
    debe_fallar(cur, "visita_acordada SIN visita_id -> rechazada", [decision("visita_acordada")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "visita_acordada CON motivo -> rechazada", [decision("visita_acordada", motivo="precio", con_visita=True)],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "descartar SIN motivo -> rechazada", [decision("descartar")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "descartar CON visita_id -> rechazada", [decision("descartar", motivo="precio", con_visita=True)],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_aceptar(cur, "visita_acordada con visita_id y sin motivo -> aceptada", [decision("visita_acordada", con_visita=True)])
    debe_aceptar(cur, "descartar con motivo y sin visita_id -> aceptada", [decision("descartar", motivo="no_contesta")])

    print("\n  -- Una decisión por oportunidad (UNIQUE) --")
    debe_fallar(cur, "dos decisiones para la misma oportunidad -> rechazada",
                [decision("descartar", motivo="precio"), decision("descartar", motivo="plazo")],
                psycopg2.errors.UniqueViolation, RESTRICCION_UNIQUE)
    debe_aceptar(cur, "una decisión en cada una de dos oportunidades -> aceptado",
                 [decision("descartar", motivo="precio", op=1), decision("descartar", motivo="otro", op=2)])

    print("\n  -- Claves foráneas --")
    debe_fallar(cur, "visita_id que no existe -> rechazada", [decision("visita_acordada", visita_id=999999999)],
                psycopg2.errors.ForeignKeyViolation, FK_VISITA)
    debe_fallar(cur, "oportunidad que no existe -> rechazada",
                [(f"INSERT INTO {TABLA} (oportunidad_id, presupuesto_id, decision, motivo, informe) "
                  f"VALUES (999999999, {PRESUPUESTO_1}, 'descartar', 'precio', 'x');", (EMAIL_PRUEBA,))],
                psycopg2.errors.ForeignKeyViolation, FK_OPORTUNIDAD)
finally:
    # Deshace TODO: datos de partida, restricción borrada (si la hubo) y
    # cualquier resto. Va en finally para que ocurra aunque algo reviente.
    cn.rollback()

# Comprobaciones finales, ya fuera de la transacción de prueba.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
restos = cur.fetchone()[0]
cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass;", (TABLA,))
presentes = {f[0] for f in cur.fetchall()}
cur.execute(f"SELECT count(*) FROM {TABLA};")
filas_tabla = cur.fetchone()[0]
cn.commit()
cn.close()
print(f"\n  Tras el rollback: clientes de prueba = {restos}, filas en {TABLA} = {filas_tabla}")
comprobar("no queda ningún dato de prueba", restos == 0)
comprobar("la tabla real conserva sus 5 restricciones quitables", set(RESTRICCIONES_QUITABLES) <= presentes,
          f"(faltan: {set(RESTRICCIONES_QUITABLES) - presentes})")

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas" + (f"  (prueba en negativo: sin {ARGS.quitar})" if ARGS.quitar else ""))
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
