"""
Verificación de SOLO LECTURA de la migración paso19 (D56, categoría 'pintura').

  P1. Los tres CHECK de tipo_reforma admiten exactamente las 5 categorías
      (y el de oportunidades sigue admitiendo NULL).
  P2. tarifas_base tiene pintura/basico, pintura/medio y pintura/alto.
  P3. Su incluye_tipico es, carácter a carácter, el texto de D56, sin cifras.
  P4. Su precio_m2 es mayor que cero (la cifra NO se imprime nunca).
  P5. umbrales_gate tiene la fila de pintura: umbral > 0 y provisional = false.
  P6. Si existe scripts/local/precios_pintura.json (ignorado por git), las 4
      cifras de la base de datos son las del archivo; si no existe, [INFO].
  P7. Están las 15 combinaciones (5 categorías × 3 niveles) de tarifas_base.
  P8. El Enum TipoReforma del código tiene las mismas 5 categorías que el
      CHECK (defensa doble, como EstadoOportunidad).

FALLA antes de aplicar paso19 (es su primera prueba en negativo) y da todo
correcto después. Una sola transacción REPEATABLE READ, READ ONLY como
primera orden: cualquier escritura daría error. No escribe ningún archivo.

Códigos de salida: 0 todo correcto; 1 algún FALLO.
"""

# json: leer el archivo local de precios.
import json
# os: leer DATABASE_URL del entorno.
import os
# re: sacar los valores de un CHECK y buscar cifras en los textos.
import re
# sys: ruta de importación, salida en UTF-8 y código de salida.
import sys
# Decimal: comparar cifras exactas, nunca float.
from decimal import Decimal
# Path: rutas a partir de la ubicación de este archivo.
from pathlib import Path

# La raíz del repositorio (este archivo vive en scripts/).
RAIZ_REPO = Path(__file__).resolve().parents[1]
# La raíz en la ruta de importación, para "from app.schemas ...".
sys.path.insert(0, str(RAIZ_REPO))
# Salida en UTF-8 (tildes en Windows).
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: la conexión a PostgreSQL.
import psycopg2  # noqa: E402
# load_dotenv: carga el .env sin enseñar ningún valor.
from dotenv import load_dotenv  # noqa: E402

# El Enum del código, para P8.
from app.schemas.common import TipoReforma  # noqa: E402

# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# El archivo local de precios (puede no existir).
RUTA_PRECIOS = RAIZ_REPO / "scripts" / "local" / "precios_pintura.json"
# Las cinco categorías de D56, escritas aquí (no copiadas del código): la prueba compara contra la decisión.
TIPOS = {"bano", "cocina", "integral_vivienda", "parcial_acabados", "pintura"}
# Los tres niveles.
NIVELES = ("basico", "medio", "alto")
# Los tres CHECK: tabla -> nombre.
CHECKS = {"oportunidades": "chk_oportunidades_tipo_reforma",
          "tarifas_base": "tarifas_base_tipo_reforma_check",
          "umbrales_gate": "chk_umbrales_gate_tipo_reforma"}
# Los textos de D56 (decisión de Gabi, 23/11), copiados aquí a propósito: si alguien cambia los de la migración, esto lo ve.
INCLUYE = {
    # basico.
    "basico": ("Protección de suelos y muebles. Pintura plástica en paredes y techos de las estancias "
               "indicadas, en color blanco. No incluye reparar grietas, quitar gotelé ni alisar paredes."),
    # medio.
    "medio": ("Protección de suelos y muebles. Pequeñas reparaciones de grietas y desconchones. Pintura "
              "plástica en paredes y techos, con colores a elegir. No incluye quitar gotelé ni alisar paredes."),
    # alto.
    "alto": ("Protección de suelos y muebles. Quitar el gotelé y alisar las paredes, con reparación de "
             "grietas. Pintura plástica en paredes y techos, con colores a elegir."),
}

# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """Cuenta un acierto o apunta un fallo, e imprime la línea (el detalle nunca lleva cifras)."""
    # Los contadores de arriba.
    global ok
    # Se cumple.
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}")
    # No se cumple.
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo} {detalle}")


# La conexión.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# try / finally: la conexión se cierra siempre.
try:
    # El cursor.
    cur = cn.cursor()
    # SQL: primera orden, misma foto para todo y ninguna escritura posible.
    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;")
    # SQL: la definición de cada uno de los tres CHECK.
    cur.execute("SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = ANY(%s);", (list(CHECKS.values()),))
    # tabla -> definición.
    definiciones = dict(cur.fetchall())
    # SQL: las filas de pintura de tarifas_base; el precio solo como "> 0" (no sale la cifra).
    cur.execute("SELECT nivel_acabados, incluye_tipico, precio_m2 > 0, precio_m2 FROM tarifas_base "
                "WHERE tipo_reforma = 'pintura';")
    # nivel -> (texto, positivo, cifra).
    pintura = {n: (t, pos, p) for n, t, pos, p in cur.fetchall()}
    # SQL: la fila de pintura de umbrales_gate.
    cur.execute("SELECT umbral > 0, provisional, umbral FROM umbrales_gate WHERE tipo_reforma = 'pintura';")
    # Una fila o ninguna.
    umbral = cur.fetchone()
    # SQL: todas las combinaciones (tipo, nivel) que existen en tarifas_base.
    cur.execute("SELECT tipo_reforma, nivel_acabados FROM tarifas_base;")
    # Conjunto de parejas.
    parejas = set(cur.fetchall())
# Solo lectura: no hay nada que confirmar.
finally:
    # Se cierra la transacción.
    cn.rollback()
    # Y la conexión.
    cn.close()

# ------------------------------------------------------------------
print("P1 - Los tres CHECK de tipo_reforma")
# Cada CHECK.
for tabla, nombre in CHECKS.items():
    # Su definición (puede faltar).
    definicion = definiciones.get(tabla)
    # Los valores entre comillas simples.
    valores = set(re.findall(r"'([^']*)'", definicion)) if definicion else None
    # Exactamente las cinco.
    comprobar(f"{nombre} admite exactamente las 5 categorías", valores == TIPOS, f"(admite {sorted(valores or [])})")
# oportunidades sigue admitiendo NULL (lead sin tipo).
comprobar("chk_oportunidades_tipo_reforma sigue admitiendo NULL",
          "IS NULL" in (definiciones.get("oportunidades") or ""))

# ------------------------------------------------------------------
print("\nP2 a P4 - tarifas_base de pintura")
# Los tres niveles, ni uno más.
comprobar("pintura tiene exactamente basico, medio y alto", set(pintura) == set(NIVELES), f"(tiene {sorted(pintura)})")
# Cada nivel.
for nivel in NIVELES:
    # La fila de ese nivel (puede faltar).
    texto, positivo, _ = pintura.get(nivel, (None, None, None))
    # P3: el texto exacto de D56.
    comprobar(f"pintura/{nivel}: incluye_tipico es el texto de D56", texto == INCLUYE[nivel])
    # P3: sin cifras (D39.9).
    comprobar(f"pintura/{nivel}: incluye_tipico sin cifras", texto is not None and not re.search(r"\d", texto))
    # P4: precio mayor que cero (sin enseñarlo).
    comprobar(f"pintura/{nivel}: precio_m2 > 0", positivo is True)

# ------------------------------------------------------------------
print("\nP5 - umbrales_gate de pintura")
# Existe.
comprobar("existe la fila de pintura", umbral is not None)
# Mayor que cero.
comprobar("umbral > 0", umbral is not None and umbral[0] is True)
# No provisional.
comprobar("provisional = false", umbral is not None and umbral[1] is False)

# ------------------------------------------------------------------
print("\nP6 - Las cifras, contra el archivo local (sin imprimirlas)")
# Sin archivo no hay con qué comparar: solo se informa.
if not RUTA_PRECIOS.exists():
    # [INFO], no cuenta ni como OK ni como FALLO.
    print("    [INFO]  no existe scripts/local/precios_pintura.json: no se comparan las cifras")
# Con archivo, se comparan.
else:
    # Se lee con Decimal exacto.
    datos = json.loads(RUTA_PRECIOS.read_text(encoding="utf-8"), parse_float=Decimal)
    # Cada nivel.
    for nivel in NIVELES:
        # La cifra del archivo, a dos decimales.
        esperado = Decimal(str(datos["precio_m2"][nivel])).quantize(Decimal("0.01"))
        # La de la base de datos (puede faltar).
        real = pintura.get(nivel, (None, None, None))[2]
        # Iguales.
        comprobar(f"pintura/{nivel}: precio_m2 igual al del archivo", real == esperado)
    # El umbral del archivo.
    esperado = Decimal(str(datos["umbral_gate"])).quantize(Decimal("0.01"))
    # Igual.
    comprobar("umbral de pintura igual al del archivo", umbral is not None and umbral[2] == esperado)

# ------------------------------------------------------------------
print("\nP7 - Las 15 combinaciones de tarifas_base")
# Las que deberían estar.
todas = {(t, n) for t in TIPOS for n in NIVELES}
# Las que faltan.
faltan = sorted(todas - parejas)
# Ninguna.
comprobar("están las 15 (5 categorías × 3 niveles)", not faltan, f"(faltan {faltan})")
# Las que sobran salen como [INFO] (no son datos de esta migración).
if parejas - todas:
    # Informativo.
    print(f"    [INFO]  otras parejas en la tabla: {sorted(parejas - todas)}")

# ------------------------------------------------------------------
print("\nP8 - El Enum TipoReforma y el CHECK dicen lo mismo")
# Los valores del Enum.
enum = {t.value for t in TipoReforma}
# Iguales a los de D56.
comprobar("TipoReforma tiene exactamente las 5 categorías", enum == TIPOS, f"(tiene {sorted(enum)})")

# ------------------------------------------------------------------
print(f"\nRESULTADO: {ok}/{ok + len(fallos)} correctas")
# Código de salida.
sys.exit(1 if fallos else 0)
