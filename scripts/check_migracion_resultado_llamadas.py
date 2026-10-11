"""
Verificación real de la migración paso12 (decisiones_gate para el resultado
de los tres tipos de llamada). Plan: docs/Plan_Resultado_Llamadas.txt,
secciones 4.1 y 4.3.

DOS PARTES:
  A) Estructura: las dos columnas nuevas (tipo_llamada con DEFAULT 'gate',
     visita_cancelada_id), la clave foránea doble de la visita cancelada,
     los cinco CHECK (tipo, decision y motivo ampliados, coherencia y
     tipo_resultado), el COMMENT ON TABLE, lo que NO cambia (UNIQUE, clave
     doble de visita_id, CHECK del informe, RLS) y que TODAS las filas
     reales siguen siendo del Gate y sin visita cancelada.
  B) Comportamiento: se intenta escribir lo que las restricciones deben
     impedir, y lo que deben permitir, con inserciones reales.

Antes de migrar, el script tiene que FALLAR en lo que paso12 añade (se
enseña N_antes/N): así se ve que detecta lo que falta.

Todo ocurre dentro de UNA transacción que se deshace al final (rollback):
no queda ninguna fila de prueba. Cada intento va en su propio SAVEPOINT,
porque en Postgres una sentencia fallida deja la transacción inutilizada
(gotcha de CLAUDE.md). Una comprobación que ESPERA un error y no lo recibe
es FALLO explícito ("la excepción se ha tragado").

Las 4 preguntas: (1) escribe en tablas reales SOLO dentro de esta
transacción, y la deshace siempre (finally); (2) no pasa su conexión a
ningún código de la aplicación (solo SQL de este script); (3) si se corta,
Postgres deshace la transacción abierta solo; la comprobación final mira que
no queda el cliente de prueba; (4) no toca el 8000 ni ninguna fila real
(lee las reales, nunca las cambia).

PRUEBAS EN NEGATIVO:
  python scripts/check_migracion_resultado_llamadas.py --quitar <restricción>
borra esa restricción DENTRO de la transacción, antes de la parte B. Las
comprobaciones que dependen de ella deben FALLAR. La tabla real la conserva.
"""

# argparse: lee --quitar. os: el entorno. sys: consola y código de salida.
import argparse
import os
import sys
# Path: rutas sin escribirlas a mano.
from pathlib import Path

# psycopg2: hablar con PostgreSQL. load_dotenv: leer el .env. Json: un
# diccionario de Python como JSONB.
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# Este script vive en scripts/: la raíz está un nivel arriba.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Carga el .env de la raíz y lee la cadena de conexión.
load_dotenv(RAIZ_REPO / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

# ----------------------------------------------------------------------
# Nombres esperados (los mismos que crea paso12)
# ----------------------------------------------------------------------
# La tabla.
TABLA = "decisiones_gate"
# Los CHECK que añade o amplía paso12 (y el del informe, que no cambia).
CHECK_TIPO = "chk_decisiones_gate_tipo_llamada"
CHECK_DECISION = "chk_decisiones_gate_decision"
CHECK_MOTIVO = "chk_decisiones_gate_motivo"
# Qué acompaña a cada resultado, y qué resultados tiene cada tipo.
CHECK_COHERENCIA = "chk_decisiones_gate_coherencia"
CHECK_TIPO_RESULTADO = "chk_decisiones_gate_tipo_resultado"
CHECK_INFORME = "chk_decisiones_gate_informe"
# Las claves foráneas (la de la visita cancelada es nueva) y el UNIQUE.
FK_CANCELADA = "decisiones_gate_visita_cancelada_oportunidad_fkey"
FK_VISITA_DOBLE = "decisiones_gate_visita_oportunidad_fkey"
FK_OPORTUNIDAD = "decisiones_gate_oportunidad_id_fkey"
RESTRICCION_UNIQUE = "decisiones_gate_oportunidad_id_key"

# Las que se pueden quitar para las pruebas en negativo.
RESTRICCIONES_QUITABLES = [CHECK_TIPO, CHECK_DECISION, CHECK_MOTIVO, CHECK_COHERENCIA,
                           CHECK_TIPO_RESULTADO, FK_CANCELADA]

# Punto de guardado de cada intento, y la marca propia (dominio reservado).
NOMBRE_SAVEPOINT = "asercion"
EMAIL_PRUEBA = "check.paso12@example.com"

# --quitar es opcional; choices limita los valores a la lista de arriba.
lector = argparse.ArgumentParser(description="Verificación de la migración paso12")
lector.add_argument("--quitar", choices=RESTRICCIONES_QUITABLES, default=None)
ARGS = lector.parse_args()

# Contador de correctas y lista de las fallidas.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: esta función cambia la variable ok del archivo.
    global ok
    # Verdadera: una correcta más y [OK].
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    # Falsa: se apunta y [FALLO].
    else:
        fallos.append(titulo)
        print(f"  [FALLO] {titulo}  {detalle}")


def ejecutar_en_savepoint(cur, sentencias):
    """
    Ejecuta una LISTA de (sql, params) en un mismo punto de guardado y
    devuelve (error, filas de la última). Lo deshace todo al terminar,
    falle o no (patrón de check_migracion_decisiones_gate).
    """
    # SAVEPOINT: un punto al que se puede volver.
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        # Si ninguna devuelve filas, se queda en None.
        filas = None
        # Cada sentencia, con sus parámetros.
        for sql, params in sentencias:
            cur.execute(sql, params)
            # description es None si la sentencia no devuelve filas.
            filas = cur.fetchall() if cur.description is not None else None
        # Sin error, y las filas de la última.
        return None, filas
    # Un error de Postgres se DEVUELVE para examinarlo.
    except psycopg2.Error as error:
        return error, None
    finally:
        # Siempre: volver al punto de guardado y liberarlo.
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


def debe_fallar(cur, titulo, sentencias, clase, restriccion):
    """
    Las sentencias tienen que fallar con esa CLASE de error y, si se indica,
    por esa RESTRICCIÓN. Sin error: FALLO explícito (se ha tragado).
    """
    # El "_" recoge las filas, que aquí no interesan.
    error, _ = ejecutar_en_savepoint(cur, sentencias)
    # Se esperaba un error y no lo hubo.
    if error is None:
        comprobar(titulo, False, "(sin error: la excepción se ha tragado)")
        return
    # El nombre de la restricción que saltó (o None).
    nombre = getattr(error.diag, "constraint_name", None)
    # Correcto solo con la clase y la restricción esperadas.
    comprobar(titulo, isinstance(error, clase) and (restriccion is None or nombre == restriccion),
              f"({type(error).__name__}, restricción={nombre})")


def debe_aceptar(cur, titulo, sentencias):
    """Las sentencias se tienen que ejecutar sin error; devuelve sus filas."""
    error, filas = ejecutar_en_savepoint(cur, sentencias)
    # Correcto si no hubo error; el detalle enseña el error o las filas.
    comprobar(titulo, error is None, f"({type(error).__name__}: {str(error).splitlines()[0]})" if error else f"-> {filas}")
    return filas


# Subconsultas que encuentran los datos de partida por su marca (el email y
# el tipo de reforma). Cada una lleva UN %s (el email).
OP_1 = ("(SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
        "JOIN clientes c ON c.id = l.cliente_id WHERE c.email = %s AND o.tipo_reforma = 'bano')")
OP_2 = OP_1.replace("'bano'", "'cocina'")
# Las visitas de partida, por su texto: A y B de la oportunidad 1, C de la 2.
VISITA = {letra: f"(SELECT id FROM visitas WHERE texto_cliente = 'prueba paso12 {letra}' "
                 f"AND oportunidad_id = {OP_1 if letra != 'C' else OP_2})" for letra in "ABC"}


def fila(tipo, dec, motivo=None, visita=None, cancelada=None, op=1, cancelada_id=None):
    """
    INSERT de un resultado de prueba con las columnas NUEVAS, saltándose
    Pydantic a propósito (lo que se prueba es la base de datos).
      visita / cancelada: la letra de una visita de partida (o None).
      cancelada_id:       un id concreto (para uno que no existe).
    """
    # La oportunidad elegida y los parámetros que ya lleva (el email).
    partes, params = [OP_1 if op == 1 else OP_2], [EMAIL_PRUEBA]
    # visita_id: la subconsulta de su letra (dos %s), o NULL.
    if visita is not None:
        partes.append(VISITA[visita])
        params += [EMAIL_PRUEBA]
    else:
        partes.append("NULL")
    # visita_cancelada_id: un id fijo, la subconsulta de su letra, o NULL.
    if cancelada_id is not None:
        partes.append("%s")
        params.append(cancelada_id)
    # Una visita de partida por su letra (su subconsulta lleva un %s).
    elif cancelada is not None:
        partes.append(VISITA[cancelada])
        params += [EMAIL_PRUEBA]
    # Ninguna: NULL.
    else:
        partes.append("NULL")
    # El INSERT completo; RETURNING enseña lo guardado si entra.
    sql = (f"INSERT INTO {TABLA} (oportunidad_id, visita_id, visita_cancelada_id, tipo_llamada, decision, motivo, informe) "
           f"VALUES ({partes[0]}, {partes[1]}, {partes[2]}, %s, %s, %s, 'Informe de prueba') "
           f"RETURNING tipo_llamada, decision;")
    # Los parámetros, en el orden de los %s del texto.
    return sql, tuple(params) + (tipo, dec, motivo)


def fila_como_main(dec, motivo=None, visita=None):
    """INSERT con las columnas de HOY, exactamente como lo escribe main."""
    # La visita (subconsulta, un %s más) o NULL.
    v_sql, v_par = (VISITA[visita], (EMAIL_PRUEBA,)) if visita else ("NULL", ())
    # El INSERT de main, sin tipo_llamada ni visita_cancelada_id.
    sql = (f"INSERT INTO {TABLA} (oportunidad_id, visita_id, decision, motivo, informe) "
           f"VALUES ({OP_1}, {v_sql}, %s, %s, 'Informe de prueba') RETURNING decision;")
    # Parámetros en el orden de los %s.
    return sql, (EMAIL_PRUEBA,) + v_par + (dec, motivo)


# Una sola conexión y su cursor para todo el script.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()

# try / finally: el rollback del final se ejecuta aunque algo reviente.
try:
    # ==================================================================
    print("=" * 78)
    print("A) ESTRUCTURA")
    print("=" * 78)
    # SQL: las columnas de la tabla, con tipo, NULL y valor por defecto.
    cur.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position;",
        (TABLA,),
    )
    # nombre -> (tipo, NULL, defecto).
    columnas = {f[0]: f[1:] for f in cur.fetchall()}
    # Se enseñan una por una.
    for nombre, datos in columnas.items():
        print(f"    {nombre:20} {datos}")
    # Las dos nuevas, con su tipo; .get da tres None si falta (FALLO sin error).
    comprobar("columna tipo_llamada: VARCHAR, NOT NULL, DEFAULT 'gate'",
              columnas.get("tipo_llamada", (None,) * 3) == ("character varying", "NO", "'gate'::character varying"))
    comprobar("columna visita_cancelada_id: INTEGER que admite NULL",
              columnas.get("visita_cancelada_id", (None,) * 3)[:2] == ("integer", "YES"))
    # Las 7 de paso10 más las 2 nuevas: 9.
    comprobar("9 columnas (las 7 de paso10 + 2)", len(columnas) == 9, f"({len(columnas)})")

    # SQL: nombre, tipo y definición de cada restricción.
    cur.execute("SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass;",
                (TABLA,))
    # nombre -> (tipo, definición); contype 'c' check, 'f' clave foránea, 'u' unique.
    restr = {f[0]: (f[1], f[2]) for f in cur.fetchall()}
    # Se enseñan en orden.
    for nombre, (tipo, definicion) in sorted(restr.items()):
        print(f"    {nombre} [{tipo}]: {definicion}")

    def definicion(nombre):
        """La definición de una restricción, o "" si no existe."""
        return restr.get(nombre, ("", ""))[1]

    # La clave foránea nueva, doble y hacia visitas.
    comprobar("clave foránea DOBLE (visita_cancelada_id, oportunidad_id) -> visitas(id, oportunidad_id)",
              "(visita_cancelada_id, oportunidad_id)" in definicion(FK_CANCELADA)
              and "visitas(id, oportunidad_id)" in definicion(FK_CANCELADA))
    # 2 de paso10 + 1: exactamente 3 claves foráneas.
    comprobar("exactamente 3 claves foráneas", sum(1 for t, _ in restr.values() if t == "f") == 3,
              f"({sorted(n for n, (t, _) in restr.items() if t == 'f')})")
    # Los tres tipos, en el CHECK nuevo.
    comprobar("CHECK tipo_llamada con los 3 tipos",
              all(f"'{t}'" in definicion(CHECK_TIPO) for t in ("gate", "seguimiento", "confirmacion_visita")))
    # Los cinco resultados en el CHECK de decision.
    comprobar("CHECK decision con los 5 resultados",
              all(f"'{r}'" in definicion(CHECK_DECISION)
                  for r in ("visita_acordada", "descartar", "confirmar_hora", "otra_hora", "cliente_cancela")))
    # 'sin_decision' entre los motivos, sin perder los de antes.
    comprobar("CHECK motivo con 'sin_decision' y los 5 de antes",
              all(f"'{m}'" in definicion(CHECK_MOTIVO)
                  for m in ("precio", "plazo", "no_contesta", "proyecto_no_viable", "otro", "sin_decision")))
    # La coherencia nueva nombra cliente_cancela y la columna nueva.
    comprobar("CHECK coherencia ampliado (cliente_cancela y visita_cancelada_id)",
              "cliente_cancela" in definicion(CHECK_COHERENCIA) and "visita_cancelada_id" in definicion(CHECK_COHERENCIA))
    # El de tipo y resultado existe y relaciona las dos columnas.
    comprobar("CHECK tipo_resultado relaciona tipo_llamada y decision",
              "tipo_llamada" in definicion(CHECK_TIPO_RESULTADO) and "decision" in definicion(CHECK_TIPO_RESULTADO))
    # Lo que NO cambia: UNIQUE, claves de paso10 e informe.
    comprobar("UNIQUE (oportunidad_id) sigue", "UNIQUE (oportunidad_id)" in definicion(RESTRICCION_UNIQUE))
    comprobar("clave doble de visita_id y la de oportunidad siguen",
              "(visita_id, oportunidad_id)" in definicion(FK_VISITA_DOBLE) and "oportunidades(id)" in definicion(FK_OPORTUNIDAD))
    comprobar("CHECK del informe sigue (btrim y longitud)",
              "btrim(informe)" in definicion(CHECK_INFORME) and "char_length(informe)" in definicion(CHECK_INFORME))

    # SQL: el comentario de la tabla.
    cur.execute("SELECT obj_description(%s::regclass, 'pg_class');", (TABLA,))
    comentario = cur.fetchone()[0] or ""
    comprobar("COMMENT ON TABLE (resultados de las llamadas, nombre histórico)",
              comentario.startswith("Resultados de las llamadas de administración") and "histórico" in comentario,
              f"({comentario!r})")
    # SQL: el interruptor de RLS de la tabla.
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (TABLA,))
    comprobar("RLS sigue activado", cur.fetchone()[0] is True)

    # Las filas reales: todas del Gate y sin visita cancelada. Se mira en un
    # SAVEPOINT porque, antes de migrar, las columnas no existen (error).
    # SQL: cuántas filas hay y cuántas no son del Gate o tienen cancelada.
    error, cuentas = ejecutar_en_savepoint(cur, [(
        f"SELECT count(*), count(*) FILTER (WHERE tipo_llamada <> 'gate'), "
        f"count(*) FILTER (WHERE visita_cancelada_id IS NOT NULL) FROM {TABLA};", None)])
    print(f"    filas reales (total, no-gate, con cancelada): {cuentas[0] if cuentas else error.pgerror.splitlines()[0]}")
    comprobar("todas las filas reales con tipo_llamada 'gate' y sin visita cancelada",
              error is None and cuentas[0][1] == 0 and cuentas[0][2] == 0)

    # ==================================================================
    print("\n" + "=" * 78)
    print("B) COMPORTAMIENTO (todo se deshace al final)")
    print("=" * 78)
    # Datos de partida, FUERA de los savepoints para que todas las pruebas
    # los vean: un cliente, un lead, dos oportunidades y tres visitas (A
    # confirmada y B cancelada en la 1; C confirmada en la 2).
    # SQL: el cliente de prueba, con el email marca.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Prueba paso12", EMAIL_PRUEBA, "600000000"))
    cliente_id = cur.fetchone()[0]
    # SQL: su lead (m2 = 10, dentro del CHECK de leads).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, %s, %s) RETURNING id;",
                (cliente_id, "check_paso12", Json({"m2": 10})))
    lead_id = cur.fetchone()[0]
    # SQL: dos oportunidades del mismo lead (bano y cocina).
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano'), (%s, 'cocina');", (lead_id, lead_id))
    # SQL: las tres visitas, cada una con un texto marca (una activa por
    # oportunidad: B está cancelada, así que el índice parcial lo admite).
    cur.execute(f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) VALUES "
                f"({OP_1}, '2030-01-07 08:30+01', 'confirmada', 'prueba paso12 A'), "
                f"({OP_1}, '2030-01-07 10:30+01', 'cancelada', 'prueba paso12 B'), "
                f"({OP_2}, '2030-01-08 08:30+01', 'confirmada', 'prueba paso12 C');",
                (EMAIL_PRUEBA, EMAIL_PRUEBA, EMAIL_PRUEBA))
    print(f"  datos de prueba: cliente {cliente_id}, lead {lead_id}, dos oportunidades y tres visitas")

    # Prueba en negativo: la restricción pedida, borrada DENTRO de la
    # transacción (el rollback final la devuelve).
    if ARGS.quitar:
        # SQL: borra la restricción pedida (solo dentro de esta transacción).
        cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT {ARGS.quitar};")
        print(f"  PRUEBA EN NEGATIVO: {ARGS.quitar} borrada DENTRO de la transacción")

    # Encabezado del grupo: el INSERT que hace main HOY (sin columnas nuevas).
    print("\n  -- main sigue funcionando (expand): su INSERT de hoy --")
    # Tiene que entrar, y la fila queda con el DEFAULT de paso12.
    filas = debe_aceptar(cur, "visita_acordada como la escribe main -> aceptada",
                         [fila_como_main("visita_acordada", visita="A"),
                          (f"SELECT tipo_llamada, visita_cancelada_id FROM {TABLA} WHERE oportunidad_id = {OP_1};",
                           (EMAIL_PRUEBA,))])
    comprobar("y queda con tipo_llamada 'gate' (DEFAULT) y sin visita cancelada", filas == [("gate", None)], f"({filas})")
    # También el descarte de main.
    debe_aceptar(cur, "descartar como lo escribe main -> aceptado", [fila_como_main("descartar", motivo="precio")])

    # Encabezado: un caso aceptado por cada resultado de cada tipo.
    print("\n  -- Los resultados válidos de cada tipo --")
    # Gate: visita acordada y descarte con el motivo nuevo.
    debe_aceptar(cur, "gate visita_acordada con su visita", [fila("gate", "visita_acordada", visita="A")])
    debe_aceptar(cur, "gate descartar con 'sin_decision' (D29.3)", [fila("gate", "descartar", motivo="sin_decision")])
    # Seguimiento: los mismos resultados que el Gate (D25.10).
    debe_aceptar(cur, "seguimiento visita_acordada", [fila("seguimiento", "visita_acordada", visita="A")])
    debe_aceptar(cur, "seguimiento descartar con 'no_contesta'", [fila("seguimiento", "descartar", motivo="no_contesta")])
    debe_aceptar(cur, "seguimiento descartar con 'sin_decision'", [fila("seguimiento", "descartar", motivo="sin_decision")])
    # Confirmación de visita: los tres resultados nuevos.
    debe_aceptar(cur, "confirmacion_visita confirmar_hora", [fila("confirmacion_visita", "confirmar_hora", visita="A")])
    debe_aceptar(cur, "confirmacion_visita otra_hora (confirmada A, cancelada B)",
                 [fila("confirmacion_visita", "otra_hora", visita="A", cancelada="B")])
    debe_aceptar(cur, "confirmacion_visita cliente_cancela (motivo, cancelada B, sin visita)",
                 [fila("confirmacion_visita", "cliente_cancela", motivo="precio", cancelada="B")])

    # Encabezado: valores fuera de las listas cerradas.
    print("\n  -- Listas cerradas --")
    # Un tipo y un resultado que no existen.
    debe_fallar(cur, "tipo_llamada 'recordatorio' -> rechazado", [fila("recordatorio", "descartar", motivo="precio")],
                psycopg2.errors.CheckViolation, CHECK_TIPO)
    debe_fallar(cur, "decision 'aplazar' -> rechazada (D29.1: no existe en N0)", [fila("gate", "aplazar")],
                psycopg2.errors.CheckViolation, CHECK_DECISION)
    # Un motivo que no existe, y un tipo vacío.
    debe_fallar(cur, "motivo 'cambio_de_planes' -> rechazado", [fila("gate", "descartar", motivo="cambio_de_planes")],
                psycopg2.errors.CheckViolation, CHECK_MOTIVO)
    debe_fallar(cur, "tipo_llamada NULL -> rechazado", [fila(None, "descartar", motivo="precio")],
                psycopg2.errors.NotNullViolation, None)

    # Encabezado: resultados que no son de ese tipo.
    print("\n  -- Tipo y resultado --")
    # Un resultado de confirmación en el Gate y otro en un seguimiento.
    debe_fallar(cur, "gate confirmar_hora -> rechazado", [fila("gate", "confirmar_hora", visita="A")],
                psycopg2.errors.CheckViolation, CHECK_TIPO_RESULTADO)
    debe_fallar(cur, "seguimiento cliente_cancela -> rechazado",
                [fila("seguimiento", "cliente_cancela", motivo="precio", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_TIPO_RESULTADO)
    # Y uno del Gate en una confirmación.
    debe_fallar(cur, "confirmacion_visita visita_acordada -> rechazado",
                [fila("confirmacion_visita", "visita_acordada", visita="A")],
                psycopg2.errors.CheckViolation, CHECK_TIPO_RESULTADO)

    # Encabezado: lo que acompaña a cada resultado. T: el tipo de la confirmación.
    print("\n  -- Coherencia (qué acompaña a cada resultado) --")
    T = "confirmacion_visita"
    # cliente_cancela: motivo obligatorio, y ni 'sin_decision' ni 'no_contesta'.
    debe_fallar(cur, "cliente_cancela sin motivo", [fila(T, "cliente_cancela", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "cliente_cancela con 'sin_decision' (D29.3)", [fila(T, "cliente_cancela", motivo="sin_decision", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "cliente_cancela con 'no_contesta' (C6)", [fila(T, "cliente_cancela", motivo="no_contesta", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    # cliente_cancela: con su visita cancelada y sin visita confirmada.
    debe_fallar(cur, "cliente_cancela sin visita cancelada", [fila(T, "cliente_cancela", motivo="precio")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "cliente_cancela con visita confirmada", [fila(T, "cliente_cancela", motivo="precio", visita="A", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    # otra_hora: las dos visitas, y distintas.
    debe_fallar(cur, "otra_hora sin visita cancelada", [fila(T, "otra_hora", visita="A")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "otra_hora sin visita confirmada", [fila(T, "otra_hora", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "otra_hora con la MISMA visita confirmada y cancelada", [fila(T, "otra_hora", visita="A", cancelada="A")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    # Los demás resultados no llevan visita cancelada.
    debe_fallar(cur, "confirmar_hora con visita cancelada", [fila(T, "confirmar_hora", visita="A", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "visita_acordada con visita cancelada", [fila("seguimiento", "visita_acordada", visita="A", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)
    debe_fallar(cur, "descartar con visita cancelada", [fila("gate", "descartar", motivo="precio", cancelada="B")],
                psycopg2.errors.CheckViolation, CHECK_COHERENCIA)

    # Encabezado: claves foráneas y UNIQUE.
    print("\n  -- Claves foráneas y UNIQUE --")
    # La visita cancelada tiene que ser de la MISMA oportunidad, y existir.
    debe_fallar(cur, "visita cancelada de OTRA oportunidad -> rechazada por la clave doble nueva",
                [fila(T, "cliente_cancela", motivo="precio", cancelada="C")],
                psycopg2.errors.ForeignKeyViolation, FK_CANCELADA)
    debe_fallar(cur, "visita cancelada que no existe -> rechazada",
                [fila(T, "cliente_cancela", motivo="precio", cancelada_id=999999999)],
                psycopg2.errors.ForeignKeyViolation, FK_CANCELADA)
    # Y sigue habiendo un solo resultado por oportunidad.
    debe_fallar(cur, "dos resultados para la misma oportunidad -> rechazado (UNIQUE sigue, P6)",
                [fila_como_main("descartar", motivo="precio"), fila_como_main("descartar", motivo="plazo")],
                psycopg2.errors.UniqueViolation, RESTRICCION_UNIQUE)
finally:
    # Deshace TODO: datos de partida, restricción borrada y cualquier resto.
    cn.rollback()

# Comprobaciones finales, ya fuera de la transacción de prueba.
# SQL: clientes que quedan con el email marca (debe ser 0).
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
restos = cur.fetchone()[0]
# SQL: las restricciones que tiene AHORA la tabla real.
cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass;", (TABLA,))
presentes = {f[0] for f in cur.fetchall()}
# commit cierra la transacción de lectura; después se cierra la conexión.
cn.commit()
cn.close()
print(f"\n  Tras el rollback: clientes de prueba = {restos}")
comprobar("no queda ningún dato de prueba", restos == 0)
# "<=": ¿están todas las quitables entre las presentes?
comprobar("la tabla real conserva sus restricciones quitables", set(RESTRICCIONES_QUITABLES) <= presentes,
          f"(faltan: {sorted(set(RESTRICCIONES_QUITABLES) - presentes)})")

# Resumen: correctas sobre el total, y si era una prueba en negativo.
total = ok + len(fallos)
# El resumen, entre dos líneas de "=".
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas" + (f"  (prueba en negativo: sin {ARGS.quitar})" if ARGS.quitar else ""))
print("=" * 78)
# Con fallos, se listan y el código de salida es 1 (lo mira la suite).
if fallos:
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
