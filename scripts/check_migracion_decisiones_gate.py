"""
Verificación real de la migración paso10 (tabla decisiones_gate para POST
/gate-decisions). Plan: docs/Plan_Endpoint_Gate_Decisions.txt, sección 3.

DOS PARTES:
  A) Estructura: la restricción nueva de visitas (UNIQUE (id,
     oportunidad_id)), las 7 columnas de decisiones_gate (sin
     presupuesto_id), sus restricciones (UNIQUE, clave foránea a
     oportunidades, clave foránea DOBLE a visitas y 4 CHECK, con la
     coherencia en su versión de paso10b), RLS activado
     y la fila horas_recordatorio_gate de reglas_negocio (valor 24 y
     descripción en UNA línea).
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
# os: para leer variables de entorno (os.getenv).
import os
# sys: para la codificación de la consola y para sys.exit con el resultado.
import sys
# Path: para construir rutas de archivos sin escribirlas a mano.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# load_dotenv: lee el .env y mete sus valores como variables de entorno.
from dotenv import load_dotenv
# Json: le dice a psycopg2 que un diccionario de Python va a una columna JSONB.
from psycopg2.extras import Json

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# Este script vive en scripts/: la raíz del repositorio está un nivel arriba.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Carga el .env de la raíz y lee la cadena de conexión.
load_dotenv(RAIZ_REPO / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

# ----------------------------------------------------------------------
# Nombres esperados (los mismos que crea paso10)
# ----------------------------------------------------------------------
# En constantes para no repetir el texto: una errata fallaría en todos los
# sitios a la vez y se vería enseguida.
TABLA = "decisiones_gate"
UNIQUE_VISITAS = "visitas_id_oportunidad_id_key"
RESTRICCION_UNIQUE = "decisiones_gate_oportunidad_id_key"
CHECK_DECISION = "chk_decisiones_gate_decision"
CHECK_MOTIVO = "chk_decisiones_gate_motivo"
CHECK_INFORME = "chk_decisiones_gate_informe"
CHECK_COHERENCIA = "chk_decisiones_gate_coherencia"
FK_OPORTUNIDAD = "decisiones_gate_oportunidad_id_fkey"
FK_VISITA_DOBLE = "decisiones_gate_visita_oportunidad_fkey"
CLAVE_REGLA = "horas_recordatorio_gate"

# Las que se pueden quitar para las pruebas en negativo: cada CHECK, el
# UNIQUE y la clave doble. (La UNIQUE de visitas no está: no se puede
# borrar mientras la clave doble dependa de ella.)
RESTRICCIONES_QUITABLES = [CHECK_DECISION, CHECK_MOTIVO, CHECK_INFORME, CHECK_COHERENCIA,
                           RESTRICCION_UNIQUE, FK_VISITA_DOBLE]

# Nombre del punto de guardado que usa cada intento de la parte B.
NOMBRE_SAVEPOINT = "asercion"
# La marca propia de este script: el email del cliente de prueba.
EMAIL_PRUEBA = "check.paso10@example.com"  # dominio reservado: nunca es real

# --quitar es opcional; choices limita los valores a la lista de arriba.
lector = argparse.ArgumentParser(description="Verificación de la migración paso10")
lector.add_argument("--quitar", choices=RESTRICCIONES_QUITABLES, default=None)
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
        # Si ninguna sentencia devuelve filas, se queda en None.
        filas = None
        # Cada elemento de la lista es una pareja (texto SQL, parámetros).
        for sql, params in sentencias:
            cur.execute(sql, params)
            # cur.description es None si la sentencia no devuelve filas.
            filas = cur.fetchall() if cur.description is not None else None
        # Todo bien: sin error, y las filas de la última sentencia.
        return None, filas
    # Cualquier error de Postgres se DEVUELVE (no se lanza) para examinarlo.
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
    # El "_" recoge las filas, que aquí no interesan.
    error, _ = ejecutar_en_savepoint(cur, sentencias)
    # Se esperaba un error y no lo hubo: FALLO explícito.
    if error is None:
        comprobar(titulo, False, "(sin error: la excepción se ha tragado)")
        return
    # diag.constraint_name: el nombre de la restricción que ha saltado.
    nombre = getattr(error.diag, "constraint_name", None)
    # Correcto solo si el error es de la clase esperada y, si se pidió, lo
    # provocó esa restricción concreta (no otra por casualidad).
    comprobar(
        titulo,
        isinstance(error, clase) and (restriccion is None or nombre == restriccion),
        f"({type(error).__name__}, restricción={nombre})",
    )


def debe_aceptar(cur, titulo, sentencias):
    """Comprueba que las sentencias se ejecutan sin error; devuelve sus filas."""
    error, filas = ejecutar_en_savepoint(cur, sentencias)
    # Correcto si no hubo error. El detalle enseña el error o las filas.
    comprobar(titulo, error is None, f"({type(error).__name__}: {error})" if error else f"-> {filas}")
    return filas


# Subconsultas que encuentran los datos de partida de la parte B por su
# marca propia (el email de prueba y el tipo de reforma), para no tener
# que pasar ids de una sentencia a otra. Cada una lleva UN %s (el email).
# OP_1: la oportunidad 'bano' del cliente de prueba. OP_2: la misma
# subconsulta cambiando 'bano' por 'cocina'. VISITA_DE: la visita de cada una.
OP_1 = ("(SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
        "JOIN clientes c ON c.id = l.cliente_id WHERE c.email = %s AND o.tipo_reforma = 'bano')")
OP_2 = OP_1.replace("'bano'", "'cocina'")
VISITA_DE = {1: f"(SELECT id FROM visitas WHERE oportunidad_id = {OP_1})",
             2: f"(SELECT id FROM visitas WHERE oportunidad_id = {OP_2})"}


def decision(dec, motivo=None, informe="Informe de prueba", op=1, visita=None, visita_id=None):
    """
    INSERT de una decisión de prueba, saltándose Pydantic a propósito (lo
    que se prueba es la base de datos).
      op:        la oportunidad de la decisión (1 o 2).
      visita:    la visita propia de la oportunidad 1 o 2 (o None).
      visita_id: un id concreto, para probar un id que no existe.
    """
    # La subconsulta de la oportunidad elegida.
    op_sql = OP_1 if op == 1 else OP_2
    # Qué se escribe en visita_id, y con qué parámetros:
    #   un id concreto -> un %s con ese id;
    if visita_id is not None:
        visita_sql, params_visita = "%s", (visita_id,)
    #   la visita propia de una oportunidad -> su subconsulta (lleva el email);
    elif visita is not None:
        visita_sql, params_visita = VISITA_DE[visita], (EMAIL_PRUEBA,)
    #   ninguna -> NULL, sin parámetros.
    else:
        visita_sql, params_visita = "NULL", ()
    # El texto del INSERT, con las subconsultas ya puestas en su sitio.
    # RETURNING decision devuelve la decisión guardada si entra.
    sql = (f"INSERT INTO {TABLA} (oportunidad_id, visita_id, decision, motivo, informe) "
           f"VALUES ({op_sql}, {visita_sql}, %s, %s, %s) RETURNING decision;")
    # Los parámetros van en el mismo orden que los %s del texto.
    return sql, (EMAIL_PRUEBA,) + params_visita + (dec, motivo, informe)


# Una sola conexión y su cursor para todo el script.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()

# try / finally: el rollback del final se ejecuta aunque algo reviente.
try:
    # ==================================================================
    print("=" * 78)
    print("A) ESTRUCTURA")
    print("=" * 78)
    # La restricción nueva de visitas (paso 1 de la migración).
    # SQL: el tipo (contype) y la definición de esa restricción de visitas.
    cur.execute(
        "SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'visitas'::regclass AND conname = %s;",
        (UNIQUE_VISITAS,),
    )
    fila = cur.fetchone()
    print(f"    visitas.{UNIQUE_VISITAS}: {fila}")
    # Tiene que existir, ser de tipo 'u' (unique) y cubrir esas dos columnas.
    comprobar("visitas tiene UNIQUE (id, oportunidad_id)", fila is not None and fila[0] == "u" and "(id, oportunidad_id)" in fila[1])

    # SQL: nombre, tipo, si admite NULL y valor por defecto de cada columna
    # de decisiones_gate, en el orden en que se definieron.
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position;
        """,
        (TABLA,),
    )
    # Diccionario nombre -> (tipo, NULL, defecto). f[1:] es la fila sin su
    # primer elemento.
    columnas = {f[0]: f[1:] for f in cur.fetchall()}
    for nombre, datos in columnas.items():
        print(f"    {nombre:16} {datos}")
    # (tipo, ¿admite NULL?) de cada columna, según el plan. Sin presupuesto_id.
    esperadas = {
        "id": ("integer", "NO"),
        "oportunidad_id": ("integer", "NO"),
        "visita_id": ("integer", "YES"),
        "decision": ("character varying", "NO"),
        "motivo": ("character varying", "YES"),
        "informe": ("text", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    }
    # d[:2] se queda con (tipo, NULL); el diccionario entero tiene que ser
    # IGUAL al esperado: ni una columna de más ni de menos.
    comprobar("las 7 columnas, con su tipo y NULL/NOT NULL (sin presupuesto_id)",
              {n: d[:2] for n, d in columnas.items()} == esperadas)
    # El tercer dato de created_at es su valor por defecto. Si la columna no
    # existiera, .get devuelve tres None y la comprobación falla sin error.
    comprobar("created_at tiene DEFAULT now()", columnas.get("created_at", (None,) * 3)[2] == "now()")

    # SQL: nombre, tipo y definición de todas las restricciones de la tabla.
    cur.execute(
        "SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass;",
        (TABLA,),
    )
    # contype: 'p' clave primaria, 'u' unique, 'f' foreign key, 'c' check.
    restr = {f[0]: (f[1], f[2]) for f in cur.fetchall()}
    for nombre, (tipo, definicion) in sorted(restr.items()):
        print(f"    {nombre} [{tipo}]: {definicion}")
    # Cada comprobación usa .get(nombre, ("", "")) para que una restricción
    # que falte dé FALLO en vez de un KeyError que pare el script.
    comprobar("UNIQUE (oportunidad_id)", restr.get(RESTRICCION_UNIQUE, ("", ""))[0] == "u"
              and "(oportunidad_id)" in restr[RESTRICCION_UNIQUE][1])
    comprobar("clave foránea oportunidad_id -> oportunidades(id)", restr.get(FK_OPORTUNIDAD, ("", ""))[0] == "f"
              and "oportunidades(id)" in restr[FK_OPORTUNIDAD][1])
    comprobar("clave foránea DOBLE (visita_id, oportunidad_id) -> visitas(id, oportunidad_id)",
              restr.get(FK_VISITA_DOBLE, ("", ""))[0] == "f"
              and "(visita_id, oportunidad_id)" in restr[FK_VISITA_DOBLE][1]
              and "visitas(id, oportunidad_id)" in restr[FK_VISITA_DOBLE][1])
    # Exactamente dos claves foráneas: ni la simple de visita_id ni la de
    # presupuesto_id del diseño anterior.
    comprobar("exactamente 2 claves foráneas", sum(1 for t, _ in restr.values() if t == "f") == 2,
              f"({sorted(n for n, (t, _) in restr.items() if t == 'f')})")
    # Los cuatro CHECK tienen que existir y ser de tipo 'c'.
    for chk in (CHECK_DECISION, CHECK_MOTIVO, CHECK_INFORME, CHECK_COHERENCIA):
        comprobar(f"CHECK {chk}", restr.get(chk, ("", ""))[0] == "c")
    # Coherencia, versión de paso10b: "si es X, entonces ..." con <>, sin
    # repetir qué valores existen (eso es cosa de chk_decisiones_gate_decision).
    # La versión antigua (paso10) contenía = 'visita_acordada'; la nueva no.
    # No basta con mirar que no haya una lista IN (...): la antigua tampoco
    # la tenía (enumeraba los valores con dos ramas unidas por OR).
    definicion_coherencia = restr.get(CHECK_COHERENCIA, ("", ""))[1]
    comprobar("el CHECK de coherencia es el de paso10b (usa <> y no repite los valores)",
              "<> 'visita_acordada'" in definicion_coherencia
              and "<> 'descartar'" in definicion_coherencia
              and "= 'visita_acordada'" not in definicion_coherencia.replace("<> 'visita_acordada'", ""),
              f"({definicion_coherencia})")
    # El CHECK del informe tiene que contener sus dos condiciones.
    comprobar("el CHECK del informe exige texto recortado (btrim) y longitud",
              "btrim(informe)" in restr.get(CHECK_INFORME, ("", ""))[1]
              and "char_length(informe)" in restr.get(CHECK_INFORME, ("", ""))[1])

    # SQL: el interruptor de RLS de la tabla (pg_class.relrowsecurity).
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (TABLA,))
    comprobar("RLS activado en decisiones_gate", cur.fetchone()[0] is True)

    # SQL: valor y descripción de la regla horas_recordatorio_gate.
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (CLAVE_REGLA,))
    fila = cur.fetchone()
    # !r enseña el texto con sus caracteres especiales visibles (\n).
    print(f"    {CLAVE_REGLA}: {fila[0]}  {fila[1]!r}" if fila else f"    {CLAVE_REGLA}: no existe")
    # Decimal('24.00') == 24 es True: Python compara el valor numérico.
    comprobar("horas_recordatorio_gate = 24", fila is not None and fila[0] == 24)
    # Una línea: sin saltos, sin dobles espacios (sangría) y sin espacios
    # en los extremos.
    comprobar("su descripción es UNA línea, sin saltos ni sangría",
              fila is not None and "\n" not in fila[1] and "  " not in fila[1] and fila[1] == fila[1].strip())

    # ==================================================================
    print("\n" + "=" * 78)
    print("B) COMPORTAMIENTO (todo se deshace al final)")
    print("=" * 78)
    # Datos de partida, FUERA de los savepoints para que todas las pruebas
    # los vean: un cliente, un lead, dos oportunidades (bano y cocina) y
    # una visita en CADA oportunidad (para probar la clave doble).
    # SQL: el cliente de prueba, con el email marca; devuelve su id.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Prueba paso10", EMAIL_PRUEBA, "600000000"))
    cliente_id = cur.fetchone()[0]
    # SQL: su lead (m2 = 10, dentro del rango del CHECK de leads).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, %s, %s) RETURNING id;",
                (cliente_id, "check_paso10", Json({"m2": 10})))
    lead_id = cur.fetchone()[0]
    # SQL: dos oportunidades del mismo lead en un solo INSERT (bano y cocina).
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano'), (%s, 'cocina');", (lead_id, lead_id))
    # SQL: una visita 'confirmada' para cada oportunidad, encontrada por su
    # subconsulta (cada una lleva un %s con el email).
    cur.execute(f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                f"VALUES ({OP_1}, '2030-01-07 08:30+01', 'confirmada', 'prueba paso10 op1'), "
                f"({OP_2}, '2030-01-08 08:30+01', 'confirmada', 'prueba paso10 op2');",
                (EMAIL_PRUEBA, EMAIL_PRUEBA))
    print(f"  datos de prueba: cliente {cliente_id}, lead {lead_id}, dos oportunidades con una visita cada una")

    # Prueba en negativo: se quita la restricción pedida DENTRO de la
    # transacción. DROP CONSTRAINT también se deshace con el rollback final.
    if ARGS.quitar:
        # SQL: borra la restricción pedida (solo dentro de esta transacción).
        cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT {ARGS.quitar};")
        print(f"  PRUEBA EN NEGATIVO: {ARGS.quitar} borrada DENTRO de la transacción")

    print("\n  -- Valores de las listas cerradas --")
    debe_fallar(cur, "decision 'continuar' -> rechazada", [decision("continuar", motivo="precio")],
                psycopg2.errors.CheckViolation, CHECK_DECISION)
    debe_fallar(cur, "motivo 'caro' -> rechazado", [decision("descartar", motivo="caro")],
                psycopg2.errors.CheckViolation, CHECK_MOTIVO)

    print("\n  -- Informe (defensa doble: recortado y de 1 a 2000 caracteres) --")
    debe_fallar(cur, "informe vacío -> rechazado", [decision("descartar", motivo="precio", informe="")],
                psycopg2.errors.CheckViolation, CHECK_INFORME)
    debe_fallar(cur, "informe de solo espacios '   ' -> rechazado", [decision("descartar", motivo="precio", informe="   ")],
                psycopg2.errors.CheckViolation, CHECK_INFORME)
    debe_fallar(cur, "informe con espacios en los extremos ' texto ' -> rechazado",
                [decision("descartar", motivo="precio", informe=" texto ")],
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
    debe_fallar(cur, "visita_acordada CON motivo -> rechazada", [decision("visita_acordada", motivo="precio", visita=1)],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "descartar SIN motivo -> rechazada", [decision("descartar")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "descartar CON visita_id -> rechazada", [decision("descartar", motivo="precio", visita=1)],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_aceptar(cur, "visita_acordada con SU visita y sin motivo -> aceptada", [decision("visita_acordada", visita=1)])
    # Descartar: visita_id NULL, así que la clave doble no se comprueba
    # (MATCH SIMPLE) y la fila entra.
    debe_aceptar(cur, "descartar con motivo y sin visita_id -> aceptada (la clave doble no se mira)",
                 [decision("descartar", motivo="no_contesta")])

    print("\n  -- Una decisión por oportunidad (UNIQUE) --")
    debe_fallar(cur, "dos decisiones para la misma oportunidad -> rechazada",
                [decision("descartar", motivo="precio"), decision("descartar", motivo="plazo")],
                psycopg2.errors.UniqueViolation, RESTRICCION_UNIQUE)
    debe_aceptar(cur, "una decisión en cada una de dos oportunidades -> aceptado",
                 [decision("descartar", motivo="precio", op=1), decision("descartar", motivo="otro", op=2)])

    print("\n  -- Claves foráneas --")
    debe_fallar(cur, "decisión de la oportunidad 1 con la visita de la oportunidad 2 -> rechazada por la clave doble",
                [decision("visita_acordada", op=1, visita=2)],
                psycopg2.errors.ForeignKeyViolation, FK_VISITA_DOBLE)
    debe_fallar(cur, "visita_id que no existe -> rechazada", [decision("visita_acordada", visita_id=999999999)],
                psycopg2.errors.ForeignKeyViolation, FK_VISITA_DOBLE)
    # Esta no usa decision(): escribe un oportunidad_id fijo que no existe.
    debe_fallar(cur, "oportunidad que no existe -> rechazada",
                [(f"INSERT INTO {TABLA} (oportunidad_id, decision, motivo, informe) "
                  f"VALUES (999999999, 'descartar', 'precio', 'x');", None)],
                psycopg2.errors.ForeignKeyViolation, FK_OPORTUNIDAD)
finally:
    # Deshace TODO: datos de partida, restricción borrada (si la hubo) y
    # cualquier resto. Va en finally para que ocurra aunque algo reviente.
    cn.rollback()

# Comprobaciones finales, ya fuera de la transacción de prueba.
# SQL: cuántos clientes quedan con el email marca (debe ser 0).
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
restos = cur.fetchone()[0]
# SQL: los nombres de las restricciones que tiene AHORA la tabla real.
cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass;", (TABLA,))
presentes = {f[0] for f in cur.fetchall()}
# SQL: filas totales de la tabla. Solo se enseña, no se exige nada: puede
# haber decisiones reales.
cur.execute(f"SELECT count(*) FROM {TABLA};")
filas_tabla = cur.fetchone()[0]
# commit cierra la transacción de lectura; después se cierra la conexión.
cn.commit()
cn.close()
print(f"\n  Tras el rollback: clientes de prueba = {restos}, filas en {TABLA} = {filas_tabla}")
comprobar("no queda ningún dato de prueba", restos == 0)
# "<=" entre conjuntos: ¿están TODAS las quitables entre las presentes?
comprobar("la tabla real conserva sus 6 restricciones quitables", set(RESTRICCIONES_QUITABLES) <= presentes,
          f"(faltan: {set(RESTRICCIONES_QUITABLES) - presentes})")

# Resumen: correctas sobre el total, y si era una prueba en negativo.
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas" + (f"  (prueba en negativo: sin {ARGS.quitar})" if ARGS.quitar else ""))
print("=" * 78)
# Si hubo fallos, se listan y el script termina con código 1 (error), que
# es lo que mira la suite.
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
