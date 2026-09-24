"""
D16 - Verificacion real de la guarda "if not conn.closed" anadida al
bloque except de get_transactional_connection().

Sintoma que motivo el arreglo: si la conexion ya estaba cerrada cuando
saltaba una excepcion dentro del "with", el conn.rollback() del except
lanzaba InterfaceError ENCIMA de la excepcion original, y quien llamaba
veia el InterfaceError en vez del error de verdad.

Segundo modo de fallo, detectado al revisar el arreglo: si la guarda se
escribe con la indentacion equivocada, el "raise" queda DENTRO del if y
la excepcion se suprime en silencio cuando la conexion esta cerrada. Eso
es peor que el sintoma original, y py_compile NO lo detecta (el archivo
compila sin error, porque Python ignora los comentarios al calcular los
niveles de indentacion). Por eso cada caso comprueba por separado que
del "with" sale una excepcion, ANTES de mirar de que tipo es.

TRES CASOS:
  1) Conexion CERRADA + ValueError dentro del with -> sale el ValueError
     original (ni InterfaceError, ni tragada). Ademas, el pool sigue
     sirviendo conexiones despues (SELECT 1 en una nueva).
  2) Conexion VIVA + ValueError dentro del with -> sale el ValueError
     original (la guarda no ha roto el camino normal).
  3) Sin excepcion -> el with termina con normalidad.

Este script NO escribe en ninguna tabla: la unica consulta que manda es
SELECT 1.
"""

import sys
from pathlib import Path

# Este script vive en scripts/, pero el paquete "app" esta en la raiz del
# repositorio. Al ejecutar "python scripts/<archivo>.py", Python solo anade
# la carpeta del archivo (scripts/) a sys.path, asi que "import app.algo"
# fallaria con ModuleNotFoundError. Se calcula la raiz a partir de la
# ubicacion de este mismo archivo, en vez de escribir una ruta absoluta,
# para que funcione en cualquier maquina y desde cualquier directorio.
# Misma tecnica que scripts/check_transactional_connection.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2

from app.db import connection as db

MENSAJE_ORIGINAL = "error original"

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}   {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"  [FALLO] {titulo}   {detalle}")


def cadena_de_causas(exc):
    """
    Devuelve la lista de excepciones encadenadas por debajo de "exc".

    Cuando salta una excepcion DENTRO de un bloque except, Python guarda
    la anterior en el atributo __context__ de la nueva. Si el rollback()
    fallara dentro del except, el InterfaceError quedaria arriba y el
    ValueError debajo, en __context__. Se recorre toda la cadena para
    detectar un InterfaceError escondido, no solo el de la cima.
    """
    causas = []
    actual = exc.__context__ if exc is not None else None
    while actual is not None and actual not in causas:
        causas.append(actual)
        actual = actual.__context__
    return causas


def ejecutar_escenario(cerrar_conexion, lanzar_error):
    """
    Corre un bloque "with get_transactional_connection()" y devuelve un
    diccionario con lo que ocurrio, para comprobarlo desde fuera.

    Los tres casos usan esta misma funcion, cambiando solo los dos
    interruptores. Asi el mecanismo de deteccion es identico en todos:
    escribiendo el try/except/else tres veces a mano podria olvidarse la
    rama "else" en alguna copia, que es justo la que detecta el fallo
    silencioso.

    cerrar_conexion -- si True, se hace conn.close() dentro del with,
                       dejando la conexion muerta (closed != 0).
    lanzar_error    -- si True, se lanza un ValueError dentro del with.
    """
    resultado = {
        "excepcion": None,        # la excepcion que salio del with, si salio
        "termino_limpio": False,  # True si el with no lanzo nada
        "closed_dentro": None,    # valor de conn.closed dentro del with
        "conn": None,             # el objeto conexion, para compararlo luego
    }
    try:
        with db.get_transactional_connection() as conn:
            resultado["conn"] = conn
            if cerrar_conexion:
                # close() cierra la conexion de verdad: a partir de aqui
                # psycopg2 la da por muerta, que es el estado exacto en
                # el que se reprodujo el incidente de D16.
                conn.close()
            resultado["closed_dentro"] = conn.closed
            if lanzar_error:
                # El error "de negocio" que debe llegar intacto a quien
                # llamo. Es el que el InterfaceError tapaba.
                raise ValueError(MENSAJE_ORIGINAL)
    except Exception as exc:
        resultado["excepcion"] = exc
    else:
        # El "else" de un try solo se ejecuta si NO hubo excepcion.
        # En los casos 1 y 2, llegar aqui significa que el with se trago
        # el error; en el caso 3 es justo lo que se espera.
        resultado["termino_limpio"] = True
    return resultado


def comprobar_sale_el_valueerror(res, etiqueta):
    """
    Comprobaciones comunes a los casos 1 y 2: del with tiene que salir
    el ValueError original, entero y sin nada encima.
    """
    encadenadas = cadena_de_causas(res["excepcion"])
    print(f"  Excepcion que salio del with   = {res['excepcion']!r}")
    print(f"  Excepciones encadenadas debajo = {encadenadas!r}")
    print("\n  Comprobaciones:")

    # Esta va PRIMERO a proposito. Si el with se tragara el error, no
    # habria ningun InterfaceError, y una comprobacion del estilo "no es
    # InterfaceError" daria [OK] sobre un fallo mucho peor. La ausencia
    # de excepcion tiene que ser un FALLO explicito por si misma.
    comprobar(
        f"{etiqueta}: el with PROPAGA una excepcion (no se la traga)",
        not res["termino_limpio"] and res["excepcion"] is not None,
        "<-- LA EXCEPCION SE HA TRAGADO" if res["termino_limpio"] else "",
    )
    comprobar(
        f"{etiqueta}: la excepcion que sale es el ValueError original",
        type(res["excepcion"]) is ValueError,
        f"({type(res['excepcion']).__name__})",
    )
    comprobar(
        f"{etiqueta}: conserva el mensaje original intacto",
        str(res["excepcion"]) == MENSAJE_ORIGINAL,
        f"('{res['excepcion']}')",
    )
    comprobar(
        f"{etiqueta}: NO es un InterfaceError (sintoma de D16)",
        not isinstance(res["excepcion"], psycopg2.InterfaceError),
        "",
    )
    comprobar(
        f"{etiqueta}: tampoco hay un InterfaceError encadenado debajo",
        not any(isinstance(c, psycopg2.InterfaceError) for c in encadenadas),
        f"(cadena de {len(encadenadas)})",
    )


# init_pool() abre el pool de conexiones contra Supabase. Sin esta
# llamada, get_transactional_connection() fallaria porque el pool
# (_pool en app/db/connection.py) todavia valdria None.
db.init_pool()

# Todo va dentro de un try/finally para que close_pool() se ejecute
# aunque una prueba reviente a mitad: si no, el proceso se quedaria con
# conexiones abiertas colgando contra el Session pooler de Supabase.
try:
    # ==================================================================
    print("=" * 78)
    print("CASO 1 - Conexion CERRADA + ValueError dentro del with")
    print("=" * 78)

    res1 = ejecutar_escenario(cerrar_conexion=True, lanzar_error=True)
    print(f"  conn.closed dentro del with    = {res1['closed_dentro']}")

    comprobar(
        "CASO 1: premisa, conn.close() deja la conexion cerrada",
        res1["closed_dentro"] not in (None, 0),
        f"(closed={res1['closed_dentro']})",
    )
    comprobar_sale_el_valueerror(res1, "CASO 1")

    # El finally de get_transactional_connection() devolvio al pool una
    # conexion cerrada. Aqui se comprueba que eso no deja el pool
    # inservible para la siguiente peticion.
    print("\n  El pool sigue sirviendo conexiones despues del fallo:")
    with db.get_db_connection() as conn_nueva:
        cur = conn_nueva.cursor()
        cur.execute("SELECT 1;")
        resultado_select = cur.fetchone()[0]
        closed_nueva = conn_nueva.closed
        es_la_misma = conn_nueva is res1["conn"]
        cur.close()

    print(f"    conexion nueva closed = {closed_nueva}")
    print(f"    es el mismo objeto que la cerrada = {es_la_misma}")
    print(f"    SELECT 1 devolvio = {resultado_select}")
    comprobar(
        "CASO 1: la conexion nueva NO viene cerrada",
        closed_nueva == 0,
        f"(closed={closed_nueva})",
    )
    comprobar(
        "CASO 1: el pool no reparte la conexion cerrada otra vez",
        not es_la_misma,
        "",
    )
    comprobar(
        "CASO 1: SELECT 1 responde con normalidad",
        resultado_select == 1,
        f"(={resultado_select})",
    )

    # ==================================================================
    print("\n" + "=" * 78)
    print("CASO 2 - Conexion VIVA + ValueError dentro del with")
    print("=" * 78)

    # Este caso vigila que la guarda no haya roto el camino normal: con
    # la conexion viva se entra en el if, se hace el rollback de verdad,
    # y la excepcion tiene que seguir subiendo igual que antes.
    res2 = ejecutar_escenario(cerrar_conexion=False, lanzar_error=True)
    print(f"  conn.closed dentro del with    = {res2['closed_dentro']}")

    comprobar(
        "CASO 2: premisa, la conexion sigue viva (closed=0)",
        res2["closed_dentro"] == 0,
        f"(closed={res2['closed_dentro']})",
    )
    comprobar_sale_el_valueerror(res2, "CASO 2")

    # ==================================================================
    print("\n" + "=" * 78)
    print("CASO 3 - Sin excepcion: el with termina con normalidad")
    print("=" * 78)

    # Aqui no se lanza nada, asi que no se pasa por el except: se llega
    # al commit() de una transaccion vacia. No escribe ninguna fila.
    res3 = ejecutar_escenario(cerrar_conexion=False, lanzar_error=False)
    print(f"  conn.closed dentro del with    = {res3['closed_dentro']}")
    print(f"  Excepcion que salio del with   = {res3['excepcion']!r}")

    print("\n  Comprobaciones:")
    comprobar(
        "CASO 3: el with termina limpio, sin lanzar nada",
        res3["termino_limpio"],
        "",
    )
    comprobar(
        "CASO 3: no salio ninguna excepcion",
        res3["excepcion"] is None,
        f"({res3['excepcion']!r})",
    )

finally:
    # close_pool() cierra todas las conexiones del pool. Va en el
    # finally para que se ejecute pase lo que pase mas arriba.
    db.close_pool()

# ==================================================================
print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for x in fallos:
        print("  - " + x)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
