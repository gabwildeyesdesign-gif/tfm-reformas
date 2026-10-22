"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN. PREPARADA, SIN APLICAR.

Crea la tabla direcciones_obra (D42.1): la dirección de la obra de una
oportunidad, una fila como mucho por oportunidad, con sus tres reglas de
validación como CHECK (D42.3). Es la segunda mitad de la defensa doble: la
primera es el esquema DireccionObra de Python (app/schemas/resultados_llamada.py),
que da 422 antes de llegar aquí.

Por qué una tabla PROPIA (D42.1): el dato es CONFIDENCIAL. Aislado, no sale
en ninguna lectura existente (ninguna consulta lo nombra) y se puede proteger
o borrar por separado (RGPD). Solo lo lee la ficha (D42.5).

Solo EXPAND (añadir): no borra ni renombra nada. main sigue funcionando: no
sabe que la tabla existe. La tabla nace vacía y con RLS activada sin
políticas, como las otras 10 (Decision_RLS_N0.txt: cierra la Data API
pública a anon/authenticated; el backend se la salta).

CUÁNDO: en la fase 2, después del ensayo con
check_migracion_direcciones_obra.py --ensayo, y SOLO tras la frase literal
de Gabi "confirmo la migración paso16".

Una sola transacción: o se crea todo (tabla, restricciones y RLS) o nada.
Con lock_timeout de 5 s: la clave foránea pide un bloqueo de oportunidades
(SHARE ROW EXCLUSIVE: frena escrituras, no lecturas); si otro proceso lo
tuviera, se espera como mucho 5 s y se falla sin cambiar nada.

Idempotente: si la tabla ya existe con sus 6 restricciones y la RLS
activada, lo dice y no toca nada. Si existe con otra forma, se PARA sin
tocar nada (estado inesperado: hay que mirarlo).

Freno: sin el argumento --confirmo-paso16 no se conecta a nada (código 2).

Las órdenes SQL están en la constante SENTENCIAS, fuera de la parte que se
ejecuta: así check_migracion_direcciones_obra.py --ensayo puede leerlas y
probarlas dentro de una transacción que se deshace, sin aplicar nada.

Vuelta atrás (escrita, sin ejecutar): paso16_vuelta_atras.py.
Comprobación: scripts/check_migracion_direcciones_obra.py (FALLA antes de
migrar: es su primera prueba en negativo).
"""

# os: leer DATABASE_URL del entorno.
import os
# sys: argumentos, consola y código de salida.
import sys
# Path: rutas de archivos.
from pathlib import Path

# psycopg2: hablar con PostgreSQL.
import psycopg2
# load_dotenv: leer el .env.
from dotenv import load_dotenv

# El nombre de la tabla y los límites. Escritos aquí a propósito: la
# migración no depende del código de app/ (MAX_CALLE_NUMERO, MAX_MUNICIPIO y
# PATRON_CODIGO_POSTAL de app/schemas/resultados_llamada.py valen lo mismo;
# si se cambian allí, hay que cambiarlos aquí con otra migración).
TABLA = "direcciones_obra"
# Máximo de caracteres de la calle con su número (D42.3).
MAX_CALLE = 150
# Máximo de caracteres del municipio (D42.3).
MAX_MUNICIPIO = 100
# Código postal español: 5 cifras y provincia 01-52 (D42.3).
PATRON_CP = "^(0[1-9]|[1-4][0-9]|5[0-2])[0-9]{3}$"

# Los nombres de las 6 restricciones (los mismos que comprueba el check).
# Clave primaria y clave única por oportunidad (una fila como mucho).
PK = f"{TABLA}_pkey"
UNICA = f"{TABLA}_oportunidad_id_key"
# Clave foránea hacia oportunidades (sin ON DELETE: como las demás de la casa).
FK = f"{TABLA}_oportunidad_id_fkey"
# Los tres CHECK de D42.3, con el prefijo chk_ de los demás de la casa.
CHK_CALLE = f"chk_{TABLA}_calle_numero"
CHK_CP = f"chk_{TABLA}_codigo_postal"
CHK_MUNICIPIO = f"chk_{TABLA}_municipio"
# Las 6, para la foto y la decisión (en Postgres 18 los NOT NULL también
# salen en pg_constraint: por eso se compara con "<=", no con "==").
RESTRICCIONES = (PK, UNICA, FK, CHK_CALLE, CHK_CP, CHK_MUNICIPIO)

# Las órdenes de la migración, en orden. Textos FIJOS del programa (ningún
# dato de fuera entra aquí). La primera es SET LOCAL: el tiempo máximo de
# espera por un bloqueo vale solo dentro de ESTA transacción.
SENTENCIAS = (
    # Esperar un bloqueo como mucho 5 segundos.
    "SET LOCAL lock_timeout = '5s';",
    # La tabla. Los textos, "recortados y no vacíos": igual a su btrim y de
    # 1 a N caracteres (char_length cuenta caracteres, no bytes). El código
    # postal, con la expresión regular (~). created_at y updated_at los pone
    # Postgres; updated_at lo cambia el upsert del servicio (D42.4).
    f"""CREATE TABLE {TABLA} (
        id              SERIAL NOT NULL,
        oportunidad_id  INTEGER NOT NULL,
        calle_numero    TEXT NOT NULL,
        codigo_postal   TEXT NOT NULL,
        municipio       TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT {PK} PRIMARY KEY (id),
        CONSTRAINT {UNICA} UNIQUE (oportunidad_id),
        CONSTRAINT {FK} FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
        CONSTRAINT {CHK_CALLE} CHECK (calle_numero = btrim(calle_numero)
            AND char_length(calle_numero) BETWEEN 1 AND {MAX_CALLE}),
        CONSTRAINT {CHK_CP} CHECK (codigo_postal ~ '{PATRON_CP}'),
        CONSTRAINT {CHK_MUNICIPIO} CHECK (municipio = btrim(municipio)
            AND char_length(municipio) BETWEEN 1 AND {MAX_MUNICIPIO})
    );""",
    # RLS activada sin políticas, como las otras 10 tablas.
    f"ALTER TABLE {TABLA} ENABLE ROW LEVEL SECURITY;",
)


def foto(cur, momento):
    """
    Enseña y devuelve el estado de la tabla: (existe, nombres de sus
    restricciones, RLS activada, número de filas). Nunca lee una fila: el
    dato es confidencial (solo el recuento).
    """
    # SQL: ¿existe la tabla? to_regclass da NULL si no existe.
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{TABLA}",))
    # True o False.
    existe = cur.fetchone()[0]
    # Sin tabla: nada más que mirar.
    if not existe:
        # Se enseña y se devuelve vacío.
        print(f"  FOTO {momento}: tabla {TABLA} = NO EXISTE")
        return False, set(), False, 0
    # SQL: los nombres de sus restricciones.
    cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass;", (f"public.{TABLA}",))
    # Como conjunto, para comparar sin orden.
    nombres = {fila[0] for fila in cur.fetchall()}
    # SQL: si tiene la RLS activada (pg_class.relrowsecurity).
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (f"public.{TABLA}",))
    # True o False.
    rls = cur.fetchone()[0]
    # SQL: cuántas filas tiene (solo el número, nunca el contenido).
    cur.execute(f"SELECT count(*) FROM {TABLA};")
    # El recuento.
    filas = cur.fetchone()[0]
    # Se enseña en una línea.
    print(f"  FOTO {momento}: tabla {TABLA} existe; restricciones {len(nombres)}; RLS {rls}; filas {filas}")
    # Se devuelve para decidir.
    return True, nombres, rls, filas


def main():
    """La migración entera: freno, foto, decisión, CREATE, foto y commit."""
    # Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
    sys.stdout.reconfigure(encoding="utf-8")
    # El freno: sin la bandera, se para aquí sin conectarse a nada.
    if "--confirmo-paso16" not in sys.argv:
        # Se explica por qué no hace nada.
        print("No se ejecuta: falta --confirmo-paso16 (solo tras la frase de Gabi "
              "\"confirmo la migración paso16\").")
        # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
        sys.exit(2)

    # parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
    raiz_repo = Path(__file__).resolve().parents[2]
    # Carga el .env de la raíz (DATABASE_URL); su valor no se imprime nunca.
    load_dotenv(raiz_repo / ".env")

    # Conexión y cursor: una sola transacción hasta el commit o el rollback.
    cn = psycopg2.connect(os.getenv("DATABASE_URL"))
    # El cursor.
    cur = cn.cursor()

    # try / except: si algo falla, rollback y no se cambia nada.
    try:
        # La foto de antes.
        existe, nombres, rls, _ = foto(cur, "ANTES")
        # Ya migrada (las restricciones esperadas y la RLS): no se toca nada.
        if existe and set(RESTRICCIONES) <= nombres and rls:
            # Se dice.
            print("  la tabla ya existe como se espera: no se toca nada")
            # Se cierra la transacción de lectura.
            cn.rollback()
            # Código 0: nada que hacer.
            sys.exit(0)
        # Existe con otra forma: estado inesperado.
        if existe:
            # Se explica y se sale sin cambios.
            print("  NO SE MIGRA: la tabla existe con otra forma (faltan restricciones o la RLS); "
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
        existe, nombres, rls, filas = foto(cur, "DESPUÉS")
        # Si no ha quedado como se espera, no se confirma.
        if not (existe and set(RESTRICCIONES) <= nombres and rls and filas == 0):
            # Se lanza un error: lo recoge el except, que deshace todo.
            raise RuntimeError("después del CREATE, la tabla, sus restricciones o la RLS no son los esperados")
        # Definitivo.
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
# importa (el ensayo de check_migracion_direcciones_obra.py), solo lee
# SENTENCIAS y no hace nada.
if __name__ == "__main__":
    # La migración.
    main()
