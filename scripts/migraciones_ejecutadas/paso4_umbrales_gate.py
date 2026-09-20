"""
MIGRACIÓN DE ESQUEMA YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACIÓN.

Ejecutada el 2026-09-20 contra la base de datos real. Implementa la
decisión D9 (docs/TFM_Decisiones_Modelo_Datos_Leads.txt): el umbral del
Gate HITL deja de ser un único valor global y pasa a depender de
tipo_reforma.

QUÉ CREA: la tabla umbrales_gate, con una fila por categoría.

    tipo_reforma        umbral      provisional
    bano                13000.00    false
    cocina              13000.00    false
    integral_vivienda   10000.00    false
    parcial_acabados    10000.00    TRUE  <- valor provisional, D10

POR QUÉ UNA TABLA NUEVA Y NO UNA COLUMNA EN tarifas_base
    tarifas_base está identificada por la PAREJA (tipo_reforma,
    nivel_acabados): 4 categorías × 3 niveles = 12 filas. El umbral de
    D9 NO depende del nivel de acabados, solo de la categoría. Metido
    ahí, el mismo 13000.00 quedaría escrito tres veces para baño y otras
    tres para cocina, con tres consecuencias reales:
      - cambiar el umbral de una categoría exigiría acertar con tres
        UPDATE, y nada impediría que uno se quedara atrás;
      - si se quedaran distintos, el presupuesto dependería del nivel de
        acabados, que es justo lo que D9 dice que NO debe influir;
      - tarifas_base guarda PRECIOS DE MERCADO; el umbral es un
        parámetro de gobernanza interna, como el margen.
    Aquí, en cambio, la clave primaria ES tipo_reforma, que es
    exactamente de lo que depende el dato: la base de datos garantiza
    por sí sola un umbral por categoría, ni dos ni ninguno.

CONSECUENCIA DE ALCANCE, documentada a propósito: el proyecto pasa de 8
a 9 tablas. El Informe de decisiones N0 afirmaba "8 tablas". Es la misma
clase de ampliación justificada que el salto de 6 a 8 (reglas_negocio y
tarifas_base): responde a un requisito verificado (D9), no a scope creep.

QUÉ NO BORRA: la fila reglas_negocio.umbral_aprobacion_manual sigue
existiendo, pero el código deja de leerla. Para que nadie la use por
error, se le antepone un aviso a su descripción, conservando el texto
original (es documentación de por qué se eligió 10.000 € en su día).

Es idempotente: se puede volver a ejecutar sin romper ni duplicar nada.

Verificación: scripts/check_migracion_umbrales_gate.py (antes de migrar
debe fallar; después, 100 %).
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# Dos niveles por debajo de la raíz (scripts/migraciones_ejecutadas/),
# igual que paso2b_insert.py y paso3_calculate_estimate.py.
RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

# Los cuatro valores son EXACTAMENTE los de los CHECK que ya existen en
# tarifas_base y oportunidades (docs/schema_actual.sql), no una lista
# escrita de memoria. Sin tildes ni eñes: en la base de datos es 'bano'.
TIPOS_REFORMA = ("bano", "cocina", "integral_vivienda", "parcial_acabados")

# Los cuatro umbrales, con la justificación de D9 resumida en la propia
# fila: la descripción viaja con el dato, así que quien mire la tabla en
# Supabase ve por qué vale lo que vale sin abrir la documentación.
FILAS = [
    (
        "bano",
        13000.00,
        False,
        "D9: valor mínimo que devuelve a cocina/medio de tamaño típico (10 m², "
        "11.500 €) por debajo del umbral con margen operativo, sin que bano/alto "
        "deje de disparar Gate en su tamaño típico (punto de corte 6,65 m²).",
    ),
    (
        "cocina",
        13000.00,
        False,
        "D9: mismo umbral que bano. Con 10.000 € un proyecto de cocina/medio de "
        "10 m² (tamaño típico) activaba el Gate, contradiciendo lo que declara "
        "la propia fila de tarifas_base para esa combinación.",
    ),
    (
        "integral_vivienda",
        10000.00,
        False,
        "D9: se mantiene el valor original porque subirlo no tendría ningún "
        "efecto medible: hasta el caso más barato (basico, 45 m²) da 25.875 €, "
        "muy por encima de cualquier umbral entre 10.000 y 20.000 €. Aquí la "
        "variable de control real es incluye_cambios_estructurales.",
    ),
    (
        "parcial_acabados",
        10000.00,
        True,  # provisional: ver D10
        "PROVISIONAL (D9/D10): no es una decisión de negocio cerrada. El umbral "
        "correcto depende de qué superficie se considera válida en esta "
        "categoría, cuestión abierta en D10: un repintado + suelo de 70 m² en "
        "nivel medio ya da 22.540 €, así que la premisa 'parcial_acabados son "
        "siempre reformas menores' no se sostiene. Revisar al cerrar D10.",
    ),
]

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def existe_tabla(nombre):
    """
    Dice si una tabla existe en el esquema public.

    to_regclass() devuelve el identificador interno de la tabla, o NULL
    si no existe. Es la forma de preguntarlo sin que Postgres lance un
    error cuando la respuesta es "no existe".
    """
    cur.execute("SELECT to_regclass(%s);", (f"public.{nombre}",))
    return cur.fetchone()[0] is not None


print("=== ANTES ===")
print(f"  ¿existe umbrales_gate? -> {'SÍ' if existe_tabla('umbrales_gate') else 'NO'}")

try:
    # ------------------------------------------------------------------
    # 1. LA TABLA
    # ------------------------------------------------------------------
    # CREATE TABLE IF NOT EXISTS: si ya existe, no hace nada y no falla.
    # Es lo que hace repetible este script. (Con las restricciones de
    # tabla no existe ese "IF NOT EXISTS", y por eso en paso3 hubo que
    # comprobarlo a mano desde Python; aquí, al ir las restricciones
    # DENTRO del CREATE TABLE, se resuelve solo.)
    #
    # Columna por columna:
    #
    # tipo_reforma VARCHAR(30) PRIMARY KEY
    #   La clave primaria ES el tipo de reforma. Una clave primaria
    #   significa dos cosas a la vez: no puede repetirse (una sola fila
    #   por categoría) y no puede ser NULL. Es la garantía que se
    #   buscaba: imposible tener dos umbrales distintos para baño.
    #   Se usa el propio texto como clave, y no un id numérico, porque
    #   el valor ya es único y estable, y así la consulta del servicio
    #   busca directamente por 'bano' sin tener que traducir nada.
    #
    # umbral NUMERIC(10,2) NOT NULL
    #   NUMERIC es el tipo exacto para dinero (ver el comentario sobre
    #   Decimal en estimate_service.py): nada de float. Mismo tipo y
    #   precisión que reglas_negocio.valor y presupuestos.importe_max,
    #   para poder compararlos sin conversiones.
    #
    # provisional BOOLEAN NOT NULL DEFAULT false
    #   Marca en el PROPIO DATO que un umbral todavía no es una decisión
    #   cerrada (hoy, parcial_acabados). Se puede consultar con SQL
    #   ("¿qué umbrales siguen provisionales?"), cosa que un comentario
    #   en el código o en la documentación no permite.
    #
    # descripcion TEXT / fecha_actualizacion DATE NOT NULL DEFAULT
    # CURRENT_DATE
    #   Mismas dos columnas, con el mismo nombre y significado, que
    #   reglas_negocio y tarifas_base: quien ya conoce esas tablas no
    #   tiene que aprender nada nuevo.
    #
    # CONSTRAINT chk_umbrales_gate_tipo_reforma
    #   Misma defensa que chk_oportunidades_tipo_reforma (D5): la clave
    #   primaria impide repetir, pero NO impide inventar. Sin este CHECK,
    #   un INSERT con 'baño' (con eñe) entraría sin protestar y esa fila
    #   no la encontraría nunca nadie. Aquí NO se permite NULL: la clave
    #   primaria ya lo prohíbe, así que la cláusula "OR IS NULL" que sí
    #   lleva el CHECK de oportunidades no tendría sentido.
    #
    # CONSTRAINT chk_umbrales_gate_umbral_positivo
    #   Un umbral de 0 o negativo haría que TODO presupuesto activara el
    #   Gate, y nadie lo notaría hasta ver administración desbordada.
    #   Es un error de dedo perfectamente posible al editar en Supabase.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS umbrales_gate (
            tipo_reforma         VARCHAR(30)   PRIMARY KEY,
            umbral               NUMERIC(10,2) NOT NULL,
            provisional          BOOLEAN       NOT NULL DEFAULT false,
            descripcion          TEXT,
            fecha_actualizacion  DATE          NOT NULL DEFAULT CURRENT_DATE,
            CONSTRAINT chk_umbrales_gate_tipo_reforma
                CHECK (tipo_reforma IN ('bano', 'cocina', 'integral_vivienda',
                                        'parcial_acabados')),
            CONSTRAINT chk_umbrales_gate_umbral_positivo
                CHECK (umbral > 0)
        );
        """
    )
    print("\n  CREATE TABLE IF NOT EXISTS umbrales_gate -> hecho")

    # ------------------------------------------------------------------
    # 2. ROW LEVEL SECURITY
    # ------------------------------------------------------------------
    # Las 8 tablas anteriores tienen RLS activado (Decision_RLS_N0.txt).
    # Activarlo aquí también no es decorativo: en Supabase, una tabla SIN
    # RLS queda expuesta a través de la Data API pública a los roles anon
    # y authenticated. Con RLS activado y sin políticas, esa puerta queda
    # cerrada. El backend no se ve afectado: se conecta con el usuario
    # dueño de las tablas, que se salta RLS.
    cur.execute("ALTER TABLE umbrales_gate ENABLE ROW LEVEL SECURITY;")
    print("  RLS activado")

    # ------------------------------------------------------------------
    # 3. LAS CUATRO FILAS
    # ------------------------------------------------------------------
    # ON CONFLICT (tipo_reforma) DO NOTHING: si la fila ya existe, no la
    # duplica ni la pisa. Mismo patrón que paso2b_insert.py con
    # reglas_negocio, y mismo motivo: que volver a ejecutar el script no
    # rompa nada.
    #
    # IMPORTANTE, y por eso DO NOTHING y no DO UPDATE: si alguien ajusta
    # un umbral en Supabase, volver a ejecutar esta migración NO debe
    # devolverlo al valor de hoy. Los umbrales son datos de negocio
    # editables sin redeploy; este script solo los SIEMBRA la primera vez.
    for fila in FILAS:
        cur.execute(
            """
            INSERT INTO umbrales_gate (tipo_reforma, umbral, provisional, descripcion)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tipo_reforma) DO NOTHING;
            """,
            fila,
        )
    # cur.rowcount vale 1 si la última sentencia insertó y 0 si hubo
    # conflicto; se imprime el recuento total leyendo la tabla, que es
    # más claro.
    cur.execute("SELECT count(*) FROM umbrales_gate;")
    print(f"  Filas en umbrales_gate: {cur.fetchone()[0]}")

    # ------------------------------------------------------------------
    # 4. AVISO EN LA REGLA QUE QUEDA OBSOLETA
    # ------------------------------------------------------------------
    # La fila reglas_negocio.umbral_aprobacion_manual ya no la lee nadie.
    # No se borra: su descripción explica por qué en su día se pasó de
    # 8.000 a 10.000 €, y eso es historia del proyecto que conviene
    # conservar. Se le antepone un aviso para que nadie la edite creyendo
    # que sigue teniendo efecto.
    #
    # El WHERE con NOT LIKE hace la sentencia repetible: si el aviso ya
    # está puesto, no vuelve a añadirlo. || es el operador de Postgres
    # para pegar dos textos (concatenar).
    cur.execute(
        """
        UPDATE reglas_negocio
        SET descripcion = %s || descripcion
        WHERE clave = 'umbral_aprobacion_manual'
          AND descripcion NOT LIKE %s;
        """,
        (
            "[OBSOLETA desde D9, 2026-09-20: el umbral depende ahora de "
            "tipo_reforma y vive en la tabla umbrales_gate. El código ya no lee "
            "esta fila; editarla no tiene ningún efecto. Se conserva por su "
            "valor histórico.] ",
            "[OBSOLETA%",
        ),
    )
    print(f"  Aviso en reglas_negocio.umbral_aprobacion_manual: {cur.rowcount} fila(s)")

    # Un solo commit para los cuatro pasos: en Postgres los CREATE TABLE
    # y ALTER TABLE son transaccionales igual que un INSERT, así que o se
    # aplica todo o no se aplica nada. No puede quedar la tabla creada
    # pero vacía, que es el estado que haría fallar todos los cálculos.
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
cur.execute(
    "SELECT tipo_reforma, umbral, provisional FROM umbrales_gate ORDER BY tipo_reforma;"
)
for t, u, p in cur.fetchall():
    print(f"  {t:<20} {u:>10}  provisional={p}")

cur.close()
cn.close()
