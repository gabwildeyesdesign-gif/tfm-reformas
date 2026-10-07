"""
Verificación de la migración paso11 (plan: docs/Plan_Endpoint_Listado_WF3.txt,
sección 2): la regla horas_seguimiento_presupuesto en reglas_negocio.

Qué comprueba (solo LEE; la sesión es de solo lectura):
  1-7.  La fila horas_seguimiento_presupuesto existe, su valor es un entero
        mayor que 0 (P6 del plan) y su descripción no está vacía, ocupa una
        sola línea y nombra presupuestos.created_at y fecha_ultimo_contacto
        (las dos columnas de las que depende el plazo).
  8-9.  La otra regla que leerá GET /llamadas-del-dia,
        horas_recordatorio_gate (paso10), existe y es un entero mayor que 0.

Qué NO comprueba, a propósito: el valor EXACTO de las reglas (48 y 24) ni
el número de filas de reglas_negocio. Son la foto de un momento: Gabi
puede cambiar el 48 por 72 sin que nada esté roto, y un recuento de toda
la tabla mira filas que no son de esta migración (regla de CLAUDE.md). La
foto exacta, antes y después, la enseña la propia migración al
ejecutarse. Aquí los valores salen como [INFO].

Ejecutado ANTES de la migración tiene que dar FALLO (la fila no existe):
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

# Las dos claves, en constantes para no repetir el texto.
CLAVE_NUEVA = "horas_seguimiento_presupuesto"
CLAVE_GATE = "horas_recordatorio_gate"

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


def es_entero_positivo(valor):
    """True si valor (Decimal de NUMERIC(10,2)) es un número entero mayor que 0."""
    # None: la fila no existe, así que no es un entero positivo.
    if valor is None:
        return False
    # to_integral_value() redondea al entero; si el número cambia, tenía
    # decimales (24.50 no es un número entero de horas). Y además > 0.
    return valor == valor.to_integral_value() and valor > 0


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
    # Sin fila: dos None, para que las comprobaciones de abajo den FALLO
    # en vez de reventar el script.
    return fila if fila is not None else (None, None)


print("=" * 78)
print("Migración paso11: regla horas_seguimiento_presupuesto")
print("=" * 78)

# ----------------------------------------------------------------------
# 1-7. La regla nueva
# ----------------------------------------------------------------------
print(f"\n1. {CLAVE_NUEVA}")
valor, descripcion = leer_regla(CLAVE_NUEVA)
# [INFO]: el valor actual (48 al ejecutar paso11). No se exige: puede
# cambiarse a mano sin que nada esté roto.
print(f"    [INFO]  valor actual: {valor}")
comprobar("la fila existe", valor is not None)
# Las dos condiciones de P6, por separado para saber cuál falla.
comprobar("el valor no tiene decimales",
          valor is not None and valor == valor.to_integral_value(), f"({valor})")
comprobar("el valor es mayor que 0", valor is not None and valor > 0, f"({valor})")
# "descripcion or ''": si es None, se usa un texto vacío para poder
# buscar dentro sin error.
texto = descripcion or ""
comprobar("la descripción no está vacía", texto.strip() != "")
# Un salto de línea guardado dentro del texto sería un error de cómo se
# escribió la migración (ver el comentario de DESCRIPCION en paso11).
comprobar("la descripción ocupa una sola línea (sin \\n ni \\r)",
          texto != "" and "\n" not in texto and "\r" not in texto)
comprobar("la descripción nombra presupuestos.created_at (la base del plazo)",
          "presupuestos.created_at" in texto)
comprobar("la descripción nombra fecha_ultimo_contacto", "fecha_ultimo_contacto" in texto)

# ----------------------------------------------------------------------
# 8-9. La regla del Gate, que también leerá GET /llamadas-del-dia
# ----------------------------------------------------------------------
print(f"\n2. {CLAVE_GATE} (paso10)")
valor_gate, _ = leer_regla(CLAVE_GATE)
print(f"    [INFO]  valor actual: {valor_gate}")
comprobar("la fila existe", valor_gate is not None)
comprobar("el valor es un entero mayor que 0", es_entero_positivo(valor_gate), f"({valor_gate})")

# Se cierra la transacción de lectura y la conexión.
cn.rollback()
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
    for titulo in fallos:
        print(f"  - {titulo}")
    sys.exit(1)
print("CORRECTO")
