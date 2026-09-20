"""
Verificación de la migración paso7 (D13 + D14).

Comprueba que el esquema quedó como debía y, donde tiene sentido, que se
COMPORTA como debe, no solo que existe:

  S1. oportunidades.fecha_ultimo_contacto existe, es TIMESTAMPTZ y admite
      NULL (un lead recién creado no ha tenido contacto todavía).
  S2. Todas las filas la tienen a NULL: en este bloque nadie la escribe
      todavía (el barrido de D13 es la sesión siguiente).
  S3. oportunidades_estado_check admite 'seguimiento_pendiente' y sigue
      admitiendo los siete valores anteriores, sin perder ninguno.
  S4. Un estado inventado se sigue rechazando.
  S5. reglas_negocio.iva_estandar_pct existe y vale 21.
  S6. presupuestos tiene importe_min_con_iva / importe_max_con_iva y ya
      NO tiene las columnas con el nombre antiguo (el renombrado no dejó
      duplicados).
  S7. presupuestos.iva_pct_aplicado es NUMERIC(4,2), NOT NULL y con
      DEFAULT 21, de modo que ningún presupuesto puede quedar sin decir
      con qué IVA se calculó.

Todo ocurre dentro de UNA transacción que termina en rollback, y cada
aserción que puede fallar va en su propio SAVEPOINT: en Postgres una
sentencia fallida aborta la transacción entera, así que sin savepoints el
primer rechazo esperado (S4) impediría ejecutar lo que viene después.
Mismo patrón que check_migracion_calculate_estimate.py,
check_migracion_umbrales_gate.py y check_migracion_m2_leads.py.
"""

import os
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")

NOMBRE_SAVEPOINT = "asercion"
ESTADO_NUEVO = "seguimiento_pendiente"
# Los siete que ya existían antes de paso7. Se listan para comprobar que
# el DROP + ADD del CHECK no se dejó ninguno por el camino: rehacer una
# restricción es justo el momento en que se pierde un valor sin que nadie
# lo note hasta que un UPDATE falla en producción.
ESTADOS_ANTERIORES = (
    "nueva",
    "cualificada",
    "pendiente_aprobacion",
    "visita_agendada",
    "presupuesto_enviado",
    "ganada",
    "perdida",
)

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


def ejecutar_en_savepoint(cur, sql, params=None):
    """Ejecuta una sentencia aislada y devuelve (error, filas)."""
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        cur.execute(sql, params)
        filas = cur.fetchall() if cur.description is not None else None
        return None, filas
    except psycopg2.Error as error:
        return error, None
    finally:
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


def columna(tabla, nombre):
    """Devuelve (tipo, admite_null, valor_por_defecto) o None si no existe."""
    cur.execute(
        """
        SELECT data_type, is_nullable, column_default, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s;
        """,
        (tabla, nombre),
    )
    return cur.fetchone()


cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

try:
    print("=" * 78)
    print("S1 y S2 - D13: fecha_ultimo_contacto")
    print("=" * 78)
    col = columna("oportunidades", "fecha_ultimo_contacto")
    print(f"  {col}")
    comprobar("existe y es TIMESTAMPTZ (no TIMESTAMP, para no perder la zona)",
              col is not None and col[0] == "timestamp with time zone",
              f"({col[0] if col else 'no existe'})")
    comprobar("admite NULL (un lead nuevo aún no ha tenido contacto)",
              col is not None and col[1] == "YES")
    cur.execute(
        "SELECT count(*), count(fecha_ultimo_contacto) FROM oportunidades;"
    )
    total, con_valor = cur.fetchone()
    # count(columna) cuenta solo los valores NO nulos, así que si vale 0
    # es que todas están a NULL. Es lo esperado en este bloque: la columna
    # se crea ahora y la escribirá el barrido de D13 más adelante.
    comprobar("ninguna fila tiene valor todavía (nadie la escribe aún)",
              con_valor == 0, f"({con_valor} con valor de {total} filas)")

    print("\n" + "=" * 78)
    print("S3 y S4 - D13: el octavo estado")
    print("=" * 78)
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'oportunidades'::regclass AND conname = 'oportunidades_estado_check';
        """
    )
    definicion = cur.fetchone()[0]
    print(f"  {definicion}")
    comprobar(f"el CHECK admite '{ESTADO_NUEVO}'", f"'{ESTADO_NUEVO}'" in definicion)
    faltan = [e for e in ESTADOS_ANTERIORES if f"'{e}'" not in definicion]
    comprobar("no se perdió ninguno de los siete anteriores", faltan == [],
              f"(faltarían: {faltan})")

    # Prueba de comportamiento, no solo de texto: se crea la cadena mínima
    # de filas (cliente -> lead -> oportunidad) y se intenta el UPDATE.
    cur.execute(
        "INSERT INTO clientes (nombre, email, telefono) VALUES (%s,%s,%s) RETURNING id;",
        ("Prueba paso7", "check-paso7@example.com", "600000000"),
    )
    cliente_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO leads (cliente_id, canal) VALUES (%s,'check_paso7') RETURNING id;",
        (cliente_id,),
    )
    lead_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s,'bano','nueva') RETURNING id;",
        (lead_id,),
    )
    oportunidad_id = cur.fetchone()[0]

    error, filas = ejecutar_en_savepoint(
        cur,
        "UPDATE oportunidades SET estado = %s WHERE id = %s RETURNING estado;",
        (ESTADO_NUEVO, oportunidad_id),
    )
    comprobar(f"un UPDATE a '{ESTADO_NUEVO}' se acepta de verdad",
              error is None and filas == [(ESTADO_NUEVO,)],
              f"({type(error).__name__ if error else filas})")
    error, _ = ejecutar_en_savepoint(
        cur,
        "UPDATE oportunidades SET estado = 'inventado' WHERE id = %s;",
        (oportunidad_id,),
    )
    comprobar("un estado inventado se sigue rechazando",
              isinstance(error, psycopg2.errors.CheckViolation),
              f"({type(error).__name__ if error else 'sin error'})")

    print("\n" + "=" * 78)
    print("S5 - D14: el tipo de IVA como regla editable")
    print("=" * 78)
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = 'iva_estandar_pct';")
    fila = cur.fetchone()
    comprobar("reglas_negocio.iva_estandar_pct = 21",
              fila is not None and fila[0] == Decimal("21.00"),
              f"({fila[0] if fila else 'no existe'})")
    comprobar("la fila explica por qué 21 y no 10",
              fila is not None and "reducido" in (fila[1] or ""),
              f"({(fila[1] or '')[:60]}...)" if fila else "")

    print("\n" + "=" * 78)
    print("S6 y S7 - D14: las columnas de presupuestos")
    print("=" * 78)
    for nombre in ("importe_min_con_iva", "importe_max_con_iva"):
        comprobar(f"existe presupuestos.{nombre}", columna("presupuestos", nombre) is not None)
    # El renombrado no debe dejar las columnas viejas: si quedaran las
    # dos, nadie sabría cuál es la buena, que es justo el problema que se
    # quería evitar al usar RENAME en vez de crear columnas nuevas.
    for nombre in ("importe_min", "importe_max"):
        comprobar(f"YA NO existe presupuestos.{nombre} (fue RENAME, no copia)",
                  columna("presupuestos", nombre) is None)

    col = columna("presupuestos", "iva_pct_aplicado")
    print(f"  iva_pct_aplicado -> {col}")
    comprobar("iva_pct_aplicado es NUMERIC(4,2)",
              col is not None and (col[3], col[4]) == (4, 2),
              f"({col[3] if col else '-'},{col[4] if col else '-'})")
    comprobar("es NOT NULL: ningún presupuesto puede quedar sin decir su IVA",
              col is not None and col[1] == "NO")
    comprobar("tiene DEFAULT 21", col is not None and str(col[2]).startswith("21"),
              f"({col[2] if col else '-'})")

    # Comportamiento: un INSERT que no menciona la columna la rellena sola.
    error, filas = ejecutar_en_savepoint(
        cur,
        """INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva,
                                     requiere_aprobacion)
           VALUES (%s, 100.00, 200.00, false) RETURNING iva_pct_aplicado;""",
        (oportunidad_id,),
    )
    comprobar("un INSERT sin indicar el IVA guarda 21 por defecto",
              error is None and filas == [(Decimal("21.00"),)],
              f"({type(error).__name__ if error else filas})")

finally:
    # Deshace la transacción entera: este script no deja rastro.
    cn.rollback()
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
for f in fallos:
    print(f"  FALLO: {f}")
print("=" * 78)
sys.exit(0 if not fallos else 1)
