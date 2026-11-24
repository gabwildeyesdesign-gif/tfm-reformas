"""
MIGRACIÓN DE ESQUEMA Y DATOS - NO ES UN SCRIPT DE VERIFICACIÓN.
PREPARADA, SIN APLICAR (D56, categoría nueva 'pintura').

Hace tres cosas, en UNA sola transacción (o todas o ninguna):

  1. Amplía los tres CHECK de tipo_reforma para que admitan 'pintura':
       chk_oportunidades_tipo_reforma   (oportunidades; sigue admitiendo NULL)
       tarifas_base_tipo_reforma_check  (tarifas_base)
       chk_umbrales_gate_tipo_reforma   (umbrales_gate)
  2. Inserta 3 filas en tarifas_base: pintura/basico, pintura/medio y
     pintura/alto, con su precio_m2 y su incluye_tipico (una línea, sin
     cifras, como pide D39.9).
  3. Inserta 1 fila en umbrales_gate: el umbral del Gate de pintura.

LOS PRECIOS Y EL UMBRAL NO ESTÁN EN ESTE ARCHIVO (D40.9: el repositorio es
público). Se leen de scripts/local/precios_pintura.json, que está en
.gitignore. Si el archivo no existe, no está ignorado por git o trae una
cifra no válida, la migración PARA sin conectarse a la base de datos.
Formato del archivo (las cifras, como texto con punto decimal):
    {"precio_m2": {"basico": "…", "medio": "…", "alto": "…"},
     "umbral_gate": "…",
     "fuente": "texto opcional para tarifas_base.fuente"}
Ninguna cifra se imprime nunca: ni la de antes ni la de después.

CUÁNDO SE APLICA (distinto de paso17): DESPUÉS del merge del código con
TipoReforma.PINTURA y DESPUÉS de reiniciar el uvicorn del 8000 con él. Motivo:
la tool get_business_rules lee TODAS las filas de tarifas_base y valida cada
tipo_reforma con el Enum (app/schemas/business_rules.py, IncluyeTipico); con
filas 'pintura' y un servidor sin PINTURA, la tool daría un error a cada
llamada. Este script solo puede comprobar el código LOCAL (freno 1), no el
del servidor: el reinicio lo confirma Gabi.

Idempotente: un CHECK que ya admite 'pintura' no se toca; una fila que ya
existe no se duplica ni se sobrescribe (se dice si coincide o no con el
archivo, sin enseñar la cifra).

Dos modos:
  --ensayo          hace todo, enseña el después y DESHACE (ROLLBACK). Su
                    conexión no puede confirmar: commit() lanza
                    CommitProhibido (regla de las conexiones de prueba sin
                    commit, CLAUDE.md).
  --confirmo-paso19 la migración real (commit). Solo tras la frase de Gabi
                    "confirmo la migración paso19".
Sin ninguno de los dos, o con los dos, no hace nada (código 2).

Vuelta atrás (escrita, sin ejecutar): paso19_vuelta_atras.py.
Comprobación: scripts/check_migracion_pintura.py (FALLA antes de migrar).
Después de aplicarla: regenerar docs/schema_actual.sql (scripts/dump_schema.py).
"""

# hashlib: la huella SHA-256 de las filas ajenas, para compararlas sin imprimirlas.
import hashlib
# json: leer el archivo local de precios.
import json
# os: leer DATABASE_URL del entorno.
import os
# re: sacar de la definición de un CHECK la lista de valores que admite.
import re
# subprocess: preguntar a git si el archivo local está ignorado.
import subprocess
# sys: argumentos, ruta de importación, salida en UTF-8 y código de salida.
import sys
# Decimal: las cifras exactas, nunca float (D4).
from decimal import Decimal, InvalidOperation
# Path: rutas a partir de la ubicación de este archivo.
from pathlib import Path

# Salida en UTF-8: sin esto, en Windows las tildes salen rotas en una tubería.
sys.stdout.reconfigure(encoding="utf-8")

# Qué modo se ha pedido: el ensayo.
ENSAYO = "--ensayo" in sys.argv
# O la migración real.
REAL = "--confirmo-paso19" in sys.argv
# El freno de los argumentos: exactamente uno de los dos.
if ENSAYO == REAL:
    # Se explica por qué no hace nada.
    print("No se ejecuta: hace falta UNO de los dos argumentos, --ensayo (deshace todo) o "
          "--confirmo-paso19 (solo tras la frase de Gabi \"confirmo la migración paso19\").")
    # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/, dos niveles por debajo de la raíz.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# La raíz en la ruta de importación, para poder hacer "from app.schemas ...".
sys.path.insert(0, str(RAIZ_REPO))
# El archivo local con los precios y el umbral (ignorado por git).
RUTA_PRECIOS = RAIZ_REPO / "scripts" / "local" / "precios_pintura.json"

# La categoría nueva.
TIPO = "pintura"
# Las cuatro categorías de antes, en el orden de los CHECK actuales.
TIPOS_ANTES = ("bano", "cocina", "integral_vivienda", "parcial_acabados")
# Las cinco de después.
TIPOS_DESPUES = TIPOS_ANTES + (TIPO,)
# Los tres niveles de acabados (los del CHECK de tarifas_base.nivel_acabados).
NIVELES = ("basico", "medio", "alto")
# Los textos de incluye_tipico de D56 (decisión de Gabi, 23/11): una línea, sin cifras.
INCLUYE = {
    # basico: pintura plástica en blanco, sin reparaciones.
    "basico": ("Protección de suelos y muebles. Pintura plástica en paredes y techos de las estancias "
               "indicadas, en color blanco. No incluye reparar grietas, quitar gotelé ni alisar paredes."),
    # medio: además colores y pequeñas reparaciones.
    "medio": ("Protección de suelos y muebles. Pequeñas reparaciones de grietas y desconchones. Pintura "
              "plástica en paredes y techos, con colores a elegir. No incluye quitar gotelé ni alisar paredes."),
    # alto: además quitar el gotelé y alisar.
    "alto": ("Protección de suelos y muebles. Quitar el gotelé y alisar las paredes, con reparación de "
             "grietas. Pintura plástica en paredes y techos, con colores a elegir."),
}
# Descripción de la fila de umbrales_gate (sin la cifra).
DESCRIPCION_UMBRAL = "D56: umbral del Gate de pintura; la cifra sale del archivo local de paso19, no del repositorio."
# Fuente por defecto de las tres tarifas, si el archivo no trae una.
FUENTE_DEFECTO = "D56 (paso19, 23/11)"
# Los tres CHECK: (tabla, nombre, ¿admite NULL?).
CHECKS = (
    # oportunidades.tipo_reforma admite NULL (lead sin tipo todavía).
    ("oportunidades", "chk_oportunidades_tipo_reforma", True),
    # tarifas_base.tipo_reforma es NOT NULL.
    ("tarifas_base", "tarifas_base_tipo_reforma_check", False),
    # umbrales_gate.tipo_reforma es la clave primaria.
    ("umbrales_gate", "chk_umbrales_gate_tipo_reforma", False),
)


def parar(mensaje):
    """Para sin haber tocado nada, con código 1."""
    # El motivo, en una línea.
    print(f"PARADA (no se ha tocado nada): {mensaje}")
    # Código 1: no se ha podido hacer.
    sys.exit(1)


# Comprobación propia: ningún texto de INCLUYE lleva cifras (D39.9).
if any(re.search(r"\d", texto) for texto in INCLUYE.values()):
    # Sería un error de este mismo archivo.
    parar("algún texto de incluye_tipico de este script lleva cifras (D39.9)")

# FRENO 1: el código local tiene TipoReforma.PINTURA.
from app.schemas.common import TipoReforma  # noqa: E402  (después de sys.path)

# Los valores del Enum, como textos.
valores_enum = {t.value for t in TipoReforma}
# Si falta 'pintura', el merge del código no está hecho (o este no es el código de D56).
if TIPO not in valores_enum:
    # Se para antes de conectarse.
    parar("app/schemas/common.py no tiene TipoReforma.PINTURA: primero el código de D56, "
          "el merge y el reinicio del uvicorn del 8000")

# FRENO 2a: el archivo local existe.
if not RUTA_PRECIOS.exists():
    # Mensaje claro: qué falta y dónde.
    parar(f"no existe {RUTA_PRECIOS.relative_to(RAIZ_REPO).as_posix()} (precios y umbral de pintura; "
          "formato en el docstring de este script)")
# FRENO 2b: git lo ignora (si no, un "git add ." lo subiría al repositorio público).
ignorado = subprocess.run(["git", "check-ignore", "-q", str(RUTA_PRECIOS)], cwd=RAIZ_REPO).returncode == 0
# check-ignore -q devuelve 0 solo si el archivo está ignorado.
if not ignorado:
    # Se para: los precios no pueden acabar en el repositorio.
    parar("git NO ignora el archivo de precios: revisa .gitignore (scripts/local/)")


def cifra(valor, nombre):
    """Convierte una cifra del archivo en Decimal válido para NUMERIC(10,2), sin imprimirla."""
    # Se acepta texto o número (json con parse_float=Decimal ya da Decimal exacto).
    if not isinstance(valor, (str, int, Decimal)) or isinstance(valor, bool):
        # Tipo inesperado (lista, null, true...).
        parar(f"{nombre}: no es una cifra")
    # try / except: un texto que no es un número.
    try:
        # A Decimal, desde su texto (exacto).
        numero = Decimal(str(valor))
    # El texto no se puede leer como número.
    except InvalidOperation:
        # Se dice cuál, sin enseñar el valor.
        parar(f"{nombre}: no es una cifra válida")
    # Tiene que ser finito y mayor que cero.
    if not numero.is_finite() or numero <= 0:
        # Los CHECK de umbral (> 0) y el sentido común lo exigen.
        parar(f"{nombre}: tiene que ser mayor que cero")
    # Como mucho dos decimales (céntimos).
    if numero != numero.quantize(Decimal("0.01")):
        # NUMERIC(10,2) lo redondearía en silencio: mejor parar.
        parar(f"{nombre}: más de dos decimales")
    # Y que quepa en NUMERIC(10,2): menos de 100.000.000.
    if numero >= Decimal("100000000"):
        # Si no, Postgres daría un error al insertar.
        parar(f"{nombre}: no cabe en NUMERIC(10,2)")
    # La cifra, con dos decimales.
    return numero.quantize(Decimal("0.01"))


# FRENO 2c: el archivo es JSON válido; parse_float=Decimal evita pasar por float.
try:
    # Se lee en UTF-8.
    datos = json.loads(RUTA_PRECIOS.read_text(encoding="utf-8"), parse_float=Decimal)
# JSON mal escrito.
except json.JSONDecodeError as error:
    # Solo la posición del error, nunca el contenido.
    parar(f"el archivo de precios no es JSON válido (línea {error.lineno}, columna {error.colno})")
# Tiene que ser un objeto con precio_m2 (objeto) y umbral_gate.
if not isinstance(datos, dict) or not isinstance(datos.get("precio_m2"), dict) or "umbral_gate" not in datos:
    # Falta la forma esperada.
    parar("el archivo de precios necesita las claves precio_m2 (con basico, medio y alto) y umbral_gate")
# Los tres niveles, exactamente.
if set(datos["precio_m2"]) != set(NIVELES):
    # Se dicen las claves (no son cifras).
    parar(f"precio_m2 tiene que traer exactamente basico, medio y alto (trae {sorted(datos['precio_m2'])})")
# Las tres cifras, validadas.
PRECIOS = {nivel: cifra(datos["precio_m2"][nivel], f"precio_m2.{nivel}") for nivel in NIVELES}
# El umbral, validado.
UMBRAL = cifra(datos["umbral_gate"], "umbral_gate")
# La fuente: texto opcional; si no, la de por defecto.
FUENTE = datos.get("fuente") if isinstance(datos.get("fuente"), str) and datos.get("fuente").strip() else FUENTE_DEFECTO
# Constancia, sin cifras.
print("Archivo de precios: existe, está ignorado por git y sus 4 cifras son válidas (no se imprimen).")

# psycopg2: la conexión a PostgreSQL (se importa después de los frenos, que no la necesitan).
import psycopg2  # noqa: E402
# La clase base de las conexiones, para la del ensayo.
import psycopg2.extensions  # noqa: E402
# load_dotenv: carga el .env (DATABASE_URL) sin enseñar ningún valor.
from dotenv import load_dotenv  # noqa: E402

# Carga el .env de la raíz.
load_dotenv(RAIZ_REPO / ".env")


class CommitProhibido(Exception):
    """Un commit() en el ensayo: nunca debe pasar."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """La conexión del ensayo: su commit() lanza CommitProhibido en vez de confirmar."""

    def commit(self):
        # En vez de confirmar, el error.
        raise CommitProhibido("commit() prohibido en el ensayo de paso19")


# Una sola conexión; la del ensayo no puede confirmar.
cn = psycopg2.connect(os.getenv("DATABASE_URL"), connection_factory=ConexionSinCommit if ENSAYO else None)
# El cursor de esa conexión.
cur = cn.cursor()


def valores_check(tabla, nombre):
    """Los valores que admite hoy un CHECK (lista de textos), o None si no existe."""
    # SQL: la definición del CHECK por su tabla y su nombre.
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = %s::regclass AND conname = %s;", (tabla, nombre))
    # Una fila o ninguna.
    fila = cur.fetchone()
    # Sin fila, el CHECK no existe.
    if fila is None:
        return None
    # Cada valor va entre comillas simples en la definición ('bano'::character varying...).
    return re.findall(r"'([^']*)'", fila[0])


def foto():
    """Lo que interesa de las tres tablas, SIN cifras: CHECK, filas de pintura y huella de las ajenas."""
    # Los valores de cada CHECK.
    checks = {nombre: valores_check(tabla, nombre) for tabla, nombre, _ in CHECKS}
    # SQL: las filas de pintura de tarifas_base (nivel y texto; el precio no).
    cur.execute("SELECT nivel_acabados, incluye_tipico FROM tarifas_base WHERE tipo_reforma = %s "
                "ORDER BY nivel_acabados;", (TIPO,))
    # Nivel -> texto.
    tarifas_pintura = dict(cur.fetchall())
    # SQL: ¿existe la fila de pintura en umbrales_gate? (sin la cifra).
    cur.execute("SELECT COUNT(*) FROM umbrales_gate WHERE tipo_reforma = %s;", (TIPO,))
    # 0 o 1.
    umbral_pintura = cur.fetchone()[0]
    # SQL: TODAS las filas ajenas de tarifas_base, completas, para la huella.
    cur.execute("SELECT id, tipo_reforma, nivel_acabados, precio_m2::text, incluye_tipico, fuente, "
                "fecha_actualizacion::text FROM tarifas_base WHERE tipo_reforma <> %s ORDER BY id;", (TIPO,))
    # Lista de tuplas (con cifras: solo se usan para la huella).
    ajenas_tarifas = cur.fetchall()
    # SQL: TODAS las filas ajenas de umbrales_gate, completas, para la huella.
    cur.execute("SELECT tipo_reforma, umbral::text, provisional, descripcion, fecha_actualizacion::text "
                "FROM umbrales_gate WHERE tipo_reforma <> %s ORDER BY tipo_reforma;", (TIPO,))
    # Lista de tuplas.
    ajenas_umbrales = cur.fetchall()
    # La huella de todo lo ajeno: si cambia una sola cifra o texto, cambia la huella.
    huella = hashlib.sha256(repr((ajenas_tarifas, ajenas_umbrales)).encode("utf-8")).hexdigest()
    # Todo junto; las cifras ajenas solo viven dentro de la huella.
    return {"checks": checks, "tarifas_pintura": tarifas_pintura, "umbral_pintura": umbral_pintura,
            "n_tarifas_ajenas": len(ajenas_tarifas), "n_umbrales_ajenos": len(ajenas_umbrales),
            "huella_ajenas": huella}


def mostrar(f):
    """Imprime una foto (sin cifras)."""
    # Cada CHECK con la lista de valores que admite.
    for nombre, valores in f["checks"].items():
        # Una línea por CHECK.
        print(f"    {nombre}: {valores}")
    # Las filas de pintura de tarifas_base: solo el nivel.
    print(f"    tarifas_base de pintura: {sorted(f['tarifas_pintura']) or 'ninguna'}")
    # La fila de pintura de umbrales_gate: existe o no.
    print(f"    umbrales_gate de pintura: {'existe' if f['umbral_pintura'] else 'no existe'}")
    # Las ajenas: cuántas y su huella (12 y 4 antes de D56).
    print(f"    filas ajenas: {f['n_tarifas_ajenas']} en tarifas_base y {f['n_umbrales_ajenos']} en "
          f"umbrales_gate (huella {f['huella_ajenas'][:16]}...)")


# El modo, en la primera línea.
print(f"=== paso19 en modo {'ENSAYO (se deshace todo)' if ENSAYO else 'REAL (commit)'} ===")
# Foto ANTES de cambiar nada.
print("=== ANTES ===")
# Se guarda para la comparación final.
antes = foto()
# Y se enseña.
mostrar(antes)

# try / except: o todo o nada.
try:
    # SQL: si otro proceso (el uvicorn de n8n) tiene bloqueada una tabla, esperar 5 s como mucho y fallar.
    cur.execute("SET LOCAL lock_timeout = '5s';")
    # PASO 1: los tres CHECK.
    print("\n  PASO 1 - CHECK de tipo_reforma")
    # Cada CHECK, con su tabla y si admite NULL.
    for tabla, nombre, admite_null in CHECKS:
        # Lo que admite ahora.
        actuales = valores_check(tabla, nombre)
        # Ya admite pintura: no se toca (idempotente).
        if actuales is not None and TIPO in actuales:
            # Se comprueba además que admite justo las cinco.
            if sorted(actuales) != sorted(TIPOS_DESPUES):
                # Alguien lo cambió a mano: se para.
                raise RuntimeError(f"{nombre} admite {actuales}, no las cinco de D56")
            # Constancia.
            print(f"    {nombre}: ya admite 'pintura', no se toca")
            # Al siguiente.
            continue
        # Solo se cambia si hoy es EXACTAMENTE el de las cuatro categorías.
        if actuales is None or sorted(actuales) != sorted(TIPOS_ANTES):
            # Un CHECK que no es el esperado: no se reinterpreta, se para.
            raise RuntimeError(f"{nombre} no es el CHECK esperado de 4 categorías (admite {actuales})")
        # La lista de valores para el SQL (constantes de este archivo, no datos de fuera).
        lista = ", ".join(f"'{t}'" for t in TIPOS_DESPUES)
        # oportunidades sigue admitiendo NULL; las otras dos, no.
        condicion = f"tipo_reforma IN ({lista})" + (" OR tipo_reforma IS NULL" if admite_null else "")
        # SQL: quitar y volver a poner el CHECK con el mismo nombre, en UNA orden (sin hueco sin CHECK).
        cur.execute(f"ALTER TABLE {tabla} DROP CONSTRAINT {nombre}, ADD CONSTRAINT {nombre} CHECK ({condicion});")
        # Constancia.
        print(f"    {nombre}: ampliado a {list(TIPOS_DESPUES)}")
    # PASO 2: las tres tarifas.
    print("\n  PASO 2 - tarifas_base de pintura")
    # Un nivel cada vez.
    for nivel in NIVELES:
        # SQL: INSERT; si la pareja (tipo, nivel) ya existe (UNIQUE), no se inserta ni se sobrescribe.
        cur.execute("INSERT INTO tarifas_base (tipo_reforma, nivel_acabados, precio_m2, incluye_tipico, fuente) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tipo_reforma, nivel_acabados) DO NOTHING;",
                    (TIPO, nivel, PRECIOS[nivel], INCLUYE[nivel], FUENTE))
        # rowcount: 1 = insertada.
        if cur.rowcount == 1:
            # Constancia, sin cifra.
            print(f"    pintura/{nivel}: insertada")
            # Al siguiente.
            continue
        # Ya existía: se compara con el archivo y con el texto de D56, sin imprimir la cifra.
        cur.execute("SELECT precio_m2, incluye_tipico FROM tarifas_base WHERE tipo_reforma = %s AND "
                    "nivel_acabados = %s;", (TIPO, nivel))
        # La fila que ya estaba.
        precio_existente, texto_existente = cur.fetchone()
        # ¿Coincide todo?
        igual = precio_existente == PRECIOS[nivel] and texto_existente == INCLUYE[nivel]
        # Constancia: no se toca en ningún caso.
        print(f"    pintura/{nivel}: ya existía, no se toca ({'igual que el archivo y D56' if igual else 'DISTINTA del archivo o de D56'})")
    # PASO 3: el umbral.
    print("\n  PASO 3 - umbrales_gate de pintura")
    # SQL: INSERT; si la fila ya existe (clave primaria), no se inserta ni se sobrescribe.
    cur.execute("INSERT INTO umbrales_gate (tipo_reforma, umbral, provisional, descripcion) "
                "VALUES (%s, %s, false, %s) ON CONFLICT (tipo_reforma) DO NOTHING;",
                (TIPO, UMBRAL, DESCRIPCION_UMBRAL))
    # 1 = insertada.
    if cur.rowcount == 1:
        # Constancia, sin cifra.
        print("    pintura: insertada (provisional = false)")
    # 0 = ya existía.
    else:
        # SQL: la cifra que ya estaba, para compararla (no se imprime).
        cur.execute("SELECT umbral FROM umbrales_gate WHERE tipo_reforma = %s;", (TIPO,))
        # ¿Igual que el archivo?
        igual = cur.fetchone()[0] == UMBRAL
        # Constancia.
        print(f"    pintura: ya existía, no se toca ({'igual que el archivo' if igual else 'DISTINTA del archivo'})")
    # La foto DENTRO de la transacción.
    print("\n=== DESPUÉS (dentro de la transacción, sin confirmar) ===")
    # Se guarda.
    despues = foto()
    # Se enseña.
    mostrar(despues)
    # Lo ajeno no puede haber cambiado.
    if despues["huella_ajenas"] != antes["huella_ajenas"]:
        # Sería un fallo grave de este script: se deshace todo.
        raise RuntimeError("han cambiado filas que no son de pintura")
    # Ensayo: se deshace.
    if ENSAYO:
        # ROLLBACK de todo.
        cn.rollback()
        # Constancia.
        print("\n  ENSAYO: rollback() ejecutado, no se ha cambiado nada")
    # Real: un solo commit.
    else:
        # Los tres pasos se hacen definitivos a la vez.
        cn.commit()
        # Constancia.
        print("\n  commit() ejecutado")
# Cualquier error: deshacer todo y volver a lanzarlo para verlo.
except Exception:
    # Nada cambiado.
    cn.rollback()
    # Constancia.
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    # El error original, entero.
    raise

# La foto final, fuera de la transacción.
print("\n=== FINAL (fuera de la transacción) ===")
# Se guarda.
final = foto()
# Se enseña.
mostrar(final)
# En el ensayo, todo tiene que estar como antes.
if ENSAYO and final != antes:
    # FALLO explícito.
    print("\n  FALLO: el ensayo ha dejado cambios")
    # Se cierra la conexión.
    cn.close()
    # Código 1.
    sys.exit(1)
# Se cierra la transacción de lectura (no hay nada que confirmar).
cn.rollback()
# El cursor.
cur.close()
# Y la conexión.
cn.close()
