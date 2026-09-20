"""
MIGRACIÓN DE ESQUEMA YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACIÓN.

Ejecutada el 2026-09-20 contra la base de datos real. Cierra la deuda
técnica del tope de superficie (m2), documentada como limitación conocida
desde el bloque de calculate-estimate.

QUÉ AÑADE:

    ALTER TABLE leads ADD CONSTRAINT chk_leads_m2_rango
    CHECK ((datos_estructurados ->> 'm2')::numeric > 0
       AND (datos_estructurados ->> 'm2')::numeric <= 500);

POR QUÉ HACE FALTA. Hasta ahora m2 solo exigía "mayor que cero", y solo
en Pydantic. Verificado con ejecución real: un lead de 60.000 m² se
aceptaba (201) y el fallo llegaba DESPUÉS, al calcular, porque
importe_max no cabe en NUMERIC(10,2) (tope 99.999.999,99 €). El cliente
recibía un 500 genérico por un dato que se podía haber rechazado antes.

POR QUÉ 500. El desbordamiento real empieza hacia los 51.151 m² (tarifa
más cara: bano/alto, 1.700 €/m²), así que 500 deja un margen de cien
veces, y sigue siendo holgado para el negocio (el Informe trabaja con
viviendas de 45 a 150 m²).

DEFENSA DOBLE, mismo criterio que D5 con los enums: Pydantic protege la
puerta HTTP (LeadCreate: m2 Decimal, gt=0, le=500 -> 422) y este CHECK
protege la TABLA venga el dato de donde venga (un script suelto, el
editor SQL de Supabase, el nodo Postgres de n8n).

DIFERENCIA CON EL CHECK DE D5, que conviene entender: aquel era sobre una
COLUMNA (oportunidades.tipo_reforma). Aquí no existe ninguna columna m2 —
el dato vive dentro del JSONB leads.datos_estructurados, por decisión D3
(el JSON es la evidencia cruda del formulario, y se descartó añadir
columnas con ALTER TABLE). Así que el CHECK se escribe sobre una
EXPRESIÓN que extrae el valor del JSON. Postgres lo admite siempre que la
expresión sea determinista, como lo son ->> y el cast a numeric.

DOS COMPORTAMIENTOS QUE HAY QUE CONOCER (no son descuidos):

  1. Si la clave 'm2' NO está en el JSON, ->> devuelve NULL, el CHECK se
     evalúa a "desconocido" y la fila SE ACEPTA. Un CHECK en Postgres
     solo rechaza cuando la condición es FALSA. Es decir: este CHECK
     garantiza el RANGO, no la PRESENCIA. Quien garantiza la presencia es
     Pydantic, porque m2 es obligatorio en LeadCreate, y POST /leads es
     el único camino real de escritura. Limitación conocida y aceptada,
     escrita también en la documentación.
  2. Si 'm2' está pero no es un número ("abc"), el cast ::numeric falla
     con un error de tipo (DataError), no con una violación de CHECK. La
     fila tampoco entra, que es lo que importa; el error es distinto.

Es idempotente: si la restricción ya existe, no hace nada.

Verificación: scripts/check_migracion_m2_leads.py.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

NOMBRE_CHECK = "chk_leads_m2_rango"
# El mismo número que MAX_M2_LEAD en app/schemas/common.py. Si se cambia
# uno, hay que cambiar el otro: es el precio de la defensa doble.
MAX_M2 = 500

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def definicion_check():
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'leads'::regclass AND conname = %s;
        """,
        (NOMBRE_CHECK,),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


print("=== ANTES ===")
print(f"  {NOMBRE_CHECK}: {definicion_check()}")

# Un ALTER TABLE ADD CONSTRAINT comprueba TODAS las filas existentes: si
# alguna incumpliera, la migración fallaría entera (y eso es lo correcto,
# porque avisaría de que hay datos que arreglar antes). Se mira primero
# cuántas hay, para que quede en la salida.
cur.execute(
    """
    SELECT count(*) FROM leads
    WHERE (datos_estructurados ->> 'm2') IS NOT NULL
      AND (datos_estructurados ->> 'm2')::numeric NOT BETWEEN 0.01 AND %s;
    """,
    (MAX_M2,),
)
print(f"  Leads existentes que incumplirían el rango: {cur.fetchone()[0]}")

try:
    if definicion_check() is None:
        # Postgres no admite "ADD CONSTRAINT IF NOT EXISTS" para
        # restricciones de tabla (ya comprobado en paso3), así que la
        # comprobación de existencia se hace desde Python.
        cur.execute(
            f"""
            ALTER TABLE leads
                ADD CONSTRAINT {NOMBRE_CHECK} CHECK (
                    (datos_estructurados ->> 'm2')::numeric > 0
                    AND (datos_estructurados ->> 'm2')::numeric <= {MAX_M2}
                );
            """
        )
        print(f"\n  ALTER TABLE leads: {NOMBRE_CHECK} añadido")
    else:
        print(f"\n  {NOMBRE_CHECK} ya existía: no se toca")
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
print(f"  {NOMBRE_CHECK}: {definicion_check()}")

cur.close()
cn.close()
