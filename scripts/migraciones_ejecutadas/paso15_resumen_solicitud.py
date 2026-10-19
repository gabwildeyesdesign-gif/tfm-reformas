"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN. PREPARADA, SIN APLICAR.

Añade a leads la columna resumen_solicitud (D28.1: el resumen corto de lo que
el cliente cuenta en el chat, escrito por el Agente 1) y su CHECK de longitud,
chk_leads_resumen_solicitud_longitud, de 1 a 300 caracteres. Es la segunda
mitad de la defensa doble: la primera es la evaluación en Python
(MAX_RESUMEN_SOLICITUD = 300, Bloque B2). Plan:
docs/Plan_Resumen_Solicitud_D28.txt, sección 4.8 (P8 = sí: se aplica en la
rama, antes de B2).

Solo EXPAND (añadir): no borra ni renombra nada. main sigue funcionando: no
lee la columna y todos sus INSERT INTO leads llevan lista de columnas (plan,
2.1). Una columna NULL sin DEFAULT no reescribe la tabla; el CHECK se
comprueba sobre las filas existentes, todas con NULL, que pasan (un CHECK
solo rechaza lo FALSO).

CUÁNDO: en la fase 2 del Bloque B1, después del ensayo con
check_migracion_resumen_solicitud.py --ensayo, y SOLO tras la frase literal
de Gabi "confirmo la migración paso15".

Una sola transacción: o se añaden la columna y el CHECK, o nada. Con
lock_timeout de 5 s: el ALTER TABLE pide un bloqueo exclusivo de leads, y si
otro proceso lo tuviera, se espera como mucho 5 s y se falla sin cambiar
nada, en vez de dejar en cola a todo el mundo.

Idempotente: si la columna y el CHECK ya existen como se espera, lo dice y no
toca nada. Si existe solo una de las dos cosas, o el CHECK con otra
definición, se PARA sin tocar nada (estado inesperado: hay que mirarlo).

Freno: sin el argumento --confirmo-paso15 no se conecta a nada (código 2).

Las órdenes SQL están en la constante SENTENCIAS, fuera de la parte que se
ejecuta: así check_migracion_resumen_solicitud.py --ensayo puede leerlas y
probarlas dentro de una transacción que se deshace, sin aplicar nada.

Vuelta atrás (escrita, sin ejecutar): paso15_vuelta_atras.py.
Comprobación: scripts/check_migracion_resumen_solicitud.py (FALLA antes de
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

# El nombre de la columna y del CHECK, y el máximo. Escritos aquí a propósito:
# la migración no depende del código de app/ (MAX_RESUMEN_SOLICITUD de
# app/schemas/common.py vale lo mismo; si se cambia allí, hay que cambiarlo
# también en la base de datos con otra migración).
COLUMNA = "resumen_solicitud"
# El CHECK, con el prefijo chk_ de los demás de la casa.
RESTRICCION = "chk_leads_resumen_solicitud_longitud"
# El máximo de caracteres.
MAXIMO = 300

# Las órdenes de la migración, en orden. Textos FIJOS del programa (ningún
# dato de fuera entra aquí). La primera es SET LOCAL: el tiempo máximo de
# espera por un bloqueo vale solo dentro de ESTA transacción.
SENTENCIAS = (
    # Esperar un bloqueo como mucho 5 segundos.
    "SET LOCAL lock_timeout = '5s';",
    # La columna: texto, admite NULL, sin valor por defecto.
    f"ALTER TABLE leads ADD COLUMN {COLUMNA} TEXT NULL;",
    # El CHECK: de 1 a MAXIMO caracteres (char_length cuenta caracteres, no
    # bytes). NULL pasa: un CHECK solo rechaza lo FALSO.
    f"ALTER TABLE leads ADD CONSTRAINT {RESTRICCION} "
    f"CHECK (char_length({COLUMNA}) BETWEEN 1 AND {MAXIMO});",
)


def foto(cur, momento):
    """
    Enseña y devuelve el estado de la columna y del CHECK: (tipo de la
    columna o None si no existe, definición del CHECK o None si no existe).
    También cuenta las filas de leads, sin enseñar ningún dato personal.
    """
    # SQL: el tipo de la columna, si existe.
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'leads' AND column_name = %s;",
        (COLUMNA,),
    )
    # La fila, o None si la columna no existe.
    fila_columna = cur.fetchone()
    # SQL: la definición del CHECK, como la escribe Postgres.
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'public.leads'::regclass AND conname = %s;",
        (RESTRICCION,),
    )
    # La fila, o None si el CHECK no existe.
    fila_check = cur.fetchone()
    # SQL: cuántas filas tiene leads (solo el número).
    cur.execute("SELECT count(*) FROM leads;")
    # El recuento.
    total = cur.fetchone()[0]
    # Los dos valores, o None.
    tipo = fila_columna[0] if fila_columna else None
    definicion = fila_check[0] if fila_check else None
    # Se enseñan en dos líneas.
    print(f"  FOTO {momento}: columna leads.{COLUMNA} = {tipo or 'NO EXISTE'}; leads: {total}")
    print(f"  FOTO {momento}: {RESTRICCION} = {definicion or 'NO EXISTE'}")
    # Se devuelven para decidir.
    return tipo, definicion


def check_esperado(definicion):
    """True si la definición del CHECK es la de 1..MAXIMO sobre la columna."""
    # Postgres reescribe BETWEEN como ">= 1 AND <= 300"; se buscan las piezas.
    return (
        definicion is not None
        and f"char_length({COLUMNA})" in definicion
        and ">= 1" in definicion
        and f"<= {MAXIMO}" in definicion
    )


def main():
    """La migración entera: freno, foto, decisión, ALTER, foto y commit."""
    # Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
    sys.stdout.reconfigure(encoding="utf-8")
    # El freno: sin la bandera, se para aquí sin conectarse a nada.
    if "--confirmo-paso15" not in sys.argv:
        # Se explica por qué no hace nada.
        print("No se ejecuta: falta --confirmo-paso15 (solo tras la frase de Gabi "
              "\"confirmo la migración paso15\").")
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
        tipo, definicion = foto(cur, "ANTES")
        # Ya migrada (columna de texto y CHECK correcto): no se toca nada.
        if tipo == "text" and check_esperado(definicion):
            # Se dice.
            print("  la columna y el CHECK ya existen como se espera: no se toca nada")
            # Se cierra la transacción de lectura.
            cn.rollback()
            # Código 0: nada que hacer.
            sys.exit(0)
        # Existe solo una parte, o con otra forma: estado inesperado.
        if tipo is not None or definicion is not None:
            # Se explica y se sale sin cambios.
            print("  NO SE MIGRA: estado inesperado (existe solo la columna, solo el CHECK, "
                  "o con otra definición); hay que revisarlo antes")
            # Nada cambiado.
            cn.rollback()
            # Código 1: no se ha podido hacer.
            sys.exit(1)
        # Las órdenes, una a una, en la misma transacción.
        for sentencia in SENTENCIAS:
            # SQL: cada orden de SENTENCIAS (ver arriba).
            cur.execute(sentencia)
            # Constancia en la salida.
            print(f"  ejecutado: {sentencia}")
        # La foto de después (todavía dentro de la transacción).
        tipo, definicion = foto(cur, "DESPUÉS")
        # Si no ha quedado como se espera, no se confirma.
        if tipo != "text" or not check_esperado(definicion):
            # Se lanza un error: lo recoge el except, que deshace todo.
            raise RuntimeError("después del ALTER, la columna o el CHECK no son los esperados")
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
# importa (el ensayo de check_migracion_resumen_solicitud.py), solo lee
# SENTENCIAS y no hace nada.
if __name__ == "__main__":
    # La migración.
    main()
