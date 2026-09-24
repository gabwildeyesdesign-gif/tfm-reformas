"""
PASO 3 - Verificacion por HTTP REAL de POST /leads.

No llama a la funcion de Python: levanta el servidor y le manda
peticiones HTTP de verdad con requests, para que pasen por todo el
camino real (enrutado de FastAPI, validacion de Pydantic, serializacion
de la respuesta).

El servidor se arranca desde Python con uvicorn.Server, NO con
"uvicorn --reload": en Windows el reloader cuelga el apagado cuando el
proceso no tiene una consola interactiva (limitacion ya documentada en
el proyecto). Aqui es un unico proceso, sin reloader.
"""

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

from app.config import DATABASE_URL, WEBHOOK_SECRET

# Desde la Fase 1 (autenticacion del webhook), POST /leads exige la
# cabecera X-Webhook-Secret; sin ella responde 401 antes de validar el
# cuerpo. Este script prueba la logica del alta, no la autenticacion
# (eso lo cubre check_webhook_auth_http.py), asi que manda siempre el
# secreto correcto. Se lee del mismo .env que usa el servidor, para no
# escribir el secreto en el codigo.
CABECERAS_AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}

PUERTO = 8010
BASE = f"http://127.0.0.1:{PUERTO}"
EMAIL_OK = "http-ok@example.com"
EMAIL_MALO = "esto-no-es-un-email"

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


# ---------- conexion de observacion, ajena al pool del servidor -------
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor(cursor_factory=RealDictCursor)


# Todos los emails que usan las pruebas. Los de las pruebas 3 y 4 NO
# deberian llegar a escribirse nunca (son 422), pero si una validacion se
# rompe -como en la prueba en negativo- se crearian filas con ellos, y la
# limpieza tiene que poder borrarlas igualmente.
EMAILS_PRUEBA = [EMAIL_OK, "otro-" + EMAIL_OK, "tel-" + EMAIL_OK]


def limpiar():
    # "= ANY(%s)" compara con cada elemento de la lista: psycopg2 envia la
    # lista de Python como un ARRAY de Postgres.
    cur.execute(
        """DELETE FROM oportunidades WHERE lead_id IN (
             SELECT l.id FROM leads l JOIN clientes c ON c.id=l.cliente_id
             WHERE c.email = ANY(%s));""",
        (EMAILS_PRUEBA,),
    )
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email = ANY(%s));",
        (EMAILS_PRUEBA,),
    )
    cur.execute("DELETE FROM clientes WHERE email = ANY(%s);", (EMAILS_PRUEBA,))
    cn.commit()


def contar_todo():
    cur.execute(
        "SELECT (SELECT COUNT(*) FROM clientes) c, (SELECT COUNT(*) FROM leads) l, "
        "(SELECT COUNT(*) FROM oportunidades) o;"
    )
    r = cur.fetchone()
    cn.commit()
    return (r["c"], r["l"], r["o"])


limpiar()

# ---------- arranque del servidor en un hilo aparte -------------------
config = uvicorn.Config(
    "app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"
)
servidor = uvicorn.Server(config)
hilo = threading.Thread(target=servidor.run, daemon=True)

print("=" * 78)
print("ARRANQUE DEL SERVIDOR (uvicorn.Server, sin --reload)")
print("=" * 78)
hilo.start()

# Se espera a que conteste /health, en vez de dormir un tiempo fijo.
inicio = time.time()
while time.time() - inicio < 30:
    try:
        r = requests.get(f"{BASE}/health", timeout=1)
        if r.status_code == 200:
            break
    except requests.exceptions.RequestException:
        time.sleep(0.25)
else:
    print("  El servidor no respondio en 30s")
    sys.exit(1)

print(f"  Servidor arriba en {time.time() - inicio:.2f}s")
print(f"  GET /health    -> {r.status_code} {r.json()}")
r = requests.get(f"{BASE}/health/db", timeout=10)
print(f"  GET /health/db -> {r.status_code} {r.json()}")

try:
    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA 1 - POST /leads con datos VALIDOS (HTTP real)")
    print("=" * 78)
    antes = contar_todo()
    print(f"  Filas antes (clientes, leads, oportunidades): {antes}")

    cuerpo = {
        "nombre": "Carmen Ruiz",
        "email": EMAIL_OK,
        # Con espacios y guiones A PROPOSITO (2026-09-24): debe llegar a
        # la base de datos ya normalizado como "+34655443322".
        "telefono": "+34 655-443-322",
        "tipo_reforma": "cocina",
        "m2": 14.0,
        "nivel_acabados": "alto",
        "incluye_cambios_estructurales": True,
        "fotos": ["leads-temp/tokHTTP/f1.jpg"],
        "lead_token": "tokHTTP",
    }
    print(f"  Enviando POST {BASE}/leads")
    resp = requests.post(f"{BASE}/leads", json=cuerpo, headers=CABECERAS_AUTH, timeout=20)
    print(f"  <- HTTP {resp.status_code}")
    print(f"  <- Cabecera content-type: {resp.headers.get('content-type')}")
    print(f"  <- Cuerpo: {resp.text}")

    comprobar("Codigo HTTP 201 Created", resp.status_code == 201, f"({resp.status_code})")
    cuerpo_resp = resp.json()
    # Fase 2: el contrato gana la clave "creado" (5 claves).
    comprobar("La respuesta trae las 5 claves del contrato",
              set(cuerpo_resp) == {"lead_id", "cliente_id", "oportunidad_id", "status", "creado"},
              f"({sorted(cuerpo_resp)})")
    comprobar("status == 'nueva'", cuerpo_resp.get("status") == "nueva")
    comprobar("Los 3 ids son enteros",
              all(isinstance(cuerpo_resp[k], int)
                  for k in ("lead_id", "cliente_id", "oportunidad_id")))

    despues = contar_todo()
    print(f"\n  Filas despues: {despues}")
    comprobar("Se creo exactamente 1 cliente, 1 lead y 1 oportunidad",
              despues == (antes[0] + 1, antes[1] + 1, antes[2] + 1),
              f"({antes} -> {despues})")

    cur.execute(
        """
        SELECT c.email, c.telefono, l.canal, l.fotos_urls, l.datos_estructurados,
               o.tipo_reforma, o.estado, o.datos_completos
        FROM leads l
        JOIN clientes c      ON c.id = l.cliente_id
        JOIN oportunidades o ON o.lead_id = l.id
        WHERE l.id = %s;
        """,
        (cuerpo_resp["lead_id"],),
    )
    fila = cur.fetchone()
    cn.commit()
    print("\n  Fila real escrita por la peticion HTTP:")
    for k, v in fila.items():
        print(f"    {k:<22} = {v!r}")
    comprobar("La fila de la base de datos corresponde al email enviado",
              fila["email"] == EMAIL_OK)
    comprobar("tipo_reforma llego intacto hasta la columna", fila["tipo_reforma"] == "cocina")
    comprobar("clientes.telefono se guardo NORMALIZADO",
              fila["telefono"] == "+34655443322", f"({fila['telefono']!r})")
    comprobar("datos_estructurados.contacto trae el contacto de esta llamada",
              fila["datos_estructurados"].get("contacto") == {
                  "nombre": "Carmen Ruiz", "email": EMAIL_OK, "telefono": "+34655443322"},
              f"({fila['datos_estructurados'].get('contacto')})")
    comprobar("canal == 'chat_web'", fila["canal"] == "chat_web", f"({fila['canal']!r})")

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA 2 - POST /leads con EMAIL INVALIDO (debe dar 422 sin tocar la BD)")
    print("=" * 78)
    antes2 = contar_todo()
    print(f"  Filas antes: {antes2}")

    cuerpo_malo = dict(cuerpo)
    cuerpo_malo["email"] = EMAIL_MALO
    cuerpo_malo["lead_token"] = "tokMALO"
    print(f"  Enviando POST con email = {EMAIL_MALO!r}")
    resp2 = requests.post(
        f"{BASE}/leads", json=cuerpo_malo, headers=CABECERAS_AUTH, timeout=20
    )
    print(f"  <- HTTP {resp2.status_code}")
    print(f"  <- Cuerpo: {resp2.text}")

    comprobar("Codigo HTTP 422 Unprocessable Entity",
              resp2.status_code == 422, f"({resp2.status_code})")
    detalle = resp2.json().get("detail", [{}])[0]
    comprobar("El error apunta al campo 'email'",
              detalle.get("loc", [])[-1:] == ["email"], f"({detalle.get('loc')})")

    despues2 = contar_todo()
    print(f"\n  Filas despues: {despues2}")
    comprobar("NO se creo NINGUNA fila (la validacion corta antes de la BD)",
              despues2 == antes2, f"({antes2} -> {despues2})")

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA 3 - POST /leads con tipo_reforma CON ENIE (422, no llega al CHECK)")
    print("=" * 78)
    antes3 = contar_todo()
    cuerpo_enie = dict(cuerpo)
    cuerpo_enie["email"] = "otro-" + EMAIL_OK
    cuerpo_enie["tipo_reforma"] = "ba\u00f1o"
    resp3 = requests.post(
        f"{BASE}/leads", json=cuerpo_enie, headers=CABECERAS_AUTH, timeout=20
    )
    print(f"  <- HTTP {resp3.status_code}")
    print(f"  <- Cuerpo: {resp3.text[:300]}")
    comprobar("Codigo HTTP 422", resp3.status_code == 422, f"({resp3.status_code})")
    comprobar("Tampoco se creo ninguna fila", contar_todo() == antes3)

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA 4 - POST /leads con telefono 'telefono666555' (422 que nombra el campo)")
    print("=" * 78)
    antes4 = contar_todo()
    cuerpo_tel = dict(cuerpo)
    cuerpo_tel["email"] = "tel-" + EMAIL_OK
    cuerpo_tel["telefono"] = "telefono666555"
    resp4 = requests.post(
        f"{BASE}/leads", json=cuerpo_tel, headers=CABECERAS_AUTH, timeout=20
    )
    print(f"  <- HTTP {resp4.status_code}")
    print(f"  <- Cuerpo: {resp4.text[:400]}")
    comprobar("Codigo HTTP 422", resp4.status_code == 422, f"({resp4.status_code})")
    # Si la validacion estuviera desactivada, la respuesta seria un 201 y
    # no traeria "detail"; .get(..., [{}]) evita que el script reviente
    # ahi y deja que la comprobacion marque FALLO.
    detalle4 = resp4.json().get("detail", [{}])[0] if resp4.status_code == 422 else {}
    comprobar("El error apunta al campo 'telefono'",
              detalle4.get("loc", [])[-1:] == ["telefono"], f"({detalle4.get('loc')})")
    comprobar("No se creo ninguna fila", contar_todo() == antes4)

    # ==============================================================
    print("\n" + "=" * 78)
    print("PRUEBA 5 - Reintento de n8n: el MISMO cuerpo de la prueba 1 otra vez")
    print("=" * 78)
    antes5 = contar_todo()
    resp5 = requests.post(f"{BASE}/leads", json=cuerpo, headers=CABECERAS_AUTH, timeout=20)
    print(f"  <- HTTP {resp5.status_code}")
    print(f"  <- Cuerpo: {resp5.text}")
    # Una repeticion responde 200, no 201: no ha creado nada. Es el mismo
    # codigo que da POST /calculate-estimate en su repeticion. El cuerpo se
    # lee con cualquier 2xx para que, si el codigo fuera otro, las
    # comprobaciones de abajo sigan pudiendo informar.
    cuerpo5 = resp5.json() if resp5.status_code in (200, 201) else {}
    comprobar("Codigo HTTP 200 (repeticion: no se ha creado nada)",
              resp5.status_code == 200, f"({resp5.status_code})")
    comprobar("creado == false", cuerpo5.get("creado") is False, f"({cuerpo5.get('creado')!r})")
    comprobar("MISMO lead_id que la prueba 1",
              cuerpo5.get("lead_id") == cuerpo_resp["lead_id"],
              f"({cuerpo_resp['lead_id']} y {cuerpo5.get('lead_id')})")
    despues5 = contar_todo()
    comprobar("0 filas nuevas en las 3 tablas", despues5 == antes5, f"({antes5} -> {despues5})")
    comprobar("La prueba 1 dijo creado == true", cuerpo_resp.get("creado") is True)

finally:
    # ---------- apagado ordenado --------------------------------------
    print("\n" + "=" * 78)
    print("APAGADO DEL SERVIDOR")
    print("=" * 78)
    t0 = time.time()
    servidor.should_exit = True
    hilo.join(timeout=20)
    print(f"  Apagado en {time.time() - t0:.3f}s   hilo vivo: {hilo.is_alive()}")

    print("\n  Limpieza de filas de prueba:")
    limpiar()
    print(f"  Filas (clientes, leads, oportunidades): {contar_todo()}")
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for x in fallos:
        print("  - " + x)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
