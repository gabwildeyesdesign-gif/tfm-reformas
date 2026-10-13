"""
Verificación de la migración paso14 (plan: docs/Plan_Horario_Visitas_D32.txt,
secciones 2.1 y 5, Bloque B1): las dos reglas nuevas de reglas_negocio.

Qué comprueba (solo LEE; la sesión es de solo lectura):
  1. desplazamiento_min (D23.5, D32): la fila existe, es un número entero de
     minutos entre 0 y 1440, y su descripción no está vacía, ocupa una sola
     línea y nombra D32.
  2. dias_oferta_visita (D32): la fila existe, es un número entero entre 1 y
     60 (el tope de búsqueda de días del plan, 3.3), y su descripción no está
     vacía, ocupa una sola línea y nombra D32.

Qué NO comprueba, a propósito: el valor EXACTO de cada regla (15, 10) ni el
número de filas de reglas_negocio (regla de CLAUDE.md: los scripts solo
comprueban SUS datos; Gabi puede cambiar un valor sin que nada esté roto).
Los valores salen como [INFO]. Los límites son los que valida el código al
leerlas (leer_reglas_visita, Bloque B2). Que la rejilla con los valores
reales sea la de D23.5 lo comprueba check_disponibilidad_service (B2).

Ejecutado ANTES de la migración tiene que dar FALLO (las filas no existen):
es la prueba en negativo de este script.

Código de salida: 0 si todo está bien, 1 si hay algún fallo.
"""

# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# Path: rutas de archivos independientes del sistema operativo.
from pathlib import Path

# La raíz del repositorio (carpeta padre de scripts/), para "import app...".
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda.
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2

# La dirección de la base de datos, leída del .env por app.config (nunca se
# imprime).
from app.config import DATABASE_URL

# Las dos reglas, con sus límites (los mismos que valida el código en B2).
REGLAS = [
    # (clave, mínimo, máximo, unidad)
    ("desplazamiento_min", 0, 1440, "minutos"),
    # El tope de 60 es el de búsqueda de días del modo días (plan, 3.3).
    ("dias_oferta_visita", 1, 60, "días"),
]
# La decisión que tiene que nombrar cada descripción.
DECISION = "D32"

# Contadores: comprobaciones correctas y títulos de las que fallan.
ok = 0
# Los títulos de las que fallan.
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: se modifica la variable ok del archivo, no una copia local.
    global ok
    # Verdadera: se suma una correcta.
    if condicion:
        # Una más.
        ok += 1
        # Se enseña.
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta en la lista de fallos.
    else:
        # Para el resumen.
        fallos.append(titulo)
        # Se enseña.
        print(f"    [FALLO] {titulo}  {detalle}")


def entero_en_rango(valor, minimo, maximo):
    """True si valor (Decimal de NUMERIC(10,2)) es un entero entre minimo y maximo, ambos incluidos."""
    # None: la fila no existe, así que no cumple.
    if valor is None:
        # No cumple.
        return False
    # Sin decimales (to_integral_value no lo cambia) y dentro del rango.
    return valor == valor.to_integral_value() and minimo <= valor <= maximo


# Conexión directa a la base de datos.
cn = psycopg2.connect(DATABASE_URL)
# readonly=True: toda la sesión es de solo lectura (Postgres rechazaría
# cualquier escritura). Este script solo mira.
cn.set_session(readonly=True)
# El cursor.
cur = cn.cursor()

# Cabecera de la salida.
print("=" * 78)
# El título.
print("Migración paso14: desplazamiento_min y dias_oferta_visita (D32)")
# Separador.
print("=" * 78)

# Una regla cada vez.
for numero, (clave, minimo, maximo, unidad) in enumerate(REGLAS, 1):
    # El título de la regla.
    print(f"\n{numero}. {clave}")
    # SQL: el valor y la descripción de esa clave (UNIQUE: como mucho una fila).
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (clave,))
    # La fila, o (None, None) si no existe (así las comprobaciones dan FALLO).
    valor, descripcion = cur.fetchone() or (None, None)
    # [INFO]: el valor actual, sin exigirlo.
    print(f"    [INFO]  {clave}: valor actual {valor}")
    # Existe.
    comprobar(f"{clave}: la fila existe", valor is not None)
    # Entero dentro de sus límites.
    comprobar(f"{clave}: entero entre {minimo} y {maximo} {unidad}",
              entero_en_rango(valor, minimo, maximo), f"({valor})")
    # "or ''": si es None, un texto vacío para poder buscar.
    texto = descripcion or ""
    # Descripción con contenido.
    comprobar(f"{clave}: la descripción no está vacía", texto.strip() != "")
    # Un salto de línea dentro del texto sería un error de la migración.
    comprobar(f"{clave}: la descripción ocupa una sola línea",
              texto != "" and "\n" not in texto and "\r" not in texto)
    # Nombra la decisión de la que sale.
    comprobar(f"{clave}: la descripción nombra {DECISION}", DECISION in texto)

# Se cierra la transacción de lectura.
cn.rollback()
# Y la conexión.
cn.close()

# Resumen.
total = ok + len(fallos)
# Separador.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{total}")
# Con algún fallo: se listan y código 1.
if fallos:
    # Cabecera.
    print("HAY FALLOS:")
    # Uno por línea.
    for titulo in fallos:
        # Con su título.
        print(f"  - {titulo}")
    # Código 1: fallo.
    sys.exit(1)
# Sin fallos.
print("CORRECTO")
