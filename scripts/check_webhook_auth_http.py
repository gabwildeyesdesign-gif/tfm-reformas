"""
FASE 1 - Verificacion por HTTP REAL de la autenticacion de POST /leads.

Comprueba, con el servidor arrancado de verdad y peticiones HTTP reales,
que la cabecera X-Webhook-Secret se exige y se compara correctamente:

  1. Sin cabecera                          -> 401
  2. Cabecera con valor incorrecto         -> 401 (misma respuesta que 1)
  3. Cabecera con un caracter no ASCII (n) -> 401, NO 500, y sin fila
                                              nueva en la tabla logs
  4. Cabecera correcta                     -> 201, con las 3 filas creadas
  5. Ninguna peticion rechazada escribe filas en ninguna tabla

Y ademas, fuera del servidor en marcha:

  6. Fail fast: con WEBHOOK_SECRET vacio, uvicorn se niega a arrancar.
  7. Con WEBHOOK_SECRET vacio, importar app.config (lo que hacen los
     scripts que solo necesitan DATABASE_URL) sigue funcionando.

Igual que check_leads_endpoint_http.py, el servidor se arranca desde
Python con uvicorn.Server en un hilo, SIN --reload (en Windows el
reloader cuelga el apagado sin consola interactiva; ver CLAUDE.md).
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# Mismo motivo que en el resto de scripts/: Python solo anade scripts/ a
# sys.path, y el paquete "app" esta en la raiz del repositorio. Se
# calcula la raiz a partir de este archivo para no depender de una ruta
# absoluta de esta maquina.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn
from psycopg2.extras import RealDictCursor

from app.config import DATABASE_URL, WEBHOOK_SECRET

# Puerto distinto del 8010 de check_leads_endpoint_http.py, para que los
# dos scripts no choquen si alguna vez se lanzan a la vez.
PUERTO = 8011
BASE = f"http://127.0.0.1:{PUERTO}"
CABECERA = "X-Webhook-Secret"
EMAIL_OK = "auth-ok@example.com"

# Cuerpo valido de POST /leads. Es valido A PROPOSITO en las pruebas de
# rechazo: si la peticion recibe 401 con un cuerpo perfecto, la unica
# causa posible es la autenticacion, no la validacion.
CUERPO_VALIDO = {
    "nombre": "Prueba Autenticacion",
    "email": EMAIL_OK,
    "telefono": "+34600111222",
    "tipo_reforma": "cocina",
    "m2": 10.0,
    "nivel_acabados": "medio",
    "incluye_cambios_estructurales": False,
    "fotos": [],
    "lead_token": "tokAUTH",
}

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    # Mismo contador que los demas check_*.py: acumula los fallos en vez
    # de pararse en el primero, para ver el cuadro completo de una vez.
    global ok
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


# ---------- conexion de observacion, ajena al pool del servidor -------
# Una conexion propia (no la del pool del servidor) para contar filas
# desde fuera, como haria un observador independiente.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor(cursor_factory=RealDictCursor)


def contar_todo():
    # Se cuenta tambien logs: el caso 3 existe precisamente para probar
    # que una cabecera rara NO acaba escribiendo en logs a traves del
    # manejador global de errores.
    cur.execute(
        "SELECT (SELECT COUNT(*) FROM clientes) c, (SELECT COUNT(*) FROM leads) l, "
        "(SELECT COUNT(*) FROM oportunidades) o, (SELECT COUNT(*) FROM logs) g;"
    )
    r = cur.fetchone()
    # commit() cierra la transaccion de lectura; sin el, las siguientes
    # consultas verian una foto congelada de la base de datos.
    cn.commit()
    return (r["c"], r["l"], r["o"], r["g"])


def limpiar():
    # Borra en orden inverso a las claves foraneas (hijos antes que
    # padres), igual que check_leads_endpoint_http.py.
    cur.execute(
        """DELETE FROM oportunidades WHERE lead_id IN (
             SELECT l.id FROM leads l JOIN clientes c ON c.id=l.cliente_id
             WHERE c.email = %s);""",
        (EMAIL_OK,),
    )
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email=%s);",
        (EMAIL_OK,),
    )
    cur.execute("DELETE FROM clientes WHERE email=%s;", (EMAIL_OK,))
    cn.commit()


def post_leads(cabeceras, cuerpo=CUERPO_VALIDO):
    return requests.post(f"{BASE}/leads", json=cuerpo, headers=cabeceras, timeout=20)


def mostrar(resp):
    print(f"  <- HTTP {resp.status_code}")
    print(f"  <- WWW-Authenticate: {resp.headers.get('www-authenticate')}")
    print(f"  <- Cuerpo: {resp.text}")


limpiar()

# ---------- arranque del servidor en un hilo aparte -------------------
config = uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning")
servidor = uvicorn.Server(config)
hilo = threading.Thread(target=servidor.run, daemon=True)

print("=" * 78)
print("ARRANQUE DEL SERVIDOR (uvicorn.Server, sin --reload)")
print("=" * 78)
hilo.start()

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
# /health no lleva cabecera: debe seguir abierto, porque la dependencia
# se aplico solo a POST /leads y no a toda la aplicacion.
print(f"  GET /health (sin cabecera) -> {r.status_code} {r.json()}")
comprobar("/health sigue accesible sin cabecera", r.status_code == 200)

try:
    filas_inicio = contar_todo()
    print(f"\n  Filas al inicio (clientes, leads, oportunidades, logs): {filas_inicio}")

    # ==============================================================
    print("\n" + "=" * 78)
    print("CASO 1 - SIN cabecera X-Webhook-Secret")
    print("=" * 78)
    r1 = post_leads({})
    mostrar(r1)
    comprobar("HTTP 401", r1.status_code == 401, f"({r1.status_code})")
    comprobar("Incluye WWW-Authenticate (exigido por RFC 9110 en un 401)",
              r1.headers.get("www-authenticate") == "APIKey")

    # ==============================================================
    print("\n" + "=" * 78)
    print("CASO 2 - Cabecera con valor INCORRECTO")
    print("=" * 78)
    # Mismo largo que el real y solo cambia el ultimo caracter: el caso
    # mas parecido posible al secreto sin serlo.
    casi = WEBHOOK_SECRET[:-1] + ("A" if WEBHOOK_SECRET[-1] != "A" else "B")
    r2 = post_leads({CABECERA: casi})
    mostrar(r2)
    comprobar("HTTP 401", r2.status_code == 401, f"({r2.status_code})")
    comprobar("Respuesta IDENTICA a la del caso 1 (no se distingue falta/incorrecta)",
              (r2.status_code, r2.text, r2.headers.get("www-authenticate"))
              == (r1.status_code, r1.text, r1.headers.get("www-authenticate")))

    # Comprobacion extra: sin cabecera Y con un cuerpo invalido. Si la
    # autenticacion va antes que la validacion, la respuesta debe ser
    # 401, no 422 (el comentario de app/api/leads.py lo afirma; aqui se
    # demuestra).
    r2b = post_leads({}, cuerpo={"email": "esto-no-es-un-email"})
    print(f"\n  Extra: sin cabecera + cuerpo invalido -> HTTP {r2b.status_code} {r2b.text}")
    comprobar("La autenticacion se evalua antes que la validacion del cuerpo (401, no 422)",
              r2b.status_code == 401, f"({r2b.status_code})")

    # ==============================================================
    print("\n" + "=" * 78)
    print("CASO 3 - Cabecera con caracter NO ASCII ('ñ')")
    print("=" * 78)
    logs_antes = contar_todo()[3]
    # requests codifica las cabeceras de tipo str en latin-1, que es
    # justo como Starlette las decodifica al recibirlas: el servidor ve
    # una 'ñ' de verdad, el caso que haria fallar compare_digest(str).
    r3 = post_leads({CABECERA: "ñ" + WEBHOOK_SECRET[1:]})
    mostrar(r3)
    logs_despues = contar_todo()[3]
    comprobar("HTTP 401 (y no 500)", r3.status_code == 401, f"({r3.status_code})")
    comprobar("No se escribio ninguna fila en logs",
              logs_despues == logs_antes, f"({logs_antes} -> {logs_despues})")

    # ==============================================================
    print("\n" + "=" * 78)
    print("CASO 5 - Las peticiones rechazadas no escribieron NADA")
    print("=" * 78)
    tras_rechazos = contar_todo()
    print(f"  Filas tras los rechazos: {tras_rechazos}")
    comprobar("Ninguna tabla cambio tras las 4 peticiones rechazadas",
              tras_rechazos == filas_inicio, f"({filas_inicio} -> {tras_rechazos})")

    # ==============================================================
    # El caso 4 va despues del 5 a proposito: el 5 compara contra las
    # filas del inicio, y el 4 si que escribe.
    print("\n" + "=" * 78)
    print("CASO 4 - Cabecera CORRECTA")
    print("=" * 78)
    r4 = post_leads({CABECERA: WEBHOOK_SECRET})
    print(f"  <- HTTP {r4.status_code}")
    print(f"  <- Cuerpo: {r4.text}")
    comprobar("HTTP 201 Created", r4.status_code == 201, f"({r4.status_code})")
    tras_ok = contar_todo()
    print(f"  Filas despues: {tras_ok}")
    comprobar("Se creo exactamente 1 cliente, 1 lead y 1 oportunidad (y 0 logs)",
              tras_ok == (tras_rechazos[0] + 1, tras_rechazos[1] + 1,
                          tras_rechazos[2] + 1, tras_rechazos[3]),
              f"({tras_rechazos} -> {tras_ok})")

finally:
    print("\n" + "=" * 78)
    print("APAGADO DEL SERVIDOR")
    print("=" * 78)
    t0 = time.time()
    servidor.should_exit = True
    hilo.join(timeout=20)
    print(f"  Apagado en {time.time() - t0:.3f}s   hilo vivo: {hilo.is_alive()}")
    limpiar()
    print(f"  Limpieza hecha. Filas (clientes, leads, oportunidades, logs): {contar_todo()}")
    cur.close()
    cn.close()


# ======================================================================
# Casos 6 y 7: se lanzan en PROCESOS NUEVOS (subprocess) porque la
# comprobacion de arranque se ejecuta al importar app/api/security.py, y
# en este proceso ya esta importado. Un proceso nuevo empieza de cero.
#
# Se pone WEBHOOK_SECRET a "   " (solo espacios) en el entorno del hijo.
# load_dotenv() no sobrescribe variables que ya existen en el entorno,
# asi que el valor del .env queda ignorado y el hijo ve el secreto
# "vacio". Se usan espacios y no "" para probar a la vez que strip()
# los trata como ausentes.
# ======================================================================
#
# PYTHONIOENCODING=utf-8: en Windows, un proceso hijo escribe su stderr
# en la codificacion de la consola (cp1252), y el mensaje de error
# lleva tildes. Sin esto, leerlo como UTF-8 revienta con
# UnicodeDecodeError (paso de verdad en la primera ejecucion de este
# script). Se fuerza al hijo a escribir en UTF-8, que es como se lee.
entorno_sin_secreto = dict(os.environ, WEBHOOK_SECRET="   ", PYTHONIOENCODING="utf-8")

print("\n" + "=" * 78)
print("CASO 6 - FAIL FAST: uvicorn con WEBHOOK_SECRET vacio")
print("=" * 78)
t0 = time.time()
proc = subprocess.run(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8012"],
    cwd=RAIZ_REPO, env=entorno_sin_secreto,
    capture_output=True, text=True, encoding="utf-8", timeout=60,
)
print(f"  Proceso terminado en {time.time() - t0:.2f}s con codigo de salida {proc.returncode}")
print("  Ultimas lineas de stderr:")
for linea in proc.stderr.strip().splitlines()[-3:]:
    print(f"    | {linea}")
comprobar("uvicorn termina con error (codigo != 0)", proc.returncode != 0,
          f"(codigo {proc.returncode})")
comprobar("El error nombra WEBHOOK_SECRET", "WEBHOOK_SECRET" in proc.stderr)
comprobar("Nunca llego a abrir el puerto",
          "Uvicorn running" not in proc.stderr + proc.stdout)

print("\n" + "=" * 78)
print("CASO 7 - Con WEBHOOK_SECRET vacio, importar app.config NO falla")
print("=" * 78)
proc7 = subprocess.run(
    [sys.executable, "-c",
     "import app.config as c; print('import ok; DATABASE_URL definido:', bool(c.DATABASE_URL))"],
    cwd=RAIZ_REPO, env=entorno_sin_secreto,
    capture_output=True, text=True, encoding="utf-8", timeout=60,
)
print(f"  codigo {proc7.returncode}  stdout: {proc7.stdout.strip()}  stderr: {proc7.stderr.strip()!r}")
comprobar("Los scripts que solo usan app.config no necesitan el secreto",
          proc7.returncode == 0)

print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for x in fallos:
        print("  - " + x)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
