"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN.

Crea la tabla decisiones_gate para POST /gate-decisions (rama
feat/n0-gate-decisions, plan en docs/Plan_Endpoint_Gate_Decisions.txt,
sección 3). Todo en UNA transacción: o se aplica entero o nada.

  1. Tabla decisiones_gate: una fila por oportunidad con Gate, con lo que
     administración decidió tras llamar al cliente (visita_acordada o
     descartar), el motivo si se descarta, la visita si se acuerda, y el
     informe de la llamada. Con sus restricciones: UNIQUE (una decisión
     por oportunidad), 3 claves foráneas y 4 CHECK.
  2. RLS activado en la tabla nueva, en la MISMA transacción: Supabase da
     por defecto todos los permisos a los roles públicos (anon y
     authenticated) sobre cada tabla nueva (docs/Decision_RLS_N0.txt).
     Si el ENABLE fuera en un paso aparte, habría un momento en que la
     tabla estaría abierta a la Data API pública.
  3. Una fila nueva en reglas_negocio: horas_recordatorio_gate = 24. La
     usa WF3 en n8n (barridos periódicos); este backend no la lee.

Es una migración puramente ADITIVA (tabla y fila nuevas, no retira nada),
así que no hace falta partirla en expandir y contraer.

Es idempotente: cada paso comprueba antes si ya está hecho, así que
ejecutarla dos veces deja la base de datos igual que una.
"""

# os: para leer variables de entorno (os.getenv).
import os
# sys: para cambiar la codificación de la salida de la consola.
import sys
# Path: para construir rutas de archivos sin escribirlas a mano.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# load_dotenv: lee el archivo .env y mete sus valores como variables de
# entorno, para que os.getenv("DATABASE_URL") los encuentre.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/, dos
# niveles por debajo de la raíz del repositorio, donde está el .env.
RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

# ----------------------------------------------------------------------
# Nombres, en un solo sitio
# ----------------------------------------------------------------------
# Se escriben como constantes (en MAYÚSCULAS, por convención) para no
# repetir el mismo texto en varios sitios: si hubiera una errata, fallaría
# en todos a la vez y se vería enseguida.
TABLA = "decisiones_gate"
RESTRICCION_UNIQUE = "decisiones_gate_oportunidad_id_key"
CHECK_DECISION = "chk_decisiones_gate_decision"
CHECK_MOTIVO = "chk_decisiones_gate_motivo"
CHECK_INFORME = "chk_decisiones_gate_informe_longitud"
CHECK_COHERENCIA = "chk_decisiones_gate_coherencia"
FK_OPORTUNIDAD = "decisiones_gate_oportunidad_id_fkey"
FK_PRESUPUESTO = "decisiones_gate_presupuesto_id_fkey"
FK_VISITA = "decisiones_gate_visita_id_fkey"
MAX_INFORME = 2000  # el mismo valor que tendrá MAX_INFORME_GATE en Pydantic

CLAVE_REGLA = "horas_recordatorio_gate"
VALOR_REGLA = 24
# La descripción va en UNA sola línea, sin saltos ni sangría (condición 3
# del Bloque B). Varios textos seguidos entre paréntesis, sin comas entre
# ellos, Python los PEGA en uno solo al leer el archivo; así el código se
# lee cómodo sin meter ningún salto de línea en el texto que se guarda.
DESCRIPCION_REGLA = (
    "Horas que puede estar una oportunidad en pendiente_aprobacion sin "
    "decisión antes de que WF3 (barridos periódicos) recuerde a "
    "administración que llame al cliente. Al registrarse la decisión "
    "(POST /gate-decisions) el estado cambia y el recordatorio se detiene solo."
)

# Una sola conexión, y un cursor: el objeto con el que se envían órdenes
# SQL y se leen sus resultados.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


# ----------------------------------------------------------------------
# Funciones de lectura (para el ANTES y el DESPUÉS)
# ----------------------------------------------------------------------
def existe_tabla():
    """True si la tabla decisiones_gate ya existe en el esquema public."""
    # to_regclass('public.decisiones_gate') devuelve el identificador
    # interno de la tabla, o NULL si no existe. Es la forma de preguntar
    # "¿existe?" sin que Postgres lance un error si la respuesta es no.
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{TABLA}",))
    return cur.fetchone()[0]


def restricciones():
    """Nombre -> definición de cada restricción de la tabla (vacío si no existe)."""
    if not existe_tabla():
        return {}
    # pg_constraint es el catálogo de Postgres donde vive cada restricción.
    # pg_get_constraintdef la devuelve escrita como SQL legible.
    cur.execute(
        """
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = %s::regclass
        ORDER BY conname;
        """,
        (TABLA,),
    )
    return dict(cur.fetchall())


def rls_activado():
    """True si la tabla tiene RLS activado (None si no existe)."""
    if not existe_tabla():
        return None
    # pg_class guarda una fila por tabla; relrowsecurity es el interruptor
    # de RLS de esa tabla.
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (TABLA,))
    return cur.fetchone()[0]


def regla():
    """(valor, descripción) de horas_recordatorio_gate, o None si no existe."""
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (CLAVE_REGLA,))
    return cur.fetchone()


def mostrar_estado():
    """Imprime el estado de todo lo que toca esta migración."""
    print(f"    tabla {TABLA} existe: {existe_tabla()}")
    for nombre, definicion in restricciones().items():
        print(f"    {nombre}: {definicion}")
    print(f"    RLS activado: {rls_activado()}")
    fila = regla()
    if fila is None:
        print(f"    {CLAVE_REGLA}: no existe")
    else:
        # repr() enseña el texto con sus caracteres especiales visibles:
        # si hubiera un salto de línea, se vería como \n.
        print(f"    {CLAVE_REGLA} = {fila[0]}  descripción: {fila[1]!r}")


print("=== ANTES ===")
mostrar_estado()

# try / except: se intenta todo el bloque; si CUALQUIER paso lanza un
# error, el except deshace lo hecho (rollback) y vuelve a lanzar el error
# para que se vea.
try:
    # ------------------------------------------------------------------
    # 1. Tabla decisiones_gate
    # ------------------------------------------------------------------
    # IF NOT EXISTS: si la tabla ya existe (segunda ejecución), Postgres no
    # hace nada y no da error. OJO: tampoco la cambia, así que si alguien
    # la hubiera creado a mano con otra forma, este paso no lo arreglaría;
    # por eso el bloque DESPUÉS enseña todas las restricciones y
    # check_migracion_decisiones_gate las comprueba una a una.
    #
    # Columna a columna:
    #   id              SERIAL: un entero que Postgres numera solo (1, 2, 3...).
    #                   PRIMARY KEY: identifica cada fila; único y no nulo.
    #   oportunidad_id  la oportunidad decidida. NOT NULL: obligatorio.
    #   presupuesto_id  qué presupuesto se decidió. Hoy es redundante
    #                   (una oportunidad tiene como mucho un presupuesto),
    #                   pero deja constancia por si en N1 hubiera varios.
    #   visita_id       la visita creada al acordarla; NULL si se descarta.
    #   decision        'visita_acordada' o 'descartar'.
    #   motivo          por qué se descarta; NULL si se acuerda visita.
    #   informe         lo que pasó en la llamada, para la auditoría anual.
    #   created_at      cuándo se registró; DEFAULT now() lo rellena solo.
    #
    # Restricciones (cada una con nombre propio, para reconocerla en los
    # errores y en las pruebas):
    #   UNIQUE (oportunidad_id): una sola decisión por oportunidad (la
    #     decisión es definitiva). Es además la SEGUNDA barrera contra dos
    #     peticiones simultáneas; la primera será el FOR UPDATE del servicio.
    #   FOREIGN KEY ... REFERENCES: el número tiene que existir en la otra
    #     tabla. Sin la oportunidad, el presupuesto o la visita a los que
    #     apunta, Postgres rechaza la fila.
    #   CHECK decision: solo los dos valores del contrato.
    #   CHECK motivo: NULL, o uno de los cinco de la lista cerrada.
    #   CHECK informe: de 1 a 2000 CARACTERES (char_length cuenta
    #     caracteres, no bytes: "á" cuenta 1), como max_length en Pydantic.
    #   CHECK coherencia: las dos únicas combinaciones válidas.
    #     visita_acordada -> tiene visita y no tiene motivo;
    #     descartar       -> tiene motivo y no tiene visita.
    #     Así una fila "acordada sin visita" o "descartada con visita" no
    #     puede existir, la escriba quien la escriba.
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLA} (
            id              SERIAL PRIMARY KEY,
            oportunidad_id  INTEGER NOT NULL,
            presupuesto_id  INTEGER NOT NULL,
            visita_id       INTEGER NULL,
            decision        VARCHAR(20) NOT NULL,
            motivo          VARCHAR(30) NULL,
            informe         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT {RESTRICCION_UNIQUE} UNIQUE (oportunidad_id),
            CONSTRAINT {FK_OPORTUNIDAD} FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
            CONSTRAINT {FK_PRESUPUESTO} FOREIGN KEY (presupuesto_id) REFERENCES presupuestos(id),
            CONSTRAINT {FK_VISITA} FOREIGN KEY (visita_id) REFERENCES visitas(id),
            CONSTRAINT {CHECK_DECISION}
                CHECK (decision IN ('visita_acordada', 'descartar')),
            CONSTRAINT {CHECK_MOTIVO}
                CHECK (motivo IS NULL OR motivo IN
                       ('precio', 'plazo', 'no_contesta', 'proyecto_no_viable', 'otro')),
            CONSTRAINT {CHECK_INFORME}
                CHECK (char_length(informe) BETWEEN 1 AND {MAX_INFORME}),
            CONSTRAINT {CHECK_COHERENCIA} CHECK (
                (decision = 'visita_acordada' AND visita_id IS NOT NULL AND motivo IS NULL)
                OR
                (decision = 'descartar' AND motivo IS NOT NULL AND visita_id IS NULL)
            )
        );
        """
    )
    print(f"\n  1. {TABLA} -> tabla asegurada")

    # ------------------------------------------------------------------
    # 2. RLS activado, en la misma transacción
    # ------------------------------------------------------------------
    # ENABLE sobre una tabla que ya lo tiene no hace nada: es repetible.
    # Sin políticas, RLS deniega todo a los roles públicos; el backend
    # entra como propietario de la tabla y no le afecta
    # (docs/Decision_RLS_N0.txt).
    cur.execute(f"ALTER TABLE {TABLA} ENABLE ROW LEVEL SECURITY;")
    print(f"  2. {TABLA} -> RLS activado")

    # ------------------------------------------------------------------
    # 3. Regla horas_recordatorio_gate
    # ------------------------------------------------------------------
    # ON CONFLICT (clave) DO NOTHING: si ya existe una fila con esa clave
    # (reglas_negocio.clave es UNIQUE), no se inserta ni se sobrescribe.
    # Un valor cambiado a mano por administración no vuelve a 24 al
    # re-ejecutar este script.
    # La descripción viaja como PARÁMETRO (%s), no escrita dentro del SQL:
    # psycopg2 la envía tal cual, sin saltos de línea añadidos.
    cur.execute(
        """
        INSERT INTO reglas_negocio (clave, valor, descripcion)
        VALUES (%s, %s, %s)
        ON CONFLICT (clave) DO NOTHING;
        """,
        (CLAVE_REGLA, VALOR_REGLA, DESCRIPCION_REGLA),
    )
    # rowcount: filas escritas por la última orden. 1 = insertada, 0 = ya existía.
    print(f"  3. {CLAVE_REGLA} = {VALOR_REGLA} -> {'insertada' if cur.rowcount == 1 else 'ya existía, no se toca'}")

    # commit: hace definitivos los tres pasos a la vez.
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    # rollback: deshace TODO lo de este try, como si no se hubiera
    # ejecutado nada. raise vuelve a lanzar el mismo error para verlo.
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
mostrar_estado()

cur.close()
cn.close()
