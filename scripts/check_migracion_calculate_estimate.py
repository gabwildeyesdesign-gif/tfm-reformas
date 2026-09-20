"""
Verificación de la migración del Bloque 1 de POST /calculate-estimate.

Qué comprueba: que la base de datos real tiene las dos restricciones que
necesita el endpoint, y que se COMPORTAN como deben (no solo que existen):

  A1. oportunidades.estado admite el valor nuevo 'pendiente_aprobacion'.
  A2. oportunidades.estado sigue rechazando un valor inventado.
  A3. Los seis estados que ya existían siguen admitiéndose (la migración
      rehace el CHECK entero, así que hay que comprobar que no se perdió
      ninguno por el camino).
  A4. presupuestos rechaza un segundo presupuesto para la misma
      oportunidad (UNIQUE sobre oportunidad_id).
  A5. INSERT ... ON CONFLICT (oportunidad_id) DO NOTHING RETURNING sobre
      una oportunidad que ya tiene presupuesto devuelve CERO filas y no
      da error. Es la sentencia exacta que usará estimate_service.py.

Qué NO hace: no aplica la migración. La migración vive aparte, en
scripts/migraciones_ejecutadas/paso3_calculate_estimate.py. En esto se
separa de su precedente, check_tipo_reforma_constraint.py, que hacía las
dos cosas en el mismo archivo. Separarlas permite ejecutar esta
verificación ANTES de migrar (debe fallar en A1, A4 y A5 y acertar en A2
y A3) y DESPUÉS (debe acertar en todo). Ver las dos salidas juntas es la
prueba de que es la migración la que cambia el comportamiento, y no algo
que ya estaba así.

No deja rastro: todo ocurre dentro de UNA transacción que se deshace al
final con rollback(). Las filas de prueba (cliente, lead, oportunidad y
presupuesto) nunca llegan a existir de forma permanente.

--------------------------------------------------------------------
POR QUÉ CADA ASERCIÓN VA DENTRO DE SU PROPIO SAVEPOINT
--------------------------------------------------------------------
En Postgres, cuando una sentencia falla dentro de una transacción, la
transacción entera queda ABORTADA. A partir de ahí, Postgres rechaza
cualquier otra sentencia con "current transaction is aborted, commands
ignored until end of transaction block" (en psycopg2, la excepción
InFailedSqlTransaction), hasta que se haga ROLLBACK. La sección 0 de este
script lo demuestra ejecutándolo, no solo diciéndolo.

Este script provoca errores A PROPÓSITO (A2 y A4 esperan un rechazo).
Sin ninguna protección, A2 abortaría la transacción y A3, A4 y A5 ni
siquiera llegarían a ejecutarse: fallarían con InFailedSqlTransaction y
el script daría por comprobado algo que nunca se comprobó.

Hay tres formas de evitarlo. Se eligió la tercera:
  - rollback() de la transacción entera tras cada error (lo que hace
    check_tipo_reforma_constraint.py). Deshace también las filas de
    preparación, así que las aserciones siguientes trabajan sobre filas
    que ya no existen.
  - Una conexión o transacción nueva por aserción. Obliga a crear y
    borrar las filas de preparación cada vez, y a confirmar (commit)
    datos de prueba en la base de datos real.
  - SAVEPOINT: un punto de guardado DENTRO de la transacción. Si la
    sentencia falla, "ROLLBACK TO SAVEPOINT" deshace solo lo ocurrido
    desde ese punto y la transacción vuelve a estar sana, con las filas
    de preparación intactas. Es el mecanismo que Postgres ofrece
    exactamente para este caso.
"""

import os
import sys
from pathlib import Path

# psycopg2 se usa aquí directamente con connect(), no a través del pool
# de app/db/connection.py. Motivo concreto: este script NO debe confirmar
# nada nunca. get_transactional_connection() hace commit() al salir del
# with si no hubo excepción, justo lo contrario de lo que se necesita. Y
# get_db_connection() (la de solo lectura) sí haría rollback, pero su
# contrato es "solo lectura", y aquí se escribe (dentro de una
# transacción que luego se deshace): usarla sería mentir sobre lo que
# hace el código. Con una conexión propia, el rollback final es
# explícito y visible en este archivo. Es el mismo criterio de los demás
# check_*.py.
#
# Las clases de error (psycopg2.errors.CheckViolation, UniqueViolation,
# InFailedSqlTransaction...) no necesitan un import aparte: el módulo
# psycopg2.errors se carga al hacer "import psycopg2". Cada una
# corresponde a un código de error oficial de Postgres, lo que permite
# capturar exactamente el error esperado y no cualquier fallo.
import psycopg2
from dotenv import load_dotenv

# La consola de Windows no escribe en UTF-8 por defecto. Sin esto, las
# tildes de los mensajes saldrían rotas.
sys.stdout.reconfigure(encoding="utf-8")

# Raíz del repositorio calculada desde la ubicación de este archivo
# (scripts/ -> raíz, un nivel: parents[1]), igual que el resto de los
# check_*.py, para que funcione en cualquier máquina.
RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")

# Marcas de las filas de prueba. El email es de example.com porque
# EmailStr rechaza los dominios reservados .test e .invalid (limitación
# ya documentada). Aquí no pasa por EmailStr, pero se usa el mismo
# criterio en todos los scripts para que los datos de prueba se
# reconozcan a simple vista.
EMAIL_PRUEBA = "check-migracion-paso3@example.com"
CANAL_PRUEBA = "check_migracion_paso3"

# Los seis estados que existían antes de la migración, copiados del
# CHECK real de docs/schema_actual.sql (no de la documentación).
ESTADOS_ANTERIORES = (
    "nueva",
    "cualificada",
    "visita_agendada",
    "presupuesto_enviado",
    "ganada",
    "perdida",
)

# Nombres de las restricciones tal como los crea paso3_calculate_estimate.py.
NOMBRE_CHECK_ESTADO = "oportunidades_estado_check"
NOMBRE_UNIQUE_PRESUPUESTO = "presupuestos_oportunidad_id_key"

# Nombre del punto de guardado. Es fijo y se reutiliza en todas las
# aserciones: tras RELEASE SAVEPOINT el nombre queda libre y se puede
# volver a usar. Tiene que ir escrito dentro del texto SQL (f-string) y
# no como parámetro %s. Los %s de psycopg2 son para VALORES (se envían
# entre comillas, como 'asercion'), y un nombre de savepoint es un
# IDENTIFICADOR, como el nombre de una tabla, que no admite comillas
# simples. Es seguro porque el valor es una constante de este archivo y
# no viene de fuera.
NOMBRE_SAVEPOINT = "asercion"


def ejecutar_en_savepoint(cur, sql, params=None):
    """
    Ejecuta UNA sentencia SQL aislada en su propio punto de guardado y
    devuelve lo que pasó, sin dejar nunca la transacción abortada.

    Devuelve una tupla (error, filas):
      - Si la sentencia falla:  (la excepción de psycopg2, None)
      - Si funciona:            (None, lista de filas devueltas)
        Si la sentencia no devuelve filas (un UPDATE sin RETURNING),
        filas es None.

    Deshace SIEMPRE el efecto de la sentencia, también cuando funciona.
    Así cada aserción empieza desde el mismo estado: la oportunidad de
    prueba en 'nueva' y con un único presupuesto. Si A1 dejara el estado
    en 'pendiente_aprobacion', A3 ya no partiría de 'nueva', y el
    resultado de una aserción dependería del orden en que se ejecutan.
    """
    # SAVEPOINT marca un punto dentro de la transacción en curso. No
    # confirma nada ni abre una transacción nueva. Solo apunta "hasta
    # aquí todo estaba bien", para poder volver a este punto más tarde
    # sin perder lo anterior (las filas de preparación).
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        cur.execute(sql, params)
        # cur.description es None cuando la sentencia no devuelve
        # columnas (UPDATE o INSERT sin RETURNING). Llamar a fetchall()
        # en ese caso lanzaría ProgrammingError, así que se comprueba
        # antes.
        filas = cur.fetchall() if cur.description is not None else None
        return None, filas
    except psycopg2.Error as error:
        # psycopg2.Error es la clase base de todos los errores de la
        # base de datos. Se captura aquí para DEVOLVERLO, no para
        # ocultarlo: quien llama decide si ese error era el esperado
        # (A2, A4) o un fallo de verdad.
        return error, None
    finally:
        # Un bloque finally se ejecuta SIEMPRE al salir del try, tanto
        # si hubo excepción como si no, e incluso cuando el try o el
        # except ya han hecho return: Python ejecuta el finally y
        # después devuelve el valor.
        #
        # ROLLBACK TO SAVEPOINT deshace todo lo ocurrido desde el
        # SAVEPOINT de arriba y, si la sentencia había fallado, SACA a
        # la transacción del estado abortado. Es la línea que permite
        # que la siguiente aserción se ejecute.
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        # RELEASE SAVEPOINT borra el punto de guardado (ROLLBACK TO lo
        # deja vivo). No confirma ni deshace nada más. Se hace para no
        # acumular savepoints abiertos, uno por aserción, que Postgres
        # tendría que mantener hasta el final de la transacción.
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


# Aquí se van guardando los resultados de cada comprobación, para
# imprimir un resumen al final y decidir el código de salida del script.
resultados = []


def registrar(nombre, correcto, detalle):
    """Guarda e imprime el resultado de una comprobación."""
    resultados.append((nombre, correcto))
    marca = "OK   " if correcto else "FALLO"
    print(f"  [{marca}] {nombre}")
    print(f"          {detalle}")


def nombre_restriccion(error):
    """
    Devuelve el nombre de la restricción que provocó un error de
    Postgres, o None si no lo trae.

    error.diag es un objeto de psycopg2 con los detalles que Postgres
    adjunta a cada error. constraint_name permite comprobar que el
    rechazo lo causó la restricción que se está verificando y no otra
    (por ejemplo, una clave foránea). Es más exigente que mirar solo el
    tipo de excepción.
    """
    return getattr(error.diag, "constraint_name", None) if error else None


cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

# try/finally envuelve todo el trabajo con la base de datos para
# garantizar que el rollback final ocurre aunque el script falle a mitad
# por un error inesperado. (Aun sin él, cerrar la conexión sin commit
# hace que Postgres deshaga la transacción. Se escribe explícito para no
# depender de ese efecto implícito.)
try:
    # ------------------------------------------------------------------
    print("=" * 80)
    print("0. DEMOSTRACIÓN: una sentencia fallida aborta la transacción entera")
    print("=" * 80)
    # No toca ninguna tabla: provoca un error con una división por cero,
    # algo que no depende de si la migración está aplicada o no.
    try:
        cur.execute("SELECT 1 / 0;")
    except psycopg2.errors.DivisionByZero:
        print("  SELECT 1 / 0  -> DivisionByZero (esperado)")
    try:
        # Una sentencia perfectamente válida, ejecutada justo después.
        cur.execute("SELECT 1;")
        registrar("Demostración", False, "SELECT 1 funcionó: la premisa no se cumple")
    except psycopg2.errors.InFailedSqlTransaction:
        registrar(
            "Demostración",
            True,
            "SELECT 1 rechazado con InFailedSqlTransaction: sin SAVEPOINT, "
            "todo lo posterior a un error se pierde",
        )
    # Se deshace la transacción rota de la demostración. Aquí sí es
    # correcto un rollback() completo: todavía no hay ninguna fila de
    # preparación que perder.
    cn.rollback()

    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("1. LAS RESTRICCIONES EXISTEN, con la definición esperada (lectura)")
    print("=" * 80)
    # Solo lecturas de catálogo: un SELECT sobre pg_constraint no puede
    # violar ninguna restricción, así que aquí no hace falta savepoint.
    # pg_get_constraintdef() devuelve la definición tal como la guarda
    # Postgres.
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'oportunidades'::regclass AND conname = %s;
        """,
        (NOMBRE_CHECK_ESTADO,),
    )
    fila = cur.fetchone()
    definicion = fila[0] if fila else "(no existe)"
    registrar(
        f"{NOMBRE_CHECK_ESTADO} incluye 'pendiente_aprobacion'",
        "'pendiente_aprobacion'" in definicion,
        definicion,
    )

    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'presupuestos'::regclass AND conname = %s;
        """,
        (NOMBRE_UNIQUE_PRESUPUESTO,),
    )
    fila = cur.fetchone()
    registrar(
        f"{NOMBRE_UNIQUE_PRESUPUESTO} existe",
        fila is not None and fila[0] == "UNIQUE (oportunidad_id)",
        fila[0] if fila else "(no existe)",
    )

    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("2. FILAS DE PREPARACIÓN (dentro de la transacción; se deshacen al final)")
    print("=" * 80)
    # Cadena mínima que exigen las claves foráneas:
    # cliente -> lead -> oportunidad -> presupuesto.
    # Van SIN savepoint a propósito: si una falla, no tiene sentido
    # seguir, y la excepción debe parar el script.
    cur.execute(
        "INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
        ("Fila descartable paso3", EMAIL_PRUEBA, "000000000"),
    )
    cliente_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO leads (cliente_id, canal) VALUES (%s, %s) RETURNING id;",
        (cliente_id, CANAL_PRUEBA),
    )
    lead_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO oportunidades (lead_id, tipo_reforma, estado)
        VALUES (%s, 'bano', 'nueva') RETURNING id;
        """,
        (lead_id,),
    )
    oportunidad_id = cur.fetchone()[0]
    # El presupuesto "ya existente" contra el que chocan A4 y A5.
    cur.execute(
        """
        INSERT INTO presupuestos (oportunidad_id, importe_min, importe_max,
                                  requiere_aprobacion)
        VALUES (%s, 6900.00, 7935.00, false) RETURNING id;
        """,
        (oportunidad_id,),
    )
    presupuesto_id = cur.fetchone()[0]
    print(
        f"  cliente={cliente_id} lead={lead_id} "
        f"oportunidad={oportunidad_id} presupuesto={presupuesto_id}"
    )

    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("3. ASERCIONES (cada una en su propio SAVEPOINT)")
    print("=" * 80)

    # A1. El valor nuevo se acepta.
    error, filas = ejecutar_en_savepoint(
        cur,
        "UPDATE oportunidades SET estado = 'pendiente_aprobacion' WHERE id = %s RETURNING estado;",
        (oportunidad_id,),
    )
    registrar(
        "A1 estado 'pendiente_aprobacion' aceptado",
        error is None and filas == [("pendiente_aprobacion",)],
        f"error={type(error).__name__ if error else None} filas={filas}",
    )

    # A2. Un valor inventado se rechaza, y lo rechaza ESTE CHECK.
    error, _ = ejecutar_en_savepoint(
        cur,
        "UPDATE oportunidades SET estado = 'inventado' WHERE id = %s;",
        (oportunidad_id,),
    )
    registrar(
        "A2 estado 'inventado' rechazado por el CHECK",
        isinstance(error, psycopg2.errors.CheckViolation)
        and nombre_restriccion(error) == NOMBRE_CHECK_ESTADO,
        f"error={type(error).__name__ if error else None} "
        f"restricción={nombre_restriccion(error)}",
    )

    # A3. Los seis estados anteriores siguen aceptados. Uno por
    # savepoint: si uno fallara, los demás se comprobarían igualmente.
    for estado in ESTADOS_ANTERIORES:
        error, filas = ejecutar_en_savepoint(
            cur,
            "UPDATE oportunidades SET estado = %s WHERE id = %s RETURNING estado;",
            (estado, oportunidad_id),
        )
        registrar(
            f"A3 estado '{estado}' sigue aceptado",
            error is None and filas == [(estado,)],
            f"error={type(error).__name__ if error else None} filas={filas}",
        )

    # A4. Un segundo presupuesto para la misma oportunidad se rechaza,
    # y lo rechaza ESTE UNIQUE.
    error, _ = ejecutar_en_savepoint(
        cur,
        """
        INSERT INTO presupuestos (oportunidad_id, importe_min, importe_max,
                                  requiere_aprobacion)
        VALUES (%s, 1.00, 2.00, false);
        """,
        (oportunidad_id,),
    )
    registrar(
        "A4 segundo presupuesto rechazado por el UNIQUE",
        isinstance(error, psycopg2.errors.UniqueViolation)
        and nombre_restriccion(error) == NOMBRE_UNIQUE_PRESUPUESTO,
        f"error={type(error).__name__ if error else None} "
        f"restricción={nombre_restriccion(error)}",
    )

    # A5. La sentencia exacta del servicio: con conflicto, cero filas y
    # ningún error. (Antes de la migración, Postgres la rechaza porque
    # ON CONFLICT (oportunidad_id) exige un UNIQUE sobre esa columna.)
    error, filas = ejecutar_en_savepoint(
        cur,
        """
        INSERT INTO presupuestos (oportunidad_id, importe_min, importe_max,
                                  requiere_aprobacion)
        VALUES (%s, 1.00, 2.00, false)
        ON CONFLICT (oportunidad_id) DO NOTHING
        RETURNING id;
        """,
        (oportunidad_id,),
    )
    registrar(
        "A5 ON CONFLICT DO NOTHING RETURNING -> 0 filas, sin error",
        error is None and filas == [],
        f"error={type(error).__name__ if error else None} filas={filas}",
    )

finally:
    # Deshace TODA la transacción: filas de preparación incluidas. Es lo
    # que hace que este script no deje rastro en la base de datos real.
    cn.rollback()

# ----------------------------------------------------------------------
print("\n" + "=" * 80)
print("4. NO QUEDA RASTRO (comprobado desde una transacción nueva)")
print("=" * 80)
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL_PRUEBA,))
restantes_clientes = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM leads WHERE canal = %s;", (CANAL_PRUEBA,))
restantes_leads = cur.fetchone()[0]
# No hace falta contar oportunidades ni presupuestos de prueba: sus
# claves foráneas impiden que existan sin su lead y su oportunidad, y el
# lead ya se ha contado.
cn.rollback()
registrar(
    "Sin filas de prueba tras el rollback",
    restantes_clientes == 0 and restantes_leads == 0,
    f"clientes={restantes_clientes} leads={restantes_leads}",
)

cur.close()
cn.close()

# ----------------------------------------------------------------------
fallos = [nombre for nombre, correcto in resultados if not correcto]
print("\n" + "=" * 80)
print(f"RESULTADO: {len(resultados) - len(fallos)}/{len(resultados)} correctas")
for nombre in fallos:
    print(f"  FALLO: {nombre}")
print("=" * 80)
# Código de salida 0 = todo correcto, 1 = algún fallo. Permite usar el
# script en una cadena de comandos o, más adelante, en CI.
sys.exit(0 if not fallos else 1)
