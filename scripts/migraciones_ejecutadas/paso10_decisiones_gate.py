"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN.

Crea la tabla decisiones_gate para POST /gate-decisions (rama
feat/n0-gate-decisions, plan en docs/Plan_Endpoint_Gate_Decisions.txt,
sección 3). Todo en UNA transacción: o se aplica entero o nada.

  1. visitas: restricción UNIQUE (id, oportunidad_id), con nombre propio.
     No limita nada nuevo (id ya es único por sí solo), pero PostgreSQL
     solo deja crear una clave foránea hacia un grupo de columnas que sea
     único en conjunto. Es el requisito de la clave doble del paso 2.
  2. Tabla decisiones_gate: una fila por oportunidad con Gate, con lo que
     administración decidió tras llamar al cliente (visita_acordada o
     descartar), el motivo si se descarta, la visita si se acuerda, y el
     informe de la llamada. Restricciones: UNIQUE (una decisión por
     oportunidad), clave foránea a oportunidades, clave foránea DOBLE
     (visita_id, oportunidad_id) -> visitas (id, oportunidad_id), y 4
     CHECK. Sin presupuesto_id: el presupuesto se obtiene siempre desde
     oportunidad_id (presupuestos.oportunidad_id es UNIQUE), y guardarlo
     dos veces permitiría que las dos copias se contradijeran.
  3. RLS activado en la tabla nueva, en la MISMA transacción: Supabase da
     por defecto todos los permisos a los roles públicos (anon y
     authenticated) sobre cada tabla nueva (docs/Decision_RLS_N0.txt).
  4. Una fila nueva en reglas_negocio: horas_recordatorio_gate = 24. La
     usa WF3 en n8n (barridos periódicos); este backend no la lee.

Es una migración puramente ADITIVA (una restricción, una tabla y una fila
nuevas; no retira nada), así que no hace falta partirla en expandir y
contraer.

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
UNIQUE_VISITAS = "visitas_id_oportunidad_id_key"  # en la tabla visitas
RESTRICCION_UNIQUE = "decisiones_gate_oportunidad_id_key"
CHECK_DECISION = "chk_decisiones_gate_decision"
CHECK_MOTIVO = "chk_decisiones_gate_motivo"
CHECK_INFORME = "chk_decisiones_gate_informe"
CHECK_COHERENCIA = "chk_decisiones_gate_coherencia"
FK_OPORTUNIDAD = "decisiones_gate_oportunidad_id_fkey"
FK_VISITA_DOBLE = "decisiones_gate_visita_oportunidad_fkey"
MAX_INFORME = 2000  # el mismo valor que tendrá MAX_INFORME_GATE en Pydantic

CLAVE_REGLA = "horas_recordatorio_gate"
VALOR_REGLA = 24
# La descripción va en UNA sola línea, sin saltos ni sangría. Varios textos
# seguidos entre paréntesis, sin comas entre ellos, Python los PEGA en uno
# solo al leer el archivo; así el código se lee cómodo sin meter ningún
# salto de línea en el texto que se guarda.
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
# Funciones de lectura (para el ANTES, el DESPUÉS y la idempotencia)
# ----------------------------------------------------------------------
def existe_tabla():
    """True si la tabla decisiones_gate ya existe en el esquema public."""
    # to_regclass('public.decisiones_gate') devuelve el identificador
    # interno de la tabla, o NULL si no existe. Es la forma de preguntar
    # "¿existe?" sin que Postgres lance un error si la respuesta es no.
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{TABLA}",))
    return cur.fetchone()[0]


def definicion_restriccion(tabla, nombre):
    """La definición SQL de una restricción de esa tabla, o None si no existe."""
    # pg_constraint es el catálogo de Postgres donde vive cada restricción.
    # pg_get_constraintdef la devuelve escrita como SQL legible.
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s;",
        (tabla, nombre),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


def restricciones_tabla():
    """Nombre -> definición de cada restricción de decisiones_gate (vacío si no existe)."""
    if not existe_tabla():
        return {}
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass ORDER BY conname;",
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
    print(f"    visitas.{UNIQUE_VISITAS}: {definicion_restriccion('visitas', UNIQUE_VISITAS)}")
    print(f"    tabla {TABLA} existe: {existe_tabla()}")
    for nombre, definicion in restricciones_tabla().items():
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
    # 1. visitas: UNIQUE (id, oportunidad_id)
    # ------------------------------------------------------------------
    # Requisito de la clave foránea doble del paso 2: PostgreSQL solo deja
    # que una clave foránea apunte a columnas que sean únicas EN CONJUNTO
    # (una PRIMARY KEY o una UNIQUE sobre exactamente esas columnas).
    # No puede fallar con los datos que ya hay: id es la clave primaria,
    # así que cada pareja (id, oportunidad_id) ya es distinta de todas.
    # Coste: Postgres crea un índice pequeño para respaldarla.
    #
    # ALTER TABLE ... ADD CONSTRAINT no admite IF NOT EXISTS: la
    # repetibilidad se consigue preguntando antes a pg_constraint.
    if definicion_restriccion("visitas", UNIQUE_VISITAS) is None:
        cur.execute(f"ALTER TABLE visitas ADD CONSTRAINT {UNIQUE_VISITAS} UNIQUE (id, oportunidad_id);")
        print(f"\n  1. visitas.{UNIQUE_VISITAS} -> creada")
    else:
        print(f"\n  1. visitas.{UNIQUE_VISITAS} ya existía: no se toca")

    # ------------------------------------------------------------------
    # 2. Tabla decisiones_gate
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
    #   FOREIGN KEY (oportunidad_id) -> oportunidades(id): la oportunidad
    #     tiene que existir.
    #   FOREIGN KEY DOBLE (visita_id, oportunidad_id) -> visitas (id,
    #     oportunidad_id): la PAREJA tiene que existir junta en visitas, es
    #     decir, la visita tiene que ser DE ESA MISMA oportunidad. Con una
    #     clave simple sobre visita_id bastaría con que la visita existiera,
    #     aunque fuera de otro cliente.
    #     Comportamiento por defecto MATCH SIMPLE: si ALGUNA de las dos
    #     columnas es NULL, la clave no se comprueba. En un descarte
    #     visita_id es NULL, así que la clave doble no se mira: es lo que se
    #     quiere, porque un descarte no tiene visita. Que un descarte no
    #     tenga visita, y una visita acordada sí, lo garantiza el CHECK de
    #     coherencia.
    #   CHECK decision: solo los dos valores del contrato.
    #   CHECK motivo: NULL, o uno de los cinco de la lista cerrada.
    #   CHECK informe: dos condiciones a la vez (AND):
    #     informe = btrim(informe): el texto ya está guardado RECORTADO,
    #       como lo guardará Pydantic. btrim quita los espacios de los dos
    #       extremos; si al quitarlos el texto cambia, es que los tenía, y
    #       se rechaza. Un informe de solo espacios también: btrim lo deja
    #       vacío, distinto del original.
    #       OJO: btrim sin segundo argumento solo quita el carácter ESPACIO,
    #       no tabuladores ni saltos de línea (Python .strip() sí).
    #     char_length(informe) BETWEEN 1 AND 2000: de 1 a 2000 CARACTERES
    #       (char_length cuenta caracteres, no bytes: "á" cuenta 1).
    #     Es la defensa doble del informe: aunque administración escriba en
    #     la tabla a mano, sin pasar por Pydantic, no entra un informe vacío,
    #     de solo espacios, o con espacios en los extremos.
    #   CHECK coherencia: las dos únicas combinaciones válidas.
    #     visita_acordada -> tiene visita y no tiene motivo;
    #     descartar       -> tiene motivo y no tiene visita.
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLA} (
            id              SERIAL PRIMARY KEY,
            oportunidad_id  INTEGER NOT NULL,
            visita_id       INTEGER NULL,
            decision        VARCHAR(20) NOT NULL,
            motivo          VARCHAR(30) NULL,
            informe         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT {RESTRICCION_UNIQUE} UNIQUE (oportunidad_id),
            CONSTRAINT {FK_OPORTUNIDAD} FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
            CONSTRAINT {FK_VISITA_DOBLE} FOREIGN KEY (visita_id, oportunidad_id)
                REFERENCES visitas (id, oportunidad_id),
            CONSTRAINT {CHECK_DECISION}
                CHECK (decision IN ('visita_acordada', 'descartar')),
            CONSTRAINT {CHECK_MOTIVO}
                CHECK (motivo IS NULL OR motivo IN
                       ('precio', 'plazo', 'no_contesta', 'proyecto_no_viable', 'otro')),
            CONSTRAINT {CHECK_INFORME}
                CHECK (informe = btrim(informe) AND char_length(informe) BETWEEN 1 AND {MAX_INFORME}),
            CONSTRAINT {CHECK_COHERENCIA} CHECK (
                (decision = 'visita_acordada' AND visita_id IS NOT NULL AND motivo IS NULL)
                OR
                (decision = 'descartar' AND motivo IS NOT NULL AND visita_id IS NULL)
            )
        );
        """
    )
    print(f"  2. {TABLA} -> tabla asegurada")

    # ------------------------------------------------------------------
    # 3. RLS activado, en la misma transacción
    # ------------------------------------------------------------------
    # ENABLE sobre una tabla que ya lo tiene no hace nada: es repetible.
    # Sin políticas, RLS deniega todo a los roles públicos; el backend
    # entra como propietario de la tabla y no le afecta
    # (docs/Decision_RLS_N0.txt).
    cur.execute(f"ALTER TABLE {TABLA} ENABLE ROW LEVEL SECURITY;")
    print(f"  3. {TABLA} -> RLS activado")

    # ------------------------------------------------------------------
    # 4. Regla horas_recordatorio_gate
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
    print(f"  4. {CLAVE_REGLA} = {VALOR_REGLA} -> {'insertada' if cur.rowcount == 1 else 'ya existía, no se toca'}")

    # commit: hace definitivos los cuatro pasos a la vez.
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
