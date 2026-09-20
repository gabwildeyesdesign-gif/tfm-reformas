"""
MIGRACIÓN DE ESQUEMA YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACIÓN.

Ejecutada el 2026-09-20 contra la base de datos real. Prepara el esquema
para dos decisiones: D13 (seguimiento a 48 h mediante barrido periódico) y
D14 (el precio que se le muestra al cliente lleva el IVA incluido).

CINCO CAMBIOS, todos en UNA transacción (en Postgres los ALTER TABLE son
transaccionales igual que un INSERT: o se aplican los cinco o ninguno, y
así no puede quedar el esquema a medias, que es el estado que rompería
todos los cálculos):

  D13
  1. oportunidades.fecha_ultimo_contacto TIMESTAMPTZ NULL.
  2. oportunidades_estado_check pasa de 7 a 8 valores, añadiendo
     'seguimiento_pendiente'.

  D14
  3. reglas_negocio: clave iva_estandar_pct = 21.
  4. presupuestos.importe_min  -> importe_min_con_iva
     presupuestos.importe_max  -> importe_max_con_iva   (RENAME, no
     columnas nuevas).
  5. presupuestos.iva_pct_aplicado NUMERIC(4,2) NOT NULL DEFAULT 21.

ESTADO DE LOS DATOS AL MIGRAR: presupuestos tenía 0 filas (comprobado con
SELECT count(*) antes de ejecutar), así que el renombrado no deja ningún
importe antiguo con la etiqueta equivocada y no hubo que decidir entre
backfillear (× 1,21) o truncar. Si algún día se repite este escenario con
datos reales, esa decisión NO se toma dentro de un script: se decide antes.

Es idempotente: cada paso comprueba primero si ya está hecho.

Verificación: scripts/check_migracion_iva_y_seguimiento.py.
"""

import os
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

ESTADO_NUEVO = "seguimiento_pendiente"
CLAVE_IVA = "iva_estandar_pct"

# 21 y no 10, aunque el tipo reducido del 10 % existe para obras en
# vivienda. Motivo (D14): el tipo reducido NO es aplicable "porque sí" a
# una reforma; depende de condiciones sobre el cliente y el inmueble
# (entre ellas, que el destinatario sea particular, que la vivienda tenga
# una antigüedad mínima y que el coste de los materiales aportados no
# supere cierto porcentaje de la base imponible). Ninguna de esas
# condiciones se puede determinar en la fase de PRE-ESTIMACIÓN, que es
# justo lo que hace este sistema: se calcula a partir de un formulario
# web, sin haber visitado el inmueble ni haber cerrado qué materiales se
# emplean.
#
# Por eso se aplica el tipo general. Es la opción conservadora en el
# sentido correcto para el cliente: si más adelante resultara aplicable el
# 10 %, el precio final BAJA respecto a lo que se le anticipó. Al revés
# —anticipar un 10 % y acabar facturando un 21 %— el cliente recibiría una
# factura mayor que el presupuesto que aceptó.
VALOR_IVA = Decimal("21")

DESCRIPCION_IVA = (
    "D14: tipo de IVA aplicado al precio que se muestra al cliente en la "
    "pre-estimación. Se usa el tipo general del 21 % y no el reducido del 10 % "
    "porque la elegibilidad del reducido depende de condiciones sobre el cliente "
    "y el inmueble que NO son determinables en la fase de pre-estimación (antes "
    "de la visita técnica y sin cerrar los materiales). Es la opción segura para "
    "el cliente: si luego aplicara el 10 %, el precio final baja respecto a lo "
    "anticipado, nunca al revés."
)

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def existe_columna(tabla, columna):
    """
    Dice si una columna existe. information_schema.columns es el catálogo
    estándar de SQL donde el motor publica la estructura de las tablas;
    se consulta con un SELECT normal, como cualquier otra tabla.
    """
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s;
        """,
        (tabla, columna),
    )
    return cur.fetchone() is not None


def definicion_check(tabla, nombre):
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = %s::regclass AND conname = %s;
        """,
        (tabla, nombre),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


print("=== ANTES ===")
print(f"  oportunidades.fecha_ultimo_contacto existe: "
      f"{existe_columna('oportunidades', 'fecha_ultimo_contacto')}")
print(f"  estado_check: {definicion_check('oportunidades', 'oportunidades_estado_check')}")
cur.execute("SELECT count(*) FROM reglas_negocio WHERE clave = %s;", (CLAVE_IVA,))
print(f"  reglas_negocio.{CLAVE_IVA}: {cur.fetchone()[0]} fila(s)")
print(f"  presupuestos.importe_min_con_iva existe: "
      f"{existe_columna('presupuestos', 'importe_min_con_iva')}")
print(f"  presupuestos.iva_pct_aplicado existe: "
      f"{existe_columna('presupuestos', 'iva_pct_aplicado')}")
cur.execute("SELECT count(*) FROM presupuestos;")
filas_presupuestos = cur.fetchone()[0]
print(f"  filas en presupuestos (afectadas por el renombrado): {filas_presupuestos}")

try:
    # ------------------------------------------------------------------
    # 1. D13 - fecha_ultimo_contacto
    # ------------------------------------------------------------------
    # ADD COLUMN IF NOT EXISTS: si ya está, no hace nada y no falla. Es lo
    # que hace repetible este paso (a diferencia de las restricciones de
    # tabla, que no admiten "IF NOT EXISTS" y hay que comprobar a mano).
    #
    # NULL (sin NOT NULL) a propósito: una oportunidad recién creada por
    # POST /leads todavía no ha tenido ningún contacto, y NULL significa
    # exactamente eso, "aún no ha ocurrido". La alternativa sería
    # rellenarla con created_at, pero eso sería MENTIR: diría que ya se
    # contactó al cliente en el momento de entrar el formulario, y el
    # barrido de seguimiento de D13 dejaría de detectar precisamente los
    # leads a los que nadie ha atendido, que son los que busca.
    #
    # TIMESTAMPTZ y no TIMESTAMP: TIMESTAMPTZ guarda el instante absoluto
    # (internamente en UTC) y lo convierte a la zona horaria de quien
    # consulta; TIMESTAMP guarda "un número de reloj" sin zona, así que
    # las 10:00 pueden significar cosas distintas según quién lo mire.
    # Como el barrido de D13 compara "ahora menos 48 horas", una
    # ambigüedad de zona (o el salto de hora de marzo y octubre) se
    # traduciría en avisos disparados con una o dos horas de desfase. Es
    # además el tipo que ya usan todas las columnas de fecha del proyecto
    # (created_at, updated_at), así que es coherente con el esquema.
    cur.execute(
        "ALTER TABLE oportunidades ADD COLUMN IF NOT EXISTS fecha_ultimo_contacto TIMESTAMPTZ NULL;"
    )
    print("\n  1. fecha_ultimo_contacto -> columna asegurada")

    # ------------------------------------------------------------------
    # 2. D13 - el octavo estado
    # ------------------------------------------------------------------
    # La lista NO se escribe de memoria: se consultó el CHECK real con
    # pg_get_constraintdef antes de escribir esto, y el resultado fueron
    # estos siete valores. El nuevo se añade sin quitar ninguno.
    #
    # DROP + ADD en la MISMA sentencia (separados por coma) porque
    # Postgres no permite editar un CHECK en su sitio; hacerlo en dos
    # sentencias dejaría un instante sin restricción sobre la columna.
    # Mismo patrón que la migración paso3.
    #
    # 'seguimiento_pendiente' se coloca detrás de 'presupuesto_enviado'
    # porque es donde encaja en el proceso: el barrido de D13 marca así a
    # una oportunidad que recibió su presupuesto y lleva 48 h sin
    # respuesta del cliente.
    check_actual = definicion_check("oportunidades", "oportunidades_estado_check")
    if check_actual is None or f"'{ESTADO_NUEVO}'" not in check_actual:
        cur.execute(
            """
            ALTER TABLE oportunidades
                DROP CONSTRAINT oportunidades_estado_check,
                ADD CONSTRAINT oportunidades_estado_check CHECK (estado IN (
                    'nueva',
                    'cualificada',
                    'pendiente_aprobacion',
                    'visita_agendada',
                    'presupuesto_enviado',
                    'seguimiento_pendiente',
                    'ganada',
                    'perdida'
                ));
            """
        )
        print(f"  2. estado_check rehecho con '{ESTADO_NUEVO}' (8 valores)")
    else:
        print(f"  2. el CHECK ya incluía '{ESTADO_NUEVO}': no se toca")

    # ------------------------------------------------------------------
    # 3. D14 - el tipo de IVA como regla editable
    # ------------------------------------------------------------------
    # Va en reglas_negocio, y no como constante en el código, por el mismo
    # motivo que margen_empresa_pct o el umbral del Gate: es un parámetro
    # de negocio que puede cambiar por una decisión ajena al software (una
    # reforma fiscal, por ejemplo) y debe poder cambiarse sin tocar código
    # ni reiniciar el servidor.
    #
    # ON CONFLICT (clave) DO NOTHING: no duplica ni pisa un valor que
    # alguien haya ajustado. Mismo patrón que paso2b y paso4.
    #
    # El valor se pasa como Decimal y no como float: la columna valor es
    # NUMERIC(10,2), el tipo exacto para números que intervienen en
    # cálculos de dinero. Un float introduciría el error binario que el
    # proyecto lleva evitando desde el principio.
    cur.execute(
        """
        INSERT INTO reglas_negocio (clave, valor, descripcion)
        VALUES (%s, %s, %s)
        ON CONFLICT (clave) DO NOTHING;
        """,
        (CLAVE_IVA, VALOR_IVA, DESCRIPCION_IVA),
    )
    print(f"  3. reglas_negocio.{CLAVE_IVA} asegurada (valor {VALOR_IVA})")

    # ------------------------------------------------------------------
    # 4. D14 - renombrado de las dos columnas de importe
    # ------------------------------------------------------------------
    # RENAME COLUMN y no columnas nuevas: el dato que guardan es el mismo
    # concepto (el importe que se le enseña al cliente), solo que hasta
    # ahora el nombre no decía si llevaba IVA o no. Crear columnas nuevas
    # dejaría las viejas ahí, y con ellas la duda de cuál es la buena.
    #
    # RENAME no admite "IF EXISTS" para columnas, así que la repetibilidad
    # se resuelve comprobando antes en information_schema.
    #
    # Renombrar NO convierte los valores: un importe que estuviera
    # guardado sin IVA seguiría sin IVA, ahora con un nombre que dice lo
    # contrario. Aquí no ocurre porque la tabla estaba VACÍA (0 filas,
    # comprobado arriba y antes de ejecutar).
    if existe_columna("presupuestos", "importe_min") and not existe_columna(
        "presupuestos", "importe_min_con_iva"
    ):
        cur.execute("ALTER TABLE presupuestos RENAME COLUMN importe_min TO importe_min_con_iva;")
        cur.execute("ALTER TABLE presupuestos RENAME COLUMN importe_max TO importe_max_con_iva;")
        print("  4. importe_min/importe_max -> importe_min_con_iva/importe_max_con_iva")
    else:
        print("  4. las columnas ya estaban renombradas: no se tocan")

    # ------------------------------------------------------------------
    # 5. D14 - el IVA aplicado queda congelado en cada presupuesto
    # ------------------------------------------------------------------
    # NUMERIC(4,2): hasta 99,99. Un porcentaje de IVA nunca llega a tres
    # cifras enteras, y dos decimales cubren tipos como el 10,5 % que
    # existen en otros países. Es más estrecho que el NUMERIC(10,2) de los
    # importes a propósito: el tipo de la columna documenta qué clase de
    # número se espera.
    #
    # NOT NULL DEFAULT 21: obliga a que todo presupuesto diga con qué IVA
    # se calculó, y el DEFAULT evita que las filas existentes (aquí
    # ninguna) quedaran sin valor.
    #
    # POR QUÉ SE GUARDA EL PORCENTAJE Y NO SE RECALCULA. Un presupuesto es
    # un documento comercial: la cifra que se le enseñó al cliente tiene
    # que poder reconstruirse tal cual meses después. Si el importe se
    # guardara sin IVA y se recalculara al leerlo, bastaría con que
    # alguien cambiara iva_estandar_pct en reglas_negocio para que TODOS
    # los presupuestos ya emitidos cambiaran de precio de forma
    # retroactiva, incluidos los que el cliente ya había aceptado.
    # Guardando el importe ya con IVA y, junto a él, el tipo aplicado, el
    # documento queda fijo en el momento en que se calculó.
    cur.execute(
        "ALTER TABLE presupuestos ADD COLUMN IF NOT EXISTS iva_pct_aplicado NUMERIC(4,2) NOT NULL DEFAULT 21;"
    )
    print("  5. iva_pct_aplicado -> columna asegurada")

    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
print(f"  estado_check: {definicion_check('oportunidades', 'oportunidades_estado_check')}")
cur.execute(
    """
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_name = 'presupuestos' AND column_name LIKE 'i%'
    ORDER BY ordinal_position;
    """
)
for fila in cur.fetchall():
    print(f"  presupuestos.{fila[0]:<22} {fila[1]:<10} nullable={fila[2]:<3} default={fila[3]}")
cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = %s;", (CLAVE_IVA,))
print(f"  {cur.fetchone()}")
cur.execute(
    """
    SELECT column_name, data_type FROM information_schema.columns
    WHERE table_name = 'oportunidades' AND column_name = 'fecha_ultimo_contacto';
    """
)
print(f"  oportunidades.{cur.fetchone()}")

cur.close()
cn.close()
