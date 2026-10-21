"""
Verificación de SOLO LECTURA del cambio de datos D39.9 (los 12 textos de
tarifas_base.incluye_tipico; script del cambio:
scripts/datos_d39_9_incluye_tipico.py).

Uso:  check_datos_d39_9_incluye_tipico.py --esperado anterior|nuevo

  CASO 1. El JSON (docs/datos/D39_9_incluye_tipico.json): los textos que
          este script tiene EN MEMORIA, vueltos a escribir en el formato del
          archivo, dan el SHA-256 aprobado (d9d734a0...2d37c2); 12 parejas
          distintas; ningún texto nuevo con dígitos ni con < > & " '.
  CASO 2. Las 12 filas reales: cada una dice ANTERIOR, NUEVO u OTRO, y
          todas tienen que tener el texto --esperado. Ni una fila de más.
  CASO 3. precio_m2, fuente y fecha_actualizacion iguales a los del
          volcado docs/datos/D39_9_tarifas_antes.json. Ese volcado lo
          escribe ESTE script la primera vez que se ejecuta con --esperado
          anterior (antes del UPDATE) y después solo lo lee. El precio va
          como huella SHA-256 de "id|precio_m2" (el repositorio es público;
          la huella basta para comparar, aunque NO es un secreto: un precio
          con dos decimales se adivina probando). Con --esperado nuevo, sin
          volcado es FALLO.
  CASO 4. Las filas de logs del cambio (entity_type 'tarifa_base', accion
          'incluye_tipico_cambiado'): cada una apunta a una de las 12 filas,
          con decision D39.9 y el SHA-256 aprobado; la historia de cada fila
          alterna aplicar / deshacer empezando por aplicar, con sus textos
          anterior y nuevo; con --esperado nuevo, la última es "aplicar"
          (tras el primer --confirmo-d39-9, exactamente 1 por fila: 12);
          con --esperado anterior, un número PAR por fila (antes de
          aplicar, 0: así se ve que el ENSAYO no deja ninguna).

Una sola transacción REPEATABLE READ, READ ONLY (la primera orden): las
filas y los logs se leen de la misma foto, y cualquier escritura en la
base de datos daría error. El único archivo que escribe es el volcado.

Códigos de salida: 0 todo correcto; 1 algún FALLO; 2 argumentos no válidos.
"""

# hashlib: SHA-256 del JSON y huellas del precio.
import hashlib
# json: leer el JSON y el volcado, y escribir el volcado.
import json
# os: leer DATABASE_URL del entorno.
import os
# re: buscar dígitos en los textos nuevos.
import re
# sys: argumentos, salida en UTF-8 y código de salida.
import sys
# datetime: la fecha y hora del volcado.
from datetime import datetime
# Path: rutas a partir de la ubicación de este archivo.
from pathlib import Path

# psycopg2: la conexión a PostgreSQL.
import psycopg2
# load_dotenv: carga el .env en el entorno (sin enseñar ningún valor).
from dotenv import load_dotenv

# Salida en UTF-8: si no, en Windows las tildes salen rotas en una tubería.
sys.stdout.reconfigure(encoding="utf-8")

# La raíz del repositorio: este archivo está en scripts/.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Se carga el .env de la raíz.
load_dotenv(RAIZ_REPO / ".env")
# La URL de la base de datos (nunca se imprime).
DATABASE_URL = os.environ.get("DATABASE_URL")
# Los textos aprobados y el volcado de antes del cambio.
RUTA_JSON = RAIZ_REPO / "docs" / "datos" / "D39_9_incluye_tipico.json"
RUTA_VOLCADO = RAIZ_REPO / "docs" / "datos" / "D39_9_tarifas_antes.json"

# El SHA-256 del JSON aprobado por el tutor (2026-10-21).
SHA_APROBADO = "d9d734a01b348aaa5610ae7f57a6c5bb476e3330886da27156b2607ebb2d37c2"
# Lo que escribe el script del cambio en logs (opción A).
ENTITY_TYPE = "tarifa_base"
ACCION = "incluye_tipico_cambiado"
DECISION = "D39.9"
# Caracteres que no pueden ir en un texto nuevo.
PROHIBIDOS = set("<>&\"'")
# 4 tipos x 3 niveles.
NUM_FILAS = 12

# Contadores de comprobaciones.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Imprime [OK] o [FALLO] y lo cuenta."""
    # global: se cambian los contadores de fuera de la función.
    global ok
    # Si la condición se cumple, cuenta como correcta.
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Si no, se anota el fallo para el resumen final.
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


def leer_esperado(argumentos):
    """'anterior' o 'nuevo'; None si los argumentos no valen."""
    # Tienen que ser exactamente dos: --esperado y el valor.
    if len(argumentos) == 2 and argumentos[0] == "--esperado" and argumentos[1] in ("anterior", "nuevo"):
        return argumentos[1]
    # Cualquier otra cosa no vale.
    return None


def huella_precio(id_fila, precio_texto):
    """SHA-256 de 'id|precio_m2': sirve para comparar sin escribir el precio."""
    # El id va delante para que dos filas con el mismo precio den huellas distintas.
    return hashlib.sha256(f"{id_fila}|{precio_texto}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# Argumentos.
esperado = leer_esperado(sys.argv[1:])
# Sin un --esperado válido no se hace nada.
if esperado is None:
    print(f"Argumentos no válidos: {sys.argv[1:]}. Uso: --esperado anterior|nuevo")
    sys.exit(2)
# La clave del JSON con el texto que deberían tener las filas.
CLAVE_ESPERADA = f"incluye_tipico_{esperado}"
print(f"Esperado: textos {esperado.upper()}")

# ------------------------------------------------------------------
print("\nCASO 1 - El JSON")
# Se lee el archivo.
entradas = json.loads(RUTA_JSON.read_text(encoding="utf-8"))
# Los textos EN MEMORIA, escritos otra vez en el formato del archivo
# (sangría de 2 y salto de línea final): si alguien los cambiara después
# de leerlos, la huella dejaría de coincidir.
sha_memoria = hashlib.sha256(
    (json.dumps(entradas, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).hexdigest()
comprobar("los textos en memoria dan el SHA-256 aprobado", sha_memoria == SHA_APROBADO, f"({sha_memoria})")
# Las parejas del JSON.
parejas = [(e["tipo_reforma"], e["nivel_acabados"]) for e in entradas]
comprobar(f"{NUM_FILAS} parejas distintas", len(set(parejas)) == NUM_FILAS == len(parejas), f"({len(parejas)})")
# Textos nuevos con algún dígito.
con_digitos = [p for p, e in zip(parejas, entradas) if re.search(r"\d", e["incluye_tipico_nuevo"])]
comprobar("ningún texto nuevo con dígitos", not con_digitos, f"({con_digitos})")
# Textos nuevos con algún carácter prohibido.
con_prohibidos = [p for p, e in zip(parejas, entradas) if PROHIBIDOS & set(e["incluye_tipico_nuevo"])]
comprobar("ningún texto nuevo con < > & \" '", not con_prohibidos, f"({con_prohibidos})")
# Diccionario pareja -> entrada, para los casos siguientes.
por_pareja = dict(zip(parejas, entradas))

# ------------------------------------------------------------------
# Lectura de la base de datos: una sola transacción de solo lectura.
cn = psycopg2.connect(DATABASE_URL)
# try / finally: la conexión se cierra siempre.
try:
    # El cursor de las lecturas.
    cur = cn.cursor()
    # SQL: la primera orden fija la transacción: misma foto para todo y
    # ninguna escritura posible.
    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;")
    # SQL: las filas de tarifas_base; el precio como texto, exacto.
    cur.execute("SELECT id, tipo_reforma, nivel_acabados, incluye_tipico, precio_m2::text, fuente, "
                "fecha_actualizacion FROM tarifas_base ORDER BY tipo_reforma, nivel_acabados;")
    # Lista de tuplas.
    filas = cur.fetchall()
    # SQL: las filas de logs del cambio, en orden de escritura.
    cur.execute("SELECT id, entity_id, detalle ->> 'decision', detalle ->> 'sentido', "
                "detalle ->> 'anterior', detalle ->> 'nuevo', detalle ->> 'sha256_json' "
                "FROM logs WHERE entity_type = %s AND accion = %s ORDER BY id;",
                (ENTITY_TYPE, ACCION))
    # Lista de tuplas.
    logs = cur.fetchall()
# Solo lectura: no hay nada que confirmar.
finally:
    cn.rollback()
    cn.close()

# ------------------------------------------------------------------
print(f"\nCASO 2 - Las {len(filas)} filas de tarifas_base")
# Las parejas reales.
parejas_reales = {(t, n) for _, t, n, *_ in filas}
comprobar(f"las parejas reales son exactamente las {NUM_FILAS} del JSON",
          parejas_reales == set(parejas), f"(reales {len(parejas_reales)})")
# Cada fila, clasificada.
for _, tipo, nivel, texto, *_ in filas:
    # La entrada del JSON de esa pareja (puede no existir).
    entrada = por_pareja.get((tipo, nivel))
    # ANTERIOR, NUEVO u OTRO.
    if entrada and texto == entrada["incluye_tipico_anterior"]:
        clase = "ANTERIOR"
    # El texto nuevo, igual carácter a carácter.
    elif entrada and texto == entrada["incluye_tipico_nuevo"]:
        clase = "NUEVO"
    # Cualquier otro texto (o una pareja que no está en el JSON).
    else:
        clase = "OTRO"
    # Tiene que ser la esperada.
    comprobar(f"{tipo}/{nivel}: {clase}", clase == esperado.upper())

# ------------------------------------------------------------------
print("\nCASO 3 - precio_m2, fuente y fecha_actualizacion sin cambios")
# Lo de ahora, en el formato del volcado.
ahora = {f"{t}/{n}": {"id": i, "huella_precio": huella_precio(i, p), "fuente": f,
                      "fecha_actualizacion": d.isoformat()}
         for i, t, n, _, p, f, d in filas}
# Con --esperado anterior y sin volcado: se escribe (es el "antes").
if esperado == "anterior" and not RUTA_VOLCADO.exists():
    # Se anota cuándo se tomó la foto.
    volcado = {"tomado": datetime.now().astimezone().isoformat(timespec="seconds"), "filas": ahora}
    # Se escribe en UTF-8 con salto de línea final.
    RUTA_VOLCADO.write_text(json.dumps(volcado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"    [INFO]  volcado escrito: {RUTA_VOLCADO.relative_to(RAIZ_REPO).as_posix()} "
          f"({len(ahora)} filas, tomado {volcado['tomado']})")
# Sin volcado no hay con qué comparar (solo puede pasar con --esperado nuevo).
if not RUTA_VOLCADO.exists():
    comprobar("existe el volcado de antes del cambio", False, f"({RUTA_VOLCADO.name})")
else:
    # Se lee.
    volcado = json.loads(RUTA_VOLCADO.read_text(encoding="utf-8"))
    print(f"    [INFO]  volcado tomado {volcado['tomado']}")
    # Las mismas filas.
    comprobar("las mismas filas que en el volcado", set(volcado["filas"]) == set(ahora))
    # Fila a fila: id, huella del precio, fuente y fecha.
    for clave, antes in volcado["filas"].items():
        # Lo de ahora de esa fila (puede faltar).
        actual = ahora.get(clave)
        # Las cuatro cosas, por separado, para saber cuál cambió.
        distintas = [k for k in ("id", "huella_precio", "fuente", "fecha_actualizacion")
                     if actual is None or actual[k] != antes[k]]
        comprobar(f"{clave}: id, precio, fuente y fecha iguales", not distintas, f"({distintas})" if distintas else "")

# ------------------------------------------------------------------
print(f"\nCASO 4 - Logs del cambio ({len(logs)} filas)")
# id real -> pareja, para saber a qué fila apunta cada log.
pareja_por_id = {i: (t, n) for i, t, n, *_ in filas}
# Logs que no apuntan a ninguna de las 12 filas.
huerfanos = [i for i, eid, *_ in logs if eid not in pareja_por_id]
comprobar("todos apuntan a una fila de tarifas_base", not huerfanos, f"(logs {huerfanos})")
# Logs con otra decisión o con otro JSON.
ajenos = [i for i, _, dec, _, _, _, sha in logs if dec != DECISION or sha != SHA_APROBADO]
comprobar("todos con decision D39.9 y el SHA-256 aprobado", not ajenos, f"(logs {ajenos})")
# Cuántos de cada sentido (solo informativo).
print(f"    [INFO]  aplicar: {sum(1 for l in logs if l[3] == 'aplicar')}; "
      f"deshacer: {sum(1 for l in logs if l[3] == 'deshacer')}")
# La historia de cada fila.
for i_fila, (tipo, nivel) in pareja_por_id.items():
    # Sus logs, en orden.
    suyos = [l for l in logs if l[1] == i_fila]
    # La entrada del JSON (si la pareja no está, ya falló el CASO 2).
    entrada = por_pareja.get((tipo, nivel))
    if entrada is None:
        continue
    # Los textos de cada sentido.
    a, n = entrada["incluye_tipico_anterior"], entrada["incluye_tipico_nuevo"]
    # Lo que tendría que tener el log número k: par aplicar, impar deshacer.
    historia_bien = all(
        (s, ant, nue) == (("aplicar", a, n) if k % 2 == 0 else ("deshacer", n, a))
        for k, (_, _, _, s, ant, nue, _) in enumerate(suyos))
    # Nuevo: número impar (el último, aplicar). Anterior: número par.
    cuenta_bien = len(suyos) % 2 == (1 if esperado == "nuevo" else 0)
    comprobar(f"{tipo}/{nivel}: {len(suyos)} log(s), alternos y con sus textos",
              historia_bien and cuenta_bien)

# ------------------------------------------------------------------
print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
# Se repiten los fallos al final.
for fallo in fallos:
    print("  - " + fallo)
print("CORRECTO" if not fallos else "HAY FALLOS")
# Código 1 si algo falló.
sys.exit(1 if fallos else 0)
