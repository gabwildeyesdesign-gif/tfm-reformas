"""
Verificación de la migración paso17 (plan: docs/Plan_Antelacion_Visitas_D45.txt,
secciones 4 y 6, V15): las dos reglas de antelación de reglas_negocio.

Qué comprueba (solo LEE; la sesión es de solo lectura), para cada una de
dias_antelacion_visita_chat y dias_antelacion_visita_admin:
  - la fila existe;
  - es un número entero entre 0 y 10 (el rango que valida
    leer_reglas_visita, P5 del plan);
  - su valor es el que confirmó Gabi el 23/10 a las 20:59 (2 y 1; V15 del
    plan aprobado por el tutor);
  - su descripción no está vacía, ocupa una sola línea y nombra D45.

OJO, a diferencia de check_migracion_d32 (que no exige el valor): V15 exige
el VALOR, porque D45 fija esos dos números. Si un día se cambian en
reglas_negocio a propósito, este script tiene que cambiar con ellos (como
check_datos_d39_9_incluye_tipico). No cuenta las filas de reglas_negocio:
solo mira SUS dos claves (regla de CLAUDE.md).

Ejecutado ANTES de la migración tiene que dar FALLO (las filas no existen):
es la prueba en negativo de este script (N12 del plan).

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

# Las dos reglas: (clave, valor esperado). Escritas aquí a propósito, no
# importadas de app/: si alguien cambia el código, este script no cambia
# con él sin que se note.
REGLAS = [
    # El chat: desde el 2.º laborable después de hoy.
    ("dias_antelacion_visita_chat", 2),
    # Administración: desde el 1.er laborable después de hoy.
    ("dias_antelacion_visita_admin", 1),
]
# El rango que valida leer_reglas_visita (MAX_DIAS_ANTELACION).
MINIMO, MAXIMO = 0, 10
# La decisión que tiene que nombrar cada descripción.
DECISION = "D45"

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
print("Migración paso17: dias_antelacion_visita_chat y dias_antelacion_visita_admin (D45)")
# Separador.
print("=" * 78)

# Una regla cada vez.
for numero, (clave, esperado) in enumerate(REGLAS, 1):
    # El título de la regla.
    print(f"\n{numero}. {clave}")
    # SQL: el valor y la descripción de esa clave (UNIQUE: como mucho una fila).
    cur.execute("SELECT valor, descripcion FROM reglas_negocio WHERE clave = %s;", (clave,))
    # La fila, o (None, None) si no existe (así las comprobaciones dan FALLO).
    valor, descripcion = cur.fetchone() or (None, None)
    # [INFO]: el valor actual.
    print(f"    [INFO]  {clave}: valor actual {valor}")
    # Existe.
    comprobar(f"{clave}: la fila existe", valor is not None)
    # Entero dentro del rango.
    comprobar(f"{clave}: entero entre {MINIMO} y {MAXIMO}", entero_en_rango(valor, MINIMO, MAXIMO), f"({valor})")
    # El valor de D45 (Decimal("2.00") == 2 es True en Python).
    comprobar(f"{clave}: vale {esperado} (D45)", valor is not None and valor == esperado, f"({valor})")
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
