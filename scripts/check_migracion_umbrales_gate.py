"""
Verificación de la migración de D9: la tabla umbrales_gate.

Comprueba que la tabla existe, que contiene los cuatro umbrales con los
valores decididos, y que SE COMPORTA como debe (no solo que está).

  U1. Existe, con tipo_reforma como clave primaria y RLS activado.
  U2. Contiene los 4 umbrales con sus valores y su marca 'provisional'.
  U3. Rechaza una categoría inventada (CHECK), incluido 'baño' con eñe.
  U4. Rechaza una categoría repetida (clave primaria).
  U5. Rechaza un umbral de 0 o negativo (CHECK).
  U6. TODA categoría que existe en tarifas_base tiene su umbral: no
      puede haber una reforma presupuestable sin umbral definido.

Igual que scripts/check_migracion_calculate_estimate.py:
  - Todo ocurre dentro de UNA transacción que termina en rollback, así
    que no deja rastro.
  - Cada aserción que puede fallar va en su propio SAVEPOINT. En
    Postgres, una sentencia fallida aborta la transacción entera; sin
    SAVEPOINT, la primera violación esperada (U3) impediría ejecutar
    U4, U5 y U6, que fallarían con InFailedSqlTransaction sin haber
    comprobado nada.

Se ejecuta ANTES de migrar (debe fallar en casi todo) y DESPUÉS (debe
dar 100 %).
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

# Lo que D9 decidió. Se escribe aquí, en el script, para comparar la
# tabla real contra la decisión documentada, no contra sí misma.
ESPERADO = {
    "bano": (Decimal("13000.00"), False),
    "cocina": (Decimal("13000.00"), False),
    "integral_vivienda": (Decimal("10000.00"), False),
    # parcial_acabados nació provisional (paso4) porque 10.000 € era el
    # valor heredado del umbral global, no una decisión. Gabi la cerró el
    # 2026-09-20 con esa misma cifra (paso5), así que ya no hay ningún
    # umbral provisional en el sistema.
    "parcial_acabados": (Decimal("10000.00"), False),
}

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
    """
    Ejecuta una sentencia aislada en su propio punto de guardado y
    devuelve (error, filas), sin dejar nunca la transacción abortada.

    Es la MISMA función que en check_migracion_calculate_estimate.py,
    copiada a propósito en vez de importada: los check_*.py del proyecto
    son scripts independientes que se pueden leer y ejecutar por
    separado, sin una librería común que haya que entender antes.

    SAVEPOINT marca un punto dentro de la transacción; ROLLBACK TO
    SAVEPOINT vuelve a él (y saca a la transacción del estado abortado si
    la sentencia falló); RELEASE SAVEPOINT borra la marca para no
    acumular una por aserción. El finally garantiza que las dos últimas
    se ejecutan pase lo que pase.
    """
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


cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

try:
    print("=" * 78)
    print("U1 - La tabla existe, con su clave primaria y RLS")
    print("=" * 78)
    # to_regclass devuelve NULL en vez de error si la tabla no existe.
    cur.execute("SELECT to_regclass('public.umbrales_gate');")
    existe = cur.fetchone()[0] is not None
    comprobar("la tabla umbrales_gate existe", existe)

    if not existe:
        # Sin tabla, las demás comprobaciones no tienen sentido: se
        # informa y se sale (es lo que pasa ANTES de migrar).
        print("\n  La tabla no existe todavía: el resto de comprobaciones se omite.")
    else:
        # pg_constraint guarda las restricciones; contype 'p' es PRIMARY
        # KEY. Es el mismo catálogo que se consultó en paso3 y en su
        # verificación.
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'umbrales_gate'::regclass ORDER BY contype, conname;
            """
        )
        restricciones = dict(cur.fetchall())
        for nombre, definicion in restricciones.items():
            print(f"        {nombre}: {definicion}")
        comprobar(
            "clave primaria sobre tipo_reforma",
            restricciones.get("umbrales_gate_pkey") == "PRIMARY KEY (tipo_reforma)",
        )
        comprobar(
            "CHECK de categorías válidas",
            "chk_umbrales_gate_tipo_reforma" in restricciones,
        )
        comprobar(
            "CHECK de umbral positivo",
            "chk_umbrales_gate_umbral_positivo" in restricciones,
        )
        # relrowsecurity es la columna del catálogo pg_class que dice si
        # una tabla tiene RLS activado. Mismo criterio que las otras 8
        # tablas (Decision_RLS_N0.txt).
        cur.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'umbrales_gate'::regclass;"
        )
        comprobar("RLS activado", cur.fetchone()[0] is True)

        print("\n" + "=" * 78)
        print("U2 - Los cuatro umbrales, con los valores de D9")
        print("=" * 78)
        cur.execute(
            "SELECT tipo_reforma, umbral, provisional FROM umbrales_gate ORDER BY tipo_reforma;"
        )
        reales = {t: (u, p) for t, u, p in cur.fetchall()}
        for tipo, (umbral_esperado, prov_esperado) in sorted(ESPERADO.items()):
            real = reales.get(tipo)
            comprobar(
                f"{tipo} -> {umbral_esperado} (provisional={prov_esperado})",
                real == (umbral_esperado, prov_esperado),
                f"(real: {real})",
            )
        comprobar("no sobra ninguna fila", len(reales) == len(ESPERADO), f"({len(reales)})")

        print("\n" + "=" * 78)
        print("U3 a U5 - La tabla rechaza lo que debe rechazar")
        print("=" * 78)
        error, _ = ejecutar_en_savepoint(
            cur,
            "INSERT INTO umbrales_gate (tipo_reforma, umbral) VALUES (%s, %s);",
            ("tejado", 5000),
        )
        comprobar(
            "U3a categoría inventada ('tejado') rechazada por el CHECK",
            isinstance(error, psycopg2.errors.CheckViolation),
            f"({type(error).__name__ if error else 'sin error'})",
        )
        # El caso que motivó el CHECK de D5: 'baño' con eñe entraría sin
        # protestar si solo hubiera clave primaria, y esa fila no la
        # encontraría nunca el servicio, que busca 'bano'.
        error, _ = ejecutar_en_savepoint(
            cur,
            "INSERT INTO umbrales_gate (tipo_reforma, umbral) VALUES (%s, %s);",
            ("baño", 13000),
        )
        comprobar(
            "U3b 'baño' con eñe rechazado por el CHECK",
            isinstance(error, psycopg2.errors.CheckViolation),
            f"({type(error).__name__ if error else 'sin error'})",
        )
        error, _ = ejecutar_en_savepoint(
            cur,
            "INSERT INTO umbrales_gate (tipo_reforma, umbral) VALUES (%s, %s);",
            ("bano", 99999),
        )
        comprobar(
            "U4 categoría repetida rechazada por la clave primaria",
            isinstance(error, psycopg2.errors.UniqueViolation),
            f"({type(error).__name__ if error else 'sin error'})",
        )
        for valor in (0, -100):
            error, _ = ejecutar_en_savepoint(
                cur,
                "UPDATE umbrales_gate SET umbral = %s WHERE tipo_reforma = 'bano';",
                (valor,),
            )
            comprobar(
                f"U5 umbral {valor} rechazado por el CHECK",
                isinstance(error, psycopg2.errors.CheckViolation),
                f"({type(error).__name__ if error else 'sin error'})",
            )

        print("\n" + "=" * 78)
        print("U6 - Ninguna categoría presupuestable se queda sin umbral")
        print("=" * 78)
        # Esta es la comprobación de integridad que de verdad importa
        # para el servicio: si una categoría tuviera tarifa pero no
        # umbral, el cálculo no podría decidir el Gate y acabaría en
        # 'requiere_revision'.
        # LEFT JOIN + WHERE ... IS NULL es la forma estándar en SQL de
        # preguntar "¿qué hay en la primera tabla que NO esté en la
        # segunda?": el LEFT JOIN conserva todas las filas de la
        # izquierda y deja a NULL las columnas de la derecha cuando no
        # hay pareja.
        cur.execute(
            """
            SELECT DISTINCT t.tipo_reforma
            FROM tarifas_base t
            LEFT JOIN umbrales_gate u ON u.tipo_reforma = t.tipo_reforma
            WHERE u.tipo_reforma IS NULL;
            """
        )
        huerfanas = [f[0] for f in cur.fetchall()]
        comprobar(
            "toda categoría con tarifa tiene umbral",
            huerfanas == [],
            f"(sin umbral: {huerfanas})",
        )

        print("\n" + "=" * 78)
        print("U7 - La regla global queda marcada como obsoleta")
        print("=" * 78)
        cur.execute(
            "SELECT descripcion FROM reglas_negocio WHERE clave = 'umbral_aprobacion_manual';"
        )
        fila = cur.fetchone()
        comprobar(
            "reglas_negocio.umbral_aprobacion_manual avisa de que ya no se usa",
            fila is not None and fila[0].startswith("[OBSOLETA desde D9"),
            f"({fila[0][:60] + '...' if fila else 'no existe'})",
        )
finally:
    # Deshace la transacción entera: este script nunca escribe nada de
    # forma permanente.
    cn.rollback()
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
for f in fallos:
    print(f"  FALLO: {f}")
print("=" * 78)
sys.exit(0 if not fallos else 1)
