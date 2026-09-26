"""
Verificacion real del manejador de excepciones global de app/main.py.

Se arranca el servidor de verdad (uvicorn.Server, sin --reload) y se
provocan errores no controlados por HTTP, comprobando despues en la
tabla logs que quedo el rastro.

Cinco escenarios:
  A) Un endpoint temporal que divide por cero: error de programacion
     puro, sin base de datos de por medio.
  B) El endpoint REAL POST /leads, sustituyendo create_lead por una
     version que revienta. Simula un bug en services/. La tecnica es el
     monkey patching que ya se uso en check_graceful_shutdown.py.
  C) El manejador NO rompe la validacion: un 422 sigue siendo 422, no
     500, y no ensucia la tabla logs.
  D) El resto de la aplicacion sigue funcionando (/health, /health/db y
     un POST /leads correcto).
  E) El PEOR caso: falla tambien el registro en logs porque la base de
     datos esta caida. Se comprueba que el cliente recibe igualmente su
     500 y que el error original aparece en stderr, que es el unico
     sitio donde puede quedar rastro cuando la base de datos no
     responde.

Uso:
    .\\venv\\Scripts\\python.exe scripts\\check_exception_handler.py
"""

import io
import sys
from pathlib import Path
import threading
import time

# Este script vive en scripts/, pero el paquete "app" esta en la raiz del
# repositorio. Al ejecutar "python scripts/<archivo>.py", Python solo anade
# la carpeta del archivo (scripts/) a sys.path, asi que "import app.algo"
# fallaria con ModuleNotFoundError. Se calcula la raiz a partir de la
# ubicacion de este mismo archivo, en vez de escribir una ruta absoluta,
# para que funcione en cualquier maquina y desde cualquier directorio.
# Misma tecnica que scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn
from psycopg2.extras import RealDictCursor

import app.api.leads as leads_api
import app.main as app_main
from app.config import DATABASE_URL, WEBHOOK_SECRET

# CORRECCIÓN 2026-09-20. Este script se escribió ANTES de que POST /leads
# exigiera autenticación. Cuando la Fase 1 añadió la cabecera
# X-Webhook-Secret, las tres llamadas a /leads de este archivo empezaron a
# recibir 401 en vez de llegar al manejador de excepciones que pretenden
# probar, y el script pasó a dar 22/28. No era un fallo del manejador: era
# un contrato de autenticación añadido después de escribir la prueba.
#
# Se envía el secreto en todas las peticiones a /leads, con el mismo
# patrón ya probado en check_webhook_auth_http.py: leerlo del .env (a
# través de app.config) en vez de escribirlo en el código, para que
# script y servidor usen siempre el mismo valor.
#
# Qué exige cada ruta, comprobado en el código y no supuesto:
#   POST /leads              -> X-Webhook-Secret (dependencia
#                               verificar_webhook_secret en api/leads.py)
#   POST /calculate-estimate -> X-Webhook-Secret (misma dependencia en
#                               api/estimates.py). Este script no la usa.
#   GET /health, /health/db  -> abiertos por diseño, sin dependencias.
#   /mcp                     -> Bearer MCP_SECRET, un secreto DISTINTO,
#                               verificado por VerificadorSecretoMCP. No
#                               interviene aquí: este script no toca MCP.
#   /__prueba_boom           -> ruta temporal que añade este script, sin
#                               autenticación, para provocar el error.
CABECERAS_AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}

PUERTO = 8011
BASE = f"http://127.0.0.1:{PUERTO}"

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


# ---- marcas propias: que este script solo lea y borre SUS logs --------
# CORRECCIÓN 2026-09-26. Antes el script leía y borraba TODAS las filas
# con accion = 'error_no_controlado'. Esa acción la escribe el manejador
# global de app/main.py para CUALQUIER 500 real, también los del uvicorn
# que sirve a n8n, así que cada ejecución borraba errores reales de
# producción (pg_stat_statements: 75 filas en 39 llamadas).
#
# Ahora cada log propio se reconoce por una de dos marcas que NINGÚN log
# real puede tener:
#   - RUTA_PROPIA: la ruta temporal que este script añade a SU app, dentro
#     de SU proceso. En cualquier otro servidor no existe: una petición a
#     ella da 404, y un 404 no pasa por el manejador ni se registra.
#   - MENSAJE_PROPIO: el texto del error que lanza la versión rota de
#     create_lead que este script inyecta (prueba B).
RUTA_PROPIA = "/__prueba_boom"
MENSAJE_PROPIO = "fallo simulado dentro de services/"

# Condición SQL de "log de este script". Los %(nombre)s son parámetros con
# nombre: psycopg2 los rellena desde un diccionario, de forma segura.
FILTRO_PROPIO = """
    accion = 'error_no_controlado' AND entity_type = 'sistema'
    AND (detalle->>'ruta' = %(ruta)s OR detalle->>'mensaje' = %(mensaje)s)
"""
PARAMS_PROPIOS = {"ruta": RUTA_PROPIA, "mensaje": MENSAJE_PROPIO}


# ---- endpoint temporal que revienta (solo existe en esta prueba) -----
def endpoint_que_revienta():
    return 1 / 0


app_main.app.add_api_route(RUTA_PROPIA, endpoint_que_revienta, methods=["GET"])

# ---- conexion de observacion, ajena al pool del servidor -------------
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor(cursor_factory=RealDictCursor)


def logs_de_error():
    """
    Logs de error de ESTA ejecución: posteriores a id_base y con una marca
    propia. Un error real que llegue durante la prueba (por ejemplo, del
    uvicorn de n8n) no cambia los recuentos del script.
    """
    cur.execute(
        f"""
        SELECT id, entity_type, entity_id, accion, detalle, created_at
        FROM logs WHERE id > %(base)s AND {FILTRO_PROPIO} ORDER BY id;
        """,
        {**PARAMS_PROPIOS, "base": id_base},
    )
    filas = cur.fetchall()
    cn.commit()
    return filas


def limpiar_logs():
    """
    Borra SOLO los logs con marca propia, de esta ejecución o de una
    anterior que se cortara antes de limpiar. Sin filtro de id a
    propósito: la marca basta para no tocar nunca un log real.
    """
    cur.execute(f"DELETE FROM logs WHERE {FILTRO_PROPIO};", PARAMS_PROPIOS)
    cn.commit()


limpiar_logs()
# Id más alto de logs en este momento. Todo lo que el script escriba a
# partir de aquí tendrá un id mayor (id es SERIAL, siempre creciente).
# COALESCE(..., 0): si la tabla estuviera vacía, max() devolvería NULL.
cur.execute("SELECT COALESCE(max(id), 0) AS m FROM logs;")
id_base = cur.fetchone()["m"]
cn.commit()
print(f"Logs de error propios al empezar: {len(logs_de_error())} (id_base = {id_base})\n")

# ---- arranque --------------------------------------------------------
config = uvicorn.Config(
    "app.main:app", host="127.0.0.1", port=PUERTO, log_level="critical"
)
servidor = uvicorn.Server(config)
hilo = threading.Thread(target=servidor.run, daemon=True)

print("=" * 78)
print("ARRANQUE DEL SERVIDOR (sin --reload)")
print("=" * 78)
hilo.start()
inicio = time.time()
while time.time() - inicio < 30:
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    except requests.exceptions.RequestException:
        time.sleep(0.25)
else:
    print("  El servidor no respondio"); sys.exit(1)
print(f"  Arriba en {time.time() - inicio:.2f}s")

try:
    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA A - Endpoint temporal que lanza ZeroDivisionError")
    print("=" * 78)
    antes = len(logs_de_error())
    r = requests.get(f"{BASE}/__prueba_boom", timeout=20)
    print(f"  GET /__prueba_boom -> HTTP {r.status_code}")
    print(f"  Cuerpo: {r.text}")

    comprobar("Codigo HTTP 500", r.status_code == 500, f"({r.status_code})")
    comprobar("Cuerpo exacto {'status':'error','detail':'Error interno'}",
              r.json() == {"status": "error", "detail": "Error interno"})
    comprobar("El cuerpo NO contiene traceback ni rutas internas",
              "Traceback" not in r.text and "app\\" not in r.text
              and "ZeroDivisionError" not in r.text)

    time.sleep(0.5)  # margen para que termine el INSERT del manejador
    filas = logs_de_error()
    comprobar("Se escribio 1 fila nueva en logs", len(filas) == antes + 1,
              f"({antes} -> {len(filas)})")
    if filas:
        f = filas[-1]
        print("\n  Fila real escrita en logs:")
        for k, v in f.items():
            print(f"    {k:<14} = {v!r}")
        comprobar("entity_type = 'sistema'", f["entity_type"] == "sistema")
        comprobar("entity_id = 0 (centinela)", f["entity_id"] == 0)
        comprobar("accion = 'error_no_controlado'",
                  f["accion"] == "error_no_controlado")
        comprobar("detalle.tipo = 'ZeroDivisionError'",
                  f["detalle"]["tipo"] == "ZeroDivisionError")
        comprobar("detalle.mensaje es el mensaje REAL de la excepcion",
                  f["detalle"]["mensaje"] == "division by zero",
                  f"('{f['detalle']['mensaje']}')")
        comprobar("detalle.ruta = '/__prueba_boom'",
                  f["detalle"]["ruta"] == "/__prueba_boom")

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA B - Fallo dentro del endpoint REAL POST /leads")
    print("=" * 78)
    print("  Se sustituye create_lead por una version que lanza RuntimeError")
    print("  (monkey patching, misma tecnica que check_graceful_shutdown.py)")

    original = leads_api.create_lead

    def create_lead_roto(data):
        # MENSAJE_PROPIO: es la marca con la que el script reconoce este log.
        raise RuntimeError(MENSAJE_PROPIO)

    leads_api.create_lead = create_lead_roto

    antes_b = len(logs_de_error())
    cuerpo = {
        "nombre": "Prueba Handler",
        "email": "handler@example.com",
        "telefono": "600000000",
        "tipo_reforma": "bano",
        "m2": 10.0,
        "nivel_acabados": "medio",
        "incluye_cambios_estructurales": False,
        "fotos": [],
        "lead_token": "tokHANDLER",
    }
    rb = requests.post(f"{BASE}/leads", json=cuerpo, headers=CABECERAS_AUTH, timeout=20)
    print(f"\n  POST /leads -> HTTP {rb.status_code}")
    print(f"  Cuerpo: {rb.text}")
    comprobar("Codigo HTTP 500", rb.status_code == 500, f"({rb.status_code})")
    comprobar("Cuerpo generico, sin el mensaje interno",
              rb.json() == {"status": "error", "detail": "Error interno"}
              and "fallo simulado" not in rb.text)

    time.sleep(0.5)
    filas_b = logs_de_error()
    comprobar("Se escribio otra fila en logs", len(filas_b) == antes_b + 1,
              f"({antes_b} -> {len(filas_b)})")
    if len(filas_b) > antes_b:
        fb = filas_b[-1]
        print("\n  Fila real escrita en logs:")
        print(f"    detalle = {fb['detalle']!r}")
        comprobar("detalle.tipo = 'RuntimeError'", fb["detalle"]["tipo"] == "RuntimeError")
        comprobar("detalle.mensaje conserva el texto real",
                  fb["detalle"]["mensaje"] == "fallo simulado dentro de services/")
        comprobar("detalle.ruta = '/leads'", fb["detalle"]["ruta"] == "/leads")
        comprobar("detalle.metodo = 'POST'", fb["detalle"]["metodo"] == "POST")

    cur.execute("SELECT COUNT(*) AS n FROM clientes WHERE email = %s;",
                ("handler@example.com",))
    n_cli = cur.fetchone()["n"]
    cn.commit()
    comprobar("El fallo NO dejo ningun cliente a medias", n_cli == 0, f"(n={n_cli})")

    leads_api.create_lead = original
    print("\n  create_lead restaurado a su version real")

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA C - El manejador NO se come los errores de validacion")
    print("=" * 78)
    antes_c = len(logs_de_error())
    malo = dict(cuerpo)
    malo["email"] = "no-es-un-email"
    rc = requests.post(f"{BASE}/leads", json=malo, headers=CABECERAS_AUTH, timeout=20)
    print(f"  POST /leads con email invalido -> HTTP {rc.status_code}")
    comprobar("Sigue siendo 422, no 500", rc.status_code == 422, f"({rc.status_code})")
    comprobar("El detalle de validacion sigue llegando al cliente",
              "email" in rc.text)
    time.sleep(0.3)
    comprobar("Un 422 NO ensucia la tabla logs",
              len(logs_de_error()) == antes_c,
              f"({antes_c} -> {len(logs_de_error())})")

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA D - El resto de la aplicacion sigue funcionando")
    print("=" * 78)
    rh = requests.get(f"{BASE}/health", timeout=10)
    rd = requests.get(f"{BASE}/health/db", timeout=10)
    print(f"  GET /health    -> {rh.status_code} {rh.json()}")
    print(f"  GET /health/db -> {rd.status_code} {rd.json()}")
    comprobar("/health sigue en 200", rh.status_code == 200)
    comprobar("/health/db sigue en 200", rd.status_code == 200)

    rp = requests.post(f"{BASE}/leads", json=cuerpo, headers=CABECERAS_AUTH, timeout=20)
    print(f"  POST /leads real -> {rp.status_code} {rp.text}")
    comprobar("POST /leads vuelve a funcionar (201)", rp.status_code == 201,
              f"({rp.status_code})")

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA E - El PEOR caso: falla tambien el registro en logs")
    print("=" * 78)
    print("  Se simula que la base de datos esta caida sustituyendo")
    print("  get_transactional_connection por una version que revienta.")
    print("  Sin el fallback a stderr, el error original desapareceria")
    print("  sin dejar rastro en NINGUN sitio.")

    original_conn = app_main.get_transactional_connection

    def conexion_rota():
        raise psycopg2.OperationalError("conexion con la base de datos perdida")

    app_main.get_transactional_connection = conexion_rota

    antes_e = len(logs_de_error())

    # Se desvia stderr a un buffer en memoria para poder leer lo que el
    # manejador escribe. El print del manejador resuelve sys.stderr en el
    # momento de ejecutarse, asi que sustituirlo aqui lo captura, aunque
    # el print ocurra en el hilo del servidor.
    buffer_stderr = io.StringIO()
    stderr_real = sys.stderr
    sys.stderr = buffer_stderr
    try:
        re_ = requests.get(f"{BASE}/__prueba_boom", timeout=20)
        time.sleep(0.6)  # margen para que el hilo del servidor imprima
    finally:
        sys.stderr = stderr_real

    capturado = buffer_stderr.getvalue()

    print(f"\n  GET /__prueba_boom -> HTTP {re_.status_code}")
    print(f"  Cuerpo: {re_.text}")
    print("\n  --- CONTENIDO REAL CAPTURADO DE stderr ---")
    for linea in capturado.rstrip("\n").split("\n"):
        print(f"  | {linea}")
    print("  --- FIN DE stderr ---")

    comprobar("El cliente SIGUE recibiendo su 500 aunque falle el registro",
              re_.status_code == 500, f"({re_.status_code})")
    comprobar("El cuerpo sigue siendo generico",
              re_.json() == {"status": "error", "detail": "Error interno"})
    comprobar("NO se escribio nada en logs (la base de datos 'esta caida')",
              len(logs_de_error()) == antes_e,
              f"({antes_e} -> {len(logs_de_error())})")
    comprobar("stderr recibio el aviso de que no se pudo registrar",
              "No se pudo registrar en la tabla logs" in capturado)
    comprobar("stderr conserva el ERROR ORIGINAL (ZeroDivisionError)",
              "ZeroDivisionError" in capturado and "division by zero" in capturado)
    comprobar("stderr dice que peticion lo provoco",
              "GET /__prueba_boom" in capturado)
    comprobar("stderr explica por que fallo el registro",
              "OperationalError" in capturado
              and "conexion con la base de datos perdida" in capturado)

    app_main.get_transactional_connection = original_conn
    print("\n  get_transactional_connection restaurada")

    rf = requests.get(f"{BASE}/__prueba_boom", timeout=20)
    time.sleep(0.5)
    comprobar("Restaurada la base de datos, vuelve a registrarse en logs",
              len(logs_de_error()) == antes_e + 1,
              f"({antes_e} -> {len(logs_de_error())})")

finally:
    print("\n" + "=" * 78)
    print("APAGADO Y LIMPIEZA")
    print("=" * 78)
    t0 = time.time()
    servidor.should_exit = True
    hilo.join(timeout=20)
    print(f"  Apagado en {time.time() - t0:.3f}s   hilo vivo: {hilo.is_alive()}")

    print("\n  Filas de error dejadas en logs antes de limpiar:")
    for f in logs_de_error():
        print(f"    id={f['id']}  {f['detalle']['tipo']}: {f['detalle']['mensaje']}")

    limpiar_logs()
    cur.execute("""DELETE FROM oportunidades WHERE lead_id IN (
                     SELECT l.id FROM leads l JOIN clientes c ON c.id=l.cliente_id
                     WHERE c.email='handler@example.com');""")
    cur.execute("""DELETE FROM leads WHERE cliente_id IN
                   (SELECT id FROM clientes WHERE email='handler@example.com');""")
    cur.execute("DELETE FROM clientes WHERE email='handler@example.com';")
    cn.commit()
    cur.execute("SELECT (SELECT COUNT(*) FROM logs) l, (SELECT COUNT(*) FROM clientes) c,"
                " (SELECT COUNT(*) FROM leads) le, (SELECT COUNT(*) FROM oportunidades) o;")
    print(f"  Estado final de las tablas: {dict(cur.fetchone())}")
    cn.commit()
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for x in fallos:
        print("  - " + x)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
