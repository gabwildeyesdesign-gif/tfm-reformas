"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN. PREPARADA, SIN APLICAR
(PASO 1 del plan D46, docs/Plan_Cambio_Visita_D46.txt, secciones 2 y 7).

Crea la tabla cambios_visita (D46, P2 del veredicto del tutor): una fila por
cada cambio o cancelación de una visita YA confirmada, con el informe de
administración. Es la segunda mitad de la defensa doble: la primera es el
esquema CambioVisitaCreate de Python (app/schemas/cambios_visita.py), que da
422 antes de llegar aquí.

Forma (sección 2 del plan):
  accion              'cambiar' | 'cancelar' (los textos de la petición)
  visita_anterior_id  la confirmada que se cambia o se cancela (obligatoria)
  visita_nueva_id     la confirmada nueva (solo al cambiar)
  motivo              solo al cancelar: precio, plazo, proyecto_no_viable u
                      otro (el catálogo de cliente_cancela, D46.3: sin
                      no_contesta ni sin_decision)
  informe             1..2000 caracteres recortados (como decisiones_gate)
Las dos visitas, con clave foránea DOBLE (visita, oportunidad) hacia
visitas(id, oportunidad_id): garantiza que son de ESTA oportunidad, como en
decisiones_gate (paso12). UNIQUE en cada visita: una visita se cambia o se
cancela una sola vez, y una nueva nace de un solo cambio. Índice por
oportunidad_id para la consulta de la ficha (P10).

Solo EXPAND (añadir): no borra ni renombra nada. main sigue funcionando: no
sabe que la tabla existe. La tabla nace vacía y con RLS activada sin
políticas, como las otras 11 (Decision_RLS_N0.txt).

CUÁNDO: en la fase 2, después de --ensayo, y SOLO tras la frase literal de
Gabi "confirmo la migración paso18". Se aplica ANTES del merge.

Dos modos (patrón de paso17), exactamente uno:
  --ensayo           ejecuta SENTENCIAS, enseña la foto de después y DESHACE
                     todo (ROLLBACK: el DDL de Postgres es transaccional). Su
                     conexión no puede confirmar: commit() lanza
                     CommitProhibido (regla de CLAUDE.md del 2026-10-08).
  --confirmo-paso18  la migración real (commit).
Sin ninguno, o con los dos, no se conecta a nada (código 2).

Una sola transacción: o se crea todo (tabla, restricciones, índice y RLS) o
nada. Con lock_timeout de 5 s: las claves foráneas piden un bloqueo de
oportunidades y de visitas (SHARE ROW EXCLUSIVE: frena escrituras, no
lecturas); si otro proceso lo tuviera, se espera como mucho 5 s y se falla
sin cambiar nada.

Idempotente: si la tabla ya existe con sus 10 restricciones, su índice y la
RLS activada, lo dice y no toca nada. Si existe con otra forma, se PARA sin
tocar nada (estado inesperado: hay que mirarlo).

Las órdenes SQL están en la constante SENTENCIAS, fuera de la parte que se
ejecuta: así check_migracion_cambios_visita.py --ensayo <ruta> puede leerlas
y probarlas (también en copias rotas) sin aplicar nada.

Vuelta atrás (escrita, sin ejecutar): paso18_vuelta_atras.py.
Comprobación: scripts/check_migracion_cambios_visita.py (FALLA antes de
migrar: es su primera prueba en negativo, N16).
"""

# os: leer DATABASE_URL del entorno.
import os
# sys: argumentos, consola y código de salida.
import sys
# Path: rutas de archivos.
from pathlib import Path

# psycopg2: hablar con PostgreSQL.
import psycopg2
# La clase base de las conexiones de psycopg2 (para la del ensayo).
import psycopg2.extensions
# load_dotenv: leer el .env.
from dotenv import load_dotenv

# El nombre de la tabla y los límites. Escritos aquí a propósito: la
# migración no depende del código de app/ (MAX_INFORME_GATE de
# app/schemas/common.py vale lo mismo; si se cambia allí, hay que cambiarlo
# aquí con otra migración).
TABLA = "cambios_visita"
# Máximo de caracteres del informe (el mismo que decisiones_gate).
MAX_INFORME = 2000

# Los nombres de las 10 restricciones (los mismos que comprueba el check).
# Clave primaria.
PK = f"{TABLA}_pkey"
# Clave foránea simple hacia oportunidades (sin ON DELETE, como las demás).
FK_OPORTUNIDAD = f"{TABLA}_oportunidad_id_fkey"
# Las dos claves foráneas dobles hacia visitas(id, oportunidad_id).
FK_ANTERIOR = f"{TABLA}_visita_anterior_oportunidad_fkey"
FK_NUEVA = f"{TABLA}_visita_nueva_oportunidad_fkey"
# Una visita se cambia o se cancela una sola vez; una nueva, de un solo cambio.
UNICA_ANTERIOR = f"{TABLA}_visita_anterior_id_key"
UNICA_NUEVA = f"{TABLA}_visita_nueva_id_key"
# Los cuatro CHECK, con el prefijo chk_ de los demás de la casa.
CHK_ACCION = f"chk_{TABLA}_accion"
CHK_MOTIVO = f"chk_{TABLA}_motivo"
CHK_INFORME = f"chk_{TABLA}_informe"
CHK_COHERENCIA = f"chk_{TABLA}_coherencia"
# Las 10, para la foto y la decisión (en Postgres 18 los NOT NULL también
# salen en pg_constraint: por eso se compara con "<=", no con "==").
RESTRICCIONES = (PK, FK_OPORTUNIDAD, FK_ANTERIOR, FK_NUEVA, UNICA_ANTERIOR, UNICA_NUEVA,
                 CHK_ACCION, CHK_MOTIVO, CHK_INFORME, CHK_COHERENCIA)
# El índice por oportunidad (P10): no respalda ninguna restricción.
INDICE = f"{TABLA}_oportunidad_id_idx"

# Las órdenes de la migración, en orden. Textos FIJOS del programa (ningún
# dato de fuera entra aquí). La primera es SET LOCAL: el tiempo máximo de
# espera por un bloqueo vale solo dentro de ESTA transacción.
SENTENCIAS = (
    # Esperar un bloqueo como mucho 5 segundos.
    "SET LOCAL lock_timeout = '5s';",
    # La tabla. visita_nueva_id y motivo admiten NULL (cada uno solo en una
    # acción). Una clave foránea con alguna columna NULL no se comprueba
    # (MATCH SIMPLE, el modo por defecto): una cancelación no tiene visita
    # nueva. El CHECK de coherencia: cambiar exige visita nueva distinta de
    # la anterior y sin motivo; cancelar, motivo y ninguna visita nueva.
    f"""CREATE TABLE {TABLA} (
        id                  SERIAL NOT NULL,
        oportunidad_id      INTEGER NOT NULL,
        accion              VARCHAR(20) NOT NULL,
        visita_anterior_id  INTEGER NOT NULL,
        visita_nueva_id     INTEGER,
        motivo              VARCHAR(30),
        informe             TEXT NOT NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT {PK} PRIMARY KEY (id),
        CONSTRAINT {FK_OPORTUNIDAD} FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
        CONSTRAINT {FK_ANTERIOR} FOREIGN KEY (visita_anterior_id, oportunidad_id)
            REFERENCES visitas(id, oportunidad_id),
        CONSTRAINT {FK_NUEVA} FOREIGN KEY (visita_nueva_id, oportunidad_id)
            REFERENCES visitas(id, oportunidad_id),
        CONSTRAINT {UNICA_ANTERIOR} UNIQUE (visita_anterior_id),
        CONSTRAINT {UNICA_NUEVA} UNIQUE (visita_nueva_id),
        CONSTRAINT {CHK_ACCION} CHECK (accion IN ('cambiar', 'cancelar')),
        CONSTRAINT {CHK_MOTIVO} CHECK (motivo IS NULL
            OR motivo IN ('precio', 'plazo', 'proyecto_no_viable', 'otro')),
        CONSTRAINT {CHK_INFORME} CHECK (informe = btrim(informe)
            AND char_length(informe) BETWEEN 1 AND {MAX_INFORME}),
        CONSTRAINT {CHK_COHERENCIA} CHECK (
            (accion <> 'cambiar' OR (visita_nueva_id IS NOT NULL AND motivo IS NULL
                                     AND visita_nueva_id <> visita_anterior_id))
            AND (accion <> 'cancelar' OR (visita_nueva_id IS NULL AND motivo IS NOT NULL)))
    );""",
    # El índice por oportunidad (la consulta 7 de la ficha, P10).
    f"CREATE INDEX {INDICE} ON {TABLA} (oportunidad_id);",
    # RLS activada sin políticas, como las otras 11 tablas.
    f"ALTER TABLE {TABLA} ENABLE ROW LEVEL SECURITY;",
)


class CommitProhibido(Exception):
    """Un commit() en el ensayo: nunca debe pasar."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """La conexión del ensayo: su commit() lanza CommitProhibido en vez de
    confirmar (regla de CLAUDE.md desde el 2026-10-08)."""

    def commit(self):
        # En vez de confirmar, el error.
        raise CommitProhibido("commit() prohibido en el ensayo de paso18")


def foto(cur, momento):
    """
    Enseña y devuelve el estado de la tabla: (existe, nombres de sus
    restricciones, existe el índice, RLS activada, número de filas).
    """
    # SQL: ¿existe la tabla? to_regclass da NULL si no existe.
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{TABLA}",))
    # True o False.
    existe = cur.fetchone()[0]
    # Sin tabla: nada más que mirar.
    if not existe:
        # Se enseña y se devuelve vacío.
        print(f"  FOTO {momento}: tabla {TABLA} = NO EXISTE")
        return False, set(), False, False, 0
    # SQL: los nombres de sus restricciones.
    cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass;", (f"public.{TABLA}",))
    # Como conjunto, para comparar sin orden.
    nombres = {fila[0] for fila in cur.fetchall()}
    # SQL: ¿existe el índice? (to_regclass también resuelve índices).
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{INDICE}",))
    # True o False.
    indice = cur.fetchone()[0]
    # SQL: si tiene la RLS activada (pg_class.relrowsecurity).
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (f"public.{TABLA}",))
    # True o False.
    rls = cur.fetchone()[0]
    # SQL: cuántas filas tiene (solo el número: los informes no se imprimen).
    cur.execute(f"SELECT count(*) FROM {TABLA};")
    # El recuento.
    filas = cur.fetchone()[0]
    # Se enseña en una línea.
    print(f"  FOTO {momento}: tabla {TABLA} existe; restricciones {len(nombres)}; índice {indice}; "
          f"RLS {rls}; filas {filas}")
    # Se devuelve para decidir.
    return True, nombres, indice, rls, filas


def como_se_espera(existe, nombres, indice, rls):
    """True si la tabla existe con sus 10 restricciones, su índice y la RLS."""
    # Las cuatro condiciones a la vez.
    return existe and set(RESTRICCIONES) <= nombres and indice and rls


def main():
    """La migración entera: freno, foto, decisión, CREATE, foto y commit o rollback."""
    # Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
    sys.stdout.reconfigure(encoding="utf-8")
    # Qué modo se ha pedido.
    ensayo = "--ensayo" in sys.argv
    # La migración real.
    real = "--confirmo-paso18" in sys.argv
    # El freno: exactamente uno de los dos; si no, se para sin conectarse.
    if ensayo == real:
        # Se explica por qué no hace nada.
        print("No se ejecuta: hace falta UNO de los dos argumentos, --ensayo (deshace todo) o "
              "--confirmo-paso18 (solo tras la frase de Gabi \"confirmo la migración paso18\").")
        # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
        sys.exit(2)

    # parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
    raiz_repo = Path(__file__).resolve().parents[2]
    # Carga el .env de la raíz (DATABASE_URL); su valor no se imprime nunca.
    load_dotenv(raiz_repo / ".env")

    # El modo, en la primera línea de la salida.
    print(f"=== paso18 en modo {'ENSAYO (se deshace todo)' if ensayo else 'REAL (commit)'} ===")
    # Conexión y cursor: una sola transacción. La del ensayo no puede confirmar.
    cn = psycopg2.connect(os.getenv("DATABASE_URL"), connection_factory=ConexionSinCommit if ensayo else None)
    # El cursor.
    cur = cn.cursor()

    # try / except: si algo falla, rollback y no se cambia nada.
    try:
        # La foto de antes.
        existe, nombres, indice, rls, _ = foto(cur, "ANTES")
        # Ya migrada: no se toca nada.
        if como_se_espera(existe, nombres, indice, rls):
            # Se dice.
            print("  la tabla ya existe como se espera: no se toca nada")
            # Se cierra la transacción de lectura.
            cn.rollback()
            # Código 0: nada que hacer.
            sys.exit(0)
        # Existe con otra forma: estado inesperado.
        if existe:
            # Se explica y se sale sin cambios.
            print("  NO SE MIGRA: la tabla existe con otra forma (faltan restricciones, el índice o la RLS); "
                  "hay que revisarlo antes")
            # Nada cambiado.
            cn.rollback()
            # Código 1: no se ha podido hacer.
            sys.exit(1)
        # Las órdenes, una a una, en la misma transacción.
        for sentencia in SENTENCIAS:
            # SQL: cada orden de SENTENCIAS (ver arriba).
            cur.execute(sentencia)
            # Constancia en la salida (solo la primera línea: el CREATE es largo).
            print(f"  ejecutado: {sentencia.splitlines()[0]}")
        # La foto de después (todavía dentro de la transacción).
        existe, nombres, indice, rls, filas = foto(cur, "DESPUÉS")
        # Si no ha quedado como se espera (y vacía), no se confirma.
        if not (como_se_espera(existe, nombres, indice, rls) and filas == 0):
            # Se lanza un error: lo recoge el except, que deshace todo.
            raise RuntimeError("después del CREATE, la tabla, sus restricciones, el índice o la RLS "
                               "no son los esperados")
        # Ensayo: se deshace todo y se comprueba que no queda nada.
        if ensayo:
            # Nada confirmado.
            cn.rollback()
            # Constancia en la salida.
            print("  ENSAYO: rollback() ejecutado, no se ha cambiado nada")
            # La foto final, ya fuera de la transacción del ensayo.
            existe_final = foto(cur, "FINAL")[0]
            # Se cierra la transacción de esa lectura.
            cn.rollback()
            # Si la tabla sigue existiendo, el ensayo ha dejado algo: FALLO.
            if existe_final:
                # Se explica.
                print("  FALLO: el ensayo ha dejado la tabla en el esquema")
                # Código 1.
                sys.exit(1)
        # Real: definitivo.
        else:
            # commit: la tabla, sus restricciones, el índice y la RLS, a la vez.
            cn.commit()
            # Constancia en la salida.
            print("  commit() ejecutado")
    # SystemExit (los sys.exit de arriba) no es un error: se deja pasar.
    except SystemExit:
        # Se vuelve a lanzar tal cual.
        raise
    # Cualquier error: deshacer todo y volver a lanzarlo para verlo.
    except Exception:
        # Nada cambiado.
        cn.rollback()
        # Se dice.
        print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
        # Y se ve el error original.
        raise
    # Pase lo que pase, se cierra la conexión.
    finally:
        # Cerrada.
        cn.close()


# Solo se ejecuta si se lanza el archivo directamente; si otro script lo
# importa (el ensayo de check_migracion_cambios_visita.py), solo lee
# SENTENCIAS y no hace nada.
if __name__ == "__main__":
    # La migración.
    main()
