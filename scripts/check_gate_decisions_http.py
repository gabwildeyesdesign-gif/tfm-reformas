"""
Verificación por HTTP REAL de la llave de administración (GATE_SECRET) y de
que el alias POST /gate-decisions ya NO existe.

Cambio 2026-10-22: alias POST /gate-decisions quitado (bloque 5); versión
anterior de este script en aba39c4. Antes probaba el contrato entero del
alias (plan: docs/Plan_Endpoint_Gate_Decisions.txt, sección 4.1). Desde hoy
solo prueba:
  CASO 0  el servidor se NIEGA a arrancar con un GATE_SECRET vacío o
          repetido (en procesos hijos, en el puerto 8020, que nunca llega a
          abrirse). Es la llave de TODAS las rutas de administración, no del
          alias, y no la prueba ningún otro script (H-0-6): se conserva
          intacto.
  CASO 1  el alias ya no existe: POST /gate-decisions responde 404 EXACTO,
          con la llave y sin ella (no 401: la ruta no existe, así que no
          hay ninguna puerta que comprobar), y GET también 404 (un 405
          diría que queda otra ruta con ese camino).
  CASO 2  la ruta que lo sustituye, POST /resultados-llamada, sigue cerrada
          sin llave: 401 con el cuerpo y la cabecera de siempre.
Lo que el alias vigilaba de la ruta nueva está ahora en
check_resultados_llamada_http (CASO 13c) y check_resultados_llamada_service
(S8).

Por qué conserva el nombre: scripts/fechas_prueba.py da a cada script su
bloque de semanas por su POSICIÓN en una lista; quitarlo movería las fechas
de los siete scripts que van detrás. Este script ya no crea visitas.

Las 4 preguntas: (1) no escribe en ninguna tabla (un 404 de una ruta que no
existe y un 401 no dejan log); (2) no pasa ninguna conexión al código;
(3) si se corta, no deja nada; (4) puertos 8019 y 8020, nunca el 8000; ningún
dato real. El valor de los secretos NUNCA se imprime: solo se usan en las
cabeceras y en el entorno de los procesos hijos.
"""

# ----------------------------------------------------------------------
# Imports: piezas de otras bibliotecas que este script necesita
# ----------------------------------------------------------------------
# os: para copiar las variables de entorno al lanzar procesos hijos.
import os
# socket: para comprobar, antes de nada, que nadie escucha en nuestros puertos.
import socket
# subprocess: para lanzar uvicorn en un proceso hijo (pruebas de arranque).
import subprocess
# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# threading: para ejecutar el servidor en un hilo de este mismo proceso.
import threading
# time: para medir tiempos y esperar a que el servidor arranque.
import time
# Path: rutas de archivos independientes del sistema operativo.
from pathlib import Path

# La raíz del repositorio es la carpeta padre de scripts/. Se añade al
# principio de sys.path para que "import app..." encuentre el paquete app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Se pone la raíz en la posición 0 de la lista de carpetas donde Python busca módulos.
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# requests: para hacer peticiones HTTP reales al servidor.
import requests
# uvicorn: el servidor que ejecuta la aplicación FastAPI.
import uvicorn

# Los valores del .env (app.config los carga). Se usan solo en cabeceras y
# en el entorno de los procesos hijos; nunca se imprimen.
from app.config import GATE_SECRET, MCP_SECRET, WEBHOOK_SECRET

# ----------------------------------------------------------------------
# Constantes de la prueba
# ----------------------------------------------------------------------
# Puerto del servidor de prueba (el de siempre de este script). Nunca el 8000.
PUERTO = 8019
# Puerto para las pruebas de arranque: el servidor NO debe llegar a abrirlo.
PUERTO_ARRANQUE = 8020
# Dirección base de todas las peticiones.
BASE = f"http://127.0.0.1:{PUERTO}"
# La llave de administración (la del formulario de n8n).
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# El 404 de FastAPI para una ruta que no existe, copiado de su documentación
# de errores (no del código probado).
CUERPO_404 = {"detail": "Not Found"}
# Un cuerpo con la forma del contrato ANTIGUO del alias: si la ruta siguiera
# viva, con la llave buena registraría un descarte (o daría 404 de negocio).
CUERPO_ALIAS = {"oportunidad_id": 2147483647, "decision": "descartar", "motivo": "precio", "informe": "x"}

# Número de comprobaciones correctas y lista de las que fallan.
ok = 0
fallos = []
# Código HTTP de cada respuesta (para "ningún 500").
codigos = []


# comprobar: se define una vez y se usa en todas las comprobaciones.
def comprobar(titulo, condicion, detalle=""):
    """Anota una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # "global ok": la función modifica la variable ok de fuera, no una copia.
    global ok
    # Si la condición es verdadera, la comprobación pasa:
    if condicion:
        # se suma 1 al contador de correctas y se imprime [OK].
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Si no (else), la comprobación falla:
    else:
        # se guarda el texto del fallo para el resumen final y se imprime [FALLO].
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


# pedir: una petición HTTP real que apunta su código.
def pedir(metodo, ruta, cuerpo=None, cabeceras=None):
    """La petición real; apunta el código para "ningún 500". Devuelve la respuesta."""
    # requests.request: GET o POST, con el cuerpo JSON si lo hay.
    r = requests.request(metodo, f"{BASE}{ruta}", headers=cabeceras or {}, json=cuerpo, timeout=30)
    # Se apunta el código.
    codigos.append(r.status_code)
    return r


# cuerpo: el JSON de una respuesta, o {} si no es JSON.
def cuerpo(r):
    """El JSON de la respuesta, o {} si no es JSON."""
    # r.json() lanza ValueError si el cuerpo no es JSON.
    try:
        return r.json()
    except ValueError:
        return {}


# puerto_ocupado: se usa antes de arrancar nada.
def puerto_ocupado(puerto):
    """True si algo acepta conexiones en 127.0.0.1:puerto."""
    # "with" cierra el socket al salir del bloque, pase lo que pase.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Espera como mucho 1 segundo a que conteste.
        s.settimeout(1)
        # connect_ex devuelve 0 si la conexión se pudo abrir.
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ======================================================================
# Antes de nada: nuestros dos puertos tienen que estar LIBRES. Si otro
# proceso los usa, se para y se avisa (CLAUDE.md): no se mata nada.
# ======================================================================
for p in (PUERTO, PUERTO_ARRANQUE):
    # Si alguno está ocupado...
    if puerto_ocupado(p):
        # ...se avisa...
        print(f"ABORTO: el puerto {p} está ocupado por otro proceso. No ejecuto nada ni mato nada.")
        # ...y se sale con código 2, sin ejecutar nada.
        sys.exit(2)

# ======================================================================
print("\nCASO 0 - Arranque: el servidor se NIEGA a arrancar (P4), en procesos hijos")
# ======================================================================
# La comprobación de arranque se ejecuta al IMPORTAR app/api/security.py,
# y en este proceso ya está importado; por eso cada caso se lanza en un
# proceso NUEVO. load_dotenv no sobrescribe variables que ya existen en el
# entorno, así que el GATE_SECRET que se pone aquí gana al del .env.
# PYTHONIOENCODING=utf-8: sin él, el hijo escribe su error (con tildes) en
# cp1252 y leerlo como UTF-8 revienta (gotcha de CLAUDE.md).
CASOS_ARRANQUE = [
    # (nombre del caso, valor de GATE_SECRET, texto que debe aparecer en el error)
    ("GATE_SECRET solo con espacios", "   ", "GATE_SECRET no está definido"),
    ("GATE_SECRET igual a WEBHOOK_SECRET", WEBHOOK_SECRET, "GATE_SECRET es igual a WEBHOOK_SECRET"),
    ("GATE_SECRET igual a MCP_SECRET", MCP_SECRET, "GATE_SECRET es igual a MCP_SECRET"),
]
# Un proceso hijo por caso.
for nombre, valor, texto_esperado in CASOS_ARRANQUE:
    # Título del caso.
    print(f"  -- {nombre}")
    # dict(os.environ, CLAVE=valor): una copia del entorno con esa clave cambiada.
    entorno = dict(os.environ, GATE_SECRET=valor, PYTHONIOENCODING="utf-8")
    # try: si el hijo no termina en 60 s, salta TimeoutExpired.
    try:
        # python -m uvicorn app.main:app --port 8020, sin --reload, en la carpeta del repo y con ese entorno.
        proc = subprocess.run(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO_ARRANQUE)],
            cwd=RAIZ_REPO, env=entorno, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        # Todo lo que escribió el hijo: la salida normal y la de errores.
        salida = proc.stdout + proc.stderr
        # Su código de salida (0 = terminó bien; distinto de 0 = error).
        codigo = proc.returncode
    # Si no terminó a tiempo:
    except subprocess.TimeoutExpired:
        # Si el servidor SÍ arrancó, no termina solo: a los 60 s
        # subprocess.run mata a ESE hijo (el nuestro) y llega aquí.
        salida, codigo = "", None
    # Última línea del error: el RuntimeError con su mensaje (sin valores).
    ultima = salida.strip().splitlines()[-1] if salida.strip() else "(sin salida)"
    # Se enseñan el código y el principio de la última línea.
    print(f"      código de salida {codigo}; última línea: {ultima[:150]}")
    # Las cuatro comprobaciones de cada caso:
    comprobar("se detiene con error (código != 0)", codigo not in (None, 0), f"({codigo})")
    comprobar(f"el error dice '{texto_esperado}'", texto_esperado in salida)
    comprobar("nunca llegó a abrir el puerto", "Uvicorn running" not in salida)
    # El mensaje nombra las variables, pero NUNCA debe llevar su valor.
    comprobar("la salida no contiene el valor de ningún secreto",
              all(s not in salida for s in (WEBHOOK_SECRET, MCP_SECRET, GATE_SECRET)))

# ======================================================================
# Servidor de verdad en un hilo de este proceso
# ======================================================================
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# daemon=True: si el script termina de golpe, el hilo no lo deja colgado.
hilo = threading.Thread(target=servidor.run, daemon=True)
# Se arranca el hilo: el servidor empieza a funcionar en paralelo.
hilo.start()
# Hora de inicio, para medir cuánto tarda.
inicio = time.time()
# Se pregunta a /health hasta que conteste, como mucho 30 s. El "else" de
# un while se ejecuta solo si el bucle termina SIN break (no contestó).
while time.time() - inicio < 30:
    # try: mientras el servidor no escucha, la petición falla con RequestException.
    try:
        # Si /health responde 200, el servidor está listo: break sale del bucle.
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    # Si todavía no escucha, se espera un cuarto de segundo y se vuelve a probar.
    except requests.exceptions.RequestException:
        time.sleep(0.25)
# else del while: solo si pasaron los 30 s sin break.
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
# Se enseña cuánto tardó en arrancar.
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio:.2f} s")

# try/finally: pase lo que pase dentro (incluso un error), el servidor se apaga.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - El alias POST /gate-decisions ya no existe (404 exacto)")
    # Con la llave buena y el cuerpo del contrato antiguo, y sin ninguna llave.
    for nombre, cabeceras in (("con X-Gate-Secret", AUTH_GATE), ("sin llave", {})):
        r = pedir("POST", "/gate-decisions", CUERPO_ALIAS, cabeceras)
        # 404 con el cuerpo exacto de "no existe esa ruta" (no un 404 de negocio).
        comprobar(f"POST /gate-decisions {nombre} -> 404 {CUERPO_404}",
                  r.status_code == 404 and cuerpo(r) == CUERPO_404, f"({r.status_code} {r.text[:120]})")
    # GET: 404 también. Si quedara otra ruta con ese camino, FastAPI daría 405.
    r = pedir("GET", "/gate-decisions", cabeceras=AUTH_GATE)
    comprobar("GET /gate-decisions -> 404 (no 405: ninguna ruta con ese camino)",
              r.status_code == 404 and cuerpo(r) == CUERPO_404, f"({r.status_code} {r.text[:120]})")

    # ------------------------------------------------------------------
    print("\nCASO 2 - POST /resultados-llamada sin llave: 401")
    # Cuerpo vacío y sin cabecera: la llave se comprueba ANTES que el cuerpo.
    r = pedir("POST", "/resultados-llamada", {})
    comprobar("POST /resultados-llamada sin llave -> 401 {'detail': 'No autorizado'}",
              r.status_code == 401 and cuerpo(r) == {"detail": "No autorizado"}, f"({r.status_code} {r.text[:120]})")
    # La misma cabecera que el 401 de /leads (función _no_autorizado).
    comprobar("   con la cabecera WWW-Authenticate: APIKey", r.headers.get("WWW-Authenticate") == "APIKey",
              f"({r.headers.get('WWW-Authenticate')})")

    # ------------------------------------------------------------------
    print("\nCASO 3 - Ningún 500")
    comprobar(f"ninguna respuesta 500 ({len(codigos)} peticiones)", 500 not in codigos, f"({codigos})")
# finally: se ejecuta SIEMPRE, haya ido bien o mal lo de arriba.
finally:
    # Se pide al servidor que termine, y se espera a su hilo como mucho 15 s.
    servidor.should_exit = True
    hilo.join(timeout=15)

# Resumen y código de salida (1 si algo falló, para la suite).
total = ok + len(fallos)
# Línea de separación y resultado: correctas / total.
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Si hubo algún fallo, se imprime la lista y el script termina con código 1.
if fallos:
    print("FALLOS:")
    # Un fallo por línea.
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
