"""
Verificación de la migración paso13 (plan: docs/Plan_Agente2_Calendar.txt,
sección 7, Bloque B1): las siete reglas nuevas de reglas_negocio.

Qué comprueba (solo LEE; la sesión es de solo lectura):
  1. Para cada una de las seis reglas de plazos de D18.7: la fila existe, su
     valor es un número entero de semanas entre 1 y 52, y su descripción no
     está vacía, ocupa una sola línea y nombra D18.7.
  2. Para cada pareja (mínimo, máximo): el mínimo no es mayor que el máximo.
  3. paso_huecos_min (D31): existe, es un número entero de minutos entre 1 y
     1440 (un día), y su descripción no está vacía, ocupa una sola línea y
     nombra D31.

Qué NO comprueba, a propósito: el valor EXACTO de cada regla (2, 3, 30...)
ni el número de filas de reglas_negocio. Son la foto de un momento: Gabi
puede cambiar un plazo sin que nada esté roto, y un recuento de toda la
tabla mira filas que no son de esta migración (regla de CLAUDE.md). La
foto exacta, antes y después, la enseña la propia migración. Aquí los
valores salen como [INFO]. Los límites 1..52 y 1..1440 son los que
validará el código al leerlas (B2 y B5).

Ejecutado ANTES de la migración tiene que dar FALLO (las filas no existen):
es la prueba en negativo de este script.

Código de salida: 0 si todo está bien, 1 si hay algún fallo.
"""

# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# Path: rutas de archivos independientes del sistema operativo.
from pathlib import Path

# La raíz del repositorio es la carpeta padre de scripts/. Se añade al
# principio de sys.path para que "import app..." encuentre el paquete app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2

# La dirección de la base de datos, leída del .env por app.config. Este
# script no necesita ningún secreto, y nunca imprime DATABASE_URL.
from app.config import DATABASE_URL

# Las tres parejas (mínimo, máximo) de plazos de D18.7, en semanas.
PAREJAS_PLAZOS = [
    ("semanas_obra_bano_min", "semanas_obra_bano_max"),
    ("semanas_obra_cocina_min", "semanas_obra_cocina_max"),
    ("semanas_fabricacion_cocina_min", "semanas_fabricacion_cocina_max"),
]
# Límites de un plazo en semanas: al menos una, como mucho un año.
MIN_SEMANAS, MAX_SEMANAS = 1, 52
# La regla de la rejilla de huecos (D31) y sus límites: entre 1 minuto y
# un día entero.
CLAVE_PASO = "paso_huecos_min"
MIN_PASO, MAX_PASO = 1, 1440

# Contadores: comprobaciones correctas y títulos de las que fallan.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: se modifica la variable ok del archivo, no una copia local.
    global ok
    # Verdadera: se suma una correcta.
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta en la lista de fallos.
    else:
        fallos.append(titulo)
        print(f"    [FALLO] {titulo}  {detalle}")


# Comprueba un valor de reglas_negocio contra sus límites.
def entero_en_rango(valor, minimo, maximo):
    """True si valor (Decimal de NUMERIC(10,2)) es un entero entre minimo y maximo, ambos incluidos."""
    # None: la fila no existe, así que no cumple.
    if valor is None:
        return False
    # to_integral_value() redondea al entero; si el número cambia, tenía
    # decimales (2.50 semanas no vale). Y además dentro del rango.
    return valor == valor.to_integral_value() and minimo <= valor <= maximo


# Conexión directa a la base de datos.
cn = psycopg2.connect(DATABASE_URL)
# readonly=True: toda la sesión es de solo lectura; Postgres rechazaría
# cualquier escritura. Este script solo mira.
cn.set_session(readonly=True)
# El cursor es el objeto con el que se envían las consultas.
cur = cn.cursor()


def leer_regla(clave):
    """(valor, descripción) de esa clave de reglas_negocio, o (None, None) si no existe."""
    # SQL: el valor y la descripción de la fila con esa clave. clave es
    # UNIQUE (reglas_negocio_clave_key), así que hay como mucho una fila.
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (clave,))
    # La fila como tupla, o None si no hay ninguna.
    fila = cur.fetchone()
    # Sin fila: dos None, para que las comprobaciones den FALLO en vez de
    # reventar el script.
    return fila if fila is not None else (None, None)


def comprobar_regla(clave, minimo, maximo, unidad, decision):
    """Las comprobaciones de una regla; devuelve su valor (o None)."""
    # Se lee la fila.
    valor, descripcion = leer_regla(clave)
    # [INFO]: el valor actual, sin exigirlo.
    print(f"    [INFO]  {clave}: valor actual {valor}")
    # Existe.
    comprobar(f"{clave}: la fila existe", valor is not None)
    # Entero dentro de sus límites.
    comprobar(f"{clave}: entero entre {minimo} y {maximo} {unidad}",
              entero_en_rango(valor, minimo, maximo), f"({valor})")
    # "descripcion or ''": si es None, un texto vacío para poder buscar.
    texto = descripcion or ""
    # Descripción con contenido.
    comprobar(f"{clave}: la descripción no está vacía", texto.strip() != "")
    # Un salto de línea dentro del texto sería un error de la migración.
    comprobar(f"{clave}: la descripción ocupa una sola línea",
              texto != "" and "\n" not in texto and "\r" not in texto)
    # Nombra la decisión de la que sale (D18.7 o D31).
    comprobar(f"{clave}: la descripción nombra {decision}", decision in texto)
    # El valor, para las comprobaciones de las parejas.
    return valor


# Cabecera de la salida.
print("=" * 78)
print("Migración paso13: plazos de D18.7 y paso de la rejilla de huecos (D31)")
print("=" * 78)

# ----------------------------------------------------------------------
# 1 y 2. Plazos de D18.7, regla a regla y por parejas
# ----------------------------------------------------------------------
print("\n1. Plazos de D18.7 (semanas)")
# Una pareja cada vez.
for clave_min, clave_max in PAREJAS_PLAZOS:
    # Las dos reglas de la pareja.
    valor_min = comprobar_regla(clave_min, MIN_SEMANAS, MAX_SEMANAS, "semanas", "D18.7")
    valor_max = comprobar_regla(clave_max, MIN_SEMANAS, MAX_SEMANAS, "semanas", "D18.7")
    # El mínimo no supera al máximo (solo si las dos existen).
    comprobar(f"{clave_min} <= {clave_max}",
              valor_min is not None and valor_max is not None and valor_min <= valor_max,
              f"({valor_min} <= {valor_max})")

# ----------------------------------------------------------------------
# 3. Paso de la rejilla de huecos (D31)
# ----------------------------------------------------------------------
print("\n2. Rejilla de huecos libres (D31)")
comprobar_regla(CLAVE_PASO, MIN_PASO, MAX_PASO, "minutos", "D31")

# Se cierra la transacción de lectura.
cn.rollback()
# Y la conexión.
cn.close()

# ----------------------------------------------------------------------
# Resumen y código de salida
# ----------------------------------------------------------------------
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total}")
# Con algún fallo: se listan y el script sale con código 1, para que la
# suite lo cuente como fallo sin tener que leer la salida.
if fallos:
    print("HAY FALLOS:")
    # Uno por línea.
    for titulo in fallos:
        print(f"  - {titulo}")
    # Código 1: fallo.
    sys.exit(1)
# Sin fallos.
print("CORRECTO")
