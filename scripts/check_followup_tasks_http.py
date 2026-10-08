"""
Verificación por HTTP REAL de POST /create-followup-task (plan:
docs/Plan_Endpoint_Create_Followup_Task.txt, sección 6.2, CASOS 1-15).

Qué hace, en pocas palabras:
  1. Levanta el servidor de verdad (uvicorn.Server en un hilo de este
     proceso, SIN --reload, en el puerto 8025: nunca el 8000, que es el
     uvicorn de n8n de Gabi). Si el 8025 está ocupado, se para sin tocar
     nada. Comprueba que quien escucha en el puerto es este proceso (PID).
  2. Crea sus propios datos por HTTP, como lo hará n8n (POST /leads, POST
     /calculate-estimate, POST /visits, POST /gate-decisions), y por SQL,
     SOLO sobre filas propias, lo que la API no puede producir (atrasar el
     presupuesto, poner fecha_ultimo_contacto, insertar una visita sin
     cambiar el estado, forzar un estado).
  3. Prueba el contrato entero: llave, forma, 404, cada 409 y su orden, la
     matriz lista <-> endpoint, el 201 con sus efectos, el 200, el 503, la
     concurrencia y la carrera con POST /visits.
  4. Borra SOLO lo suyo y comprueba que no ha tocado nada ajeno.

NUNCA llama al endpoint con oportunidades ajenas: los 8 seguimientos por
abrir reales (D25.16) solo se MIRAN (siguen igual al terminar).

Marca propia: emails http-followup-<8 caracteres>@example.com, tokens uuid4.

Márgenes del plazo por HTTP: "dentro" = 49 h; "fuera" = 48 h - 10 min,
aplicado al FINAL de la preparación (el reloj avanza mientras se prepara).
El límite exacto se prueba en check_followup_service (S1).

Si alguna petición al endpoint respondió 500 (solo debe pasar en las
pruebas en negativo), los logs del manejador global no llevan marca propia:
se borran por id exacto, y solo si son exactamente los esperados (regla de
CLAUDE.md).

El valor de los secretos NUNCA se imprime: solo se usan en las cabeceras.
"""

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
# contextlib.redirect_stderr: para capturar la línea [AVISO] del 503.
import contextlib
# io.StringIO: un "archivo" en memoria donde cae esa captura.
import io
# os: el PID de este proceso.
import os
# socket: para ver si alguien escucha ya en el puerto.
import socket
# subprocess: para preguntar a netstat quién escucha en el puerto.
import subprocess
# sys: sys.path, la salida y el código de salida.
import sys
# threading: el servidor corre en un hilo; Barrier sincroniza peticiones.
import threading
# time: esperas cortas mientras arranca el servidor.
import time
# uuid: tokens y marcas únicas en cada ejecución.
import uuid
# ThreadPoolExecutor: varias peticiones a la vez (concurrencia).
from concurrent.futures import ThreadPoolExecutor
# datetime y timedelta: instantes y duraciones.
from datetime import datetime, timedelta
# Decimal: para inyectar valores de la regla (CASO 10).
from decimal import Decimal
# Path: rutas de archivos.
from pathlib import Path
# ZoneInfo: la zona oficial Europe/Madrid, con el cambio de hora.
from zoneinfo import ZoneInfo

# La raíz del repositorio es la carpeta padre de scripts/. Se pone la
# primera en sys.path para que "import app..." encuentre el paquete.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: para preparar, mirar y limpiar la base de datos directamente.
import psycopg2
# requests: peticiones HTTP reales.
import requests
# uvicorn: el servidor.
import uvicorn

# Secretos y URL de la base de datos del .env (nunca se imprimen).
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET
# El módulo del servicio, para los cambios EN MEMORIA del CASO 10.
from app.services import followup_service as servicio

# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------
# Puerto propio (B9 del plan: libre y sin uso por ningún otro script).
PUERTO = 8025
# Direcciones del servidor y del endpoint.
BASE = f"http://127.0.0.1:{PUERTO}"
URL = f"{BASE}/create-followup-task"
# Cabeceras: la de n8n (para crear datos) y la del Gate (la de este endpoint).
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# Patrón SQL (LIKE) de los clientes de ESTE script.
PATRON_EMAIL = "http-followup-%@example.com"
# Zona de Madrid.
MADRID = ZoneInfo("Europe/Madrid")
# El único motivo de N0 (D25.13).
MOTIVO = "sin_respuesta_visita"
# Claves EXACTAS de la respuesta, copiadas del PLAN (2.6), no del código.
CLAVES_RESPUESTA = {"log_id", "oportunidad_id", "motivo", "estado_oportunidad", "fecha_apertura", "creado"}
# Claves EXACTAS del detalle del log, copiadas del PLAN (2.5).
CLAVES_DETALLE = {"motivo", "estado_anterior", "horas_seguimiento_presupuesto", "fecha_presupuesto"}

# ----------------------------------------------------------------------
# Contadores
# ----------------------------------------------------------------------
# Comprobaciones correctas y títulos de las que fallan.
ok = 0
fallos = []
# Código de CADA respuesta del endpoint (para contar los 500) y el texto
# de cada una (para buscar importes y emails, CASO 14).
codigos = []
textos = []
# Pares (código, creado) de cada respuesta 2xx (201 <-> creado, CASO 14).
pares_creado = []


def comprobar(titulo, condicion, detalle=""):
    """Imprime [OK] o [FALLO] y lleva la cuenta."""
    # global: el contador es el de fuera de la función.
    global ok
    # Correcta: suma; fallida: se apunta su título.
    if condicion:
        ok += 1
    else:
        fallos.append(titulo)
    # La línea del resultado, con el detalle si lo hay.
    print(f"    [{'OK' if condicion else 'FALLO'}] {titulo} {detalle}")


# ----------------------------------------------------------------------
# Conexión propia a la base de datos
# ----------------------------------------------------------------------
# autocommit: cada orden se confirma sola, así que cada lectura ve lo
# último que escribió el servidor y cada escritura propia la ve él.
cn = psycopg2.connect(DATABASE_URL)
cn.autocommit = True
# El cursor que envía las consultas y lee sus resultados.
cur = cn.cursor()


def uno(sql, params=None):
    """Ejecuta un SELECT y devuelve su primera fila (o None)."""
    # SQL: el SELECT que llega en sql (cada llamada lleva su "# SQL").
    cur.execute(sql, params)
    return cur.fetchone()


def todas(sql, params=None):
    """Ejecuta un SELECT y devuelve todas sus filas."""
    # SQL: el SELECT que llega en sql (cada llamada lleva su "# SQL").
    cur.execute(sql, params)
    return cur.fetchall()


def escribir(sql, params=None):
    """Una escritura SOBRE FILAS PROPIAS (cada llamada lleva su "# SQL")."""
    # SQL: el UPDATE o INSERT que llega en sql, confirmado al momento.
    cur.execute(sql, params)


# ----------------------------------------------------------------------
# Qué es "propio": todo lo que cuelga de los clientes de prueba
# ----------------------------------------------------------------------
# Subconsultas encadenadas: clientes propios -> sus leads -> sus
# oportunidades. %(patron)s se rellena con PATRON_EMAIL.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPS = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# La condición de fila propia de cada tabla, en el ORDEN de borrado
# (primero lo que apunta a otras tablas).
PROPIO = {
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPS})",
    "visitas": f"oportunidad_id IN ({SQL_OPS})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPS})",
    "oportunidades": f"id IN ({SQL_OPS})",
    "leads": f"id IN ({SQL_LEADS})",
    "clientes": f"id IN ({SQL_CLIENTES})",
}


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas."""
    # Una tabla cada vez, en el orden del diccionario.
    for tabla, propio in PROPIO.items():
        # SQL: borra las filas de esa tabla que cumplen su condición de propias.
        cur.execute(f"DELETE FROM {tabla} WHERE {propio};", {"patron": PATRON_EMAIL})


def foto_ajena():
    """Para cada tabla: (id_base, filas ajenas con id <= id_base)."""
    # Diccionario tabla -> (id_base, ajenas); se rellena en el bucle.
    foto = {}
    for tabla, propio in PROPIO.items():
        # SQL: el id máximo de la tabla ahora (0 si está vacía).
        id_base = uno(f"SELECT COALESCE(max(id), 0) FROM {tabla};")[0]
        # SQL: cuántas filas NO propias hay con id <= id_base.
        ajenas = uno(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({propio});",
                     {"base": id_base, "patron": PATRON_EMAIL})[0]
        foto[tabla] = (id_base, ajenas)
    return foto


# ----------------------------------------------------------------------
# Ayudas para crear y mirar datos propios
# ----------------------------------------------------------------------
# Emails propios creados (para comprobar que no salen en las respuestas).
EMAILS = []


def lead_http(gate):
    """Crea un lead por POST /leads. Devuelve (oportunidad_id, lead_token)."""
    # Token único (clave de idempotencia de POST /leads) y email propio.
    token = str(uuid.uuid4())
    email = f"http-followup-{uuid.uuid4().hex[:8]}@example.com"
    EMAILS.append(email)
    # Petición real a POST /leads con la cabecera de n8n. gate=True:
    # cambios estructurales, que activan el Gate al calcular.
    r = requests.post(f"{BASE}/leads", headers=AUTH_WEBHOOK, timeout=30, json={
        "nombre": "Seguimiento HTTP", "email": email, "telefono": "600000011", "tipo_reforma": "bano",
        "m2": 6, "nivel_acabados": "medio", "incluye_cambios_estructurales": gate, "fotos": [],
        "lead_token": token,
    })
    # Sin el lead no tiene sentido seguir.
    assert r.status_code == 201, r.text
    return r.json()["oportunidad_id"], token


def caso(gate=False, calcular=True):
    """Lead propio y, si se pide, su cálculo: con Gate queda en
    'pendiente_aprobacion'; sin Gate, en 'presupuesto_enviado'.
    Devuelve (oportunidad_id, lead_token)."""
    # El lead.
    op, token = lead_http(gate)
    # Sin cálculo, se queda en 'nueva' sin presupuesto.
    if calcular:
        # Petición real a POST /calculate-estimate; comprueba el estado.
        r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH_WEBHOOK,
                          json={"oportunidad_id": op}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == ("pendiente_aprobacion" if gate else "presupuesto_enviado"), r.text
    return op, token


def es_propia(op):
    """Comprueba que una oportunidad es propia (antes de tocarla por SQL)."""
    # SQL: 1 si la oportunidad cuelga de un cliente propio, 0 si no.
    n = uno(f"SELECT count(*) FROM oportunidades WHERE id = %(op)s AND {PROPIO['oportunidades']};",
            {"op": op, "patron": PATRON_EMAIL})[0]
    # Si no es propia, el script se para sin tocar nada.
    assert n == 1, f"la oportunidad {op} no es propia"


def retrasar(op, horas, minutos=0):
    """presupuestos.created_at = now() - (horas, minutos), SOLO si es propia."""
    # SQL: mueve la fecha del presupuesto hacia atrás; el AND con PROPIO
    # garantiza que solo puede tocar filas propias.
    escribir(f"UPDATE presupuestos SET created_at = now() - %(d)s WHERE oportunidad_id = %(op)s "
             f"AND {PROPIO['presupuestos']};",
             {"d": timedelta(hours=horas, minutes=minutos), "op": op, "patron": PATRON_EMAIL})


def poner_estado(op, estado):
    """Fuerza el estado de una oportunidad PROPIA (lo que la API no hace)."""
    # SQL: UPDATE limitado a filas propias.
    escribir(f"UPDATE oportunidades SET estado = %(estado)s WHERE id = %(op)s AND {PROPIO['oportunidades']};",
             {"estado": estado, "op": op, "patron": PATRON_EMAIL})


def poner_contacto(op):
    """fecha_ultimo_contacto = now() en una oportunidad PROPIA."""
    # SQL: UPDATE limitado a filas propias.
    escribir(f"UPDATE oportunidades SET fecha_ultimo_contacto = now() WHERE id = %(op)s "
             f"AND {PROPIO['oportunidades']};", {"op": op, "patron": PATRON_EMAIL})


def insertar_visita(op, estado):
    """Inserta una visita en una oportunidad PROPIA SIN cambiar su estado."""
    # Solo sobre oportunidades propias.
    es_propia(op)
    # SQL: la visita, dentro de 3 días, con un texto fijo.
    escribir("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
             "VALUES (%s, now() + interval '3 days', %s, 'prueba del seguimiento');", (op, estado))


def foto_op(op):
    """Lo que el endpoint podría tocar de una oportunidad: (estado,
    updated_at, fecha_ultimo_contacto, nº de visitas, nº de logs)."""
    # SQL: las tres columnas y dos recuentos (visitas y logs de esa oportunidad).
    return uno("SELECT o.estado, o.updated_at, o.fecha_ultimo_contacto, "
               "(SELECT count(*) FROM visitas v WHERE v.oportunidad_id = o.id), "
               "(SELECT count(*) FROM logs l WHERE l.entity_type = 'oportunidad' AND l.entity_id = o.id) "
               "FROM oportunidades o WHERE o.id = %s;", (op,))


def logs_apertura(op):
    """Los logs 'seguimiento_abierto' de una oportunidad: (id, detalle, created_at)."""
    # SQL: los logs de apertura de esa oportunidad, por orden de id.
    return todas("SELECT id, detalle, created_at FROM logs WHERE entity_type = 'oportunidad' "
                 "AND entity_id = %s AND accion = 'seguimiento_abierto' ORDER BY id;", (op,))


def pedir(op, cabeceras=AUTH_GATE, cuerpo=None):
    """POST /create-followup-task; apunta su código y su texto."""
    # Cuerpo por defecto: el correcto para esa oportunidad.
    cuerpo = cuerpo if cuerpo is not None else {"oportunidad_id": op, "motivo": MOTIVO}
    # Petición real al endpoint.
    r = requests.post(URL, headers=cabeceras, json=cuerpo, timeout=60)
    # Se apuntan el código y el texto para los CASOS 14 y 16.
    codigos.append(r.status_code)
    textos.append(r.text)
    # En un 2xx se apunta también el par (código, creado).
    if r.status_code in (200, 201):
        pares_creado.append((r.status_code, r.json().get("creado")))
    return r


def motivo(r):
    """El motivo de un error de negocio ({"detail": {"motivo": ...}})."""
    # Si no es un objeto con "motivo", devuelve None (no rompe la prueba).
    try:
        return r.json()["detail"]["motivo"]
    except (ValueError, KeyError, TypeError):
        return None


def json_o_vacio(r):
    """El cuerpo JSON de una respuesta, o {} si no es JSON (un 500, por
    ejemplo): así una prueba en negativo da un recuento, no un KeyError."""
    # Si el cuerpo no es JSON o no es un objeto, se devuelve {}.
    try:
        cuerpo = r.json()
        return cuerpo if isinstance(cuerpo, dict) else {}
    except ValueError:
        return {}


def listado():
    """GET /llamadas-del-dia: (ids de b), ids de c), cuerpo entero)."""
    # Petición real a la lista diaria, con la llave del Gate.
    r = requests.get(f"{BASE}/llamadas-del-dia", headers=AUTH_GATE, timeout=30)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    # Los ids de los apartados b) y c).
    b = {e["oportunidad_id"] for e in cuerpo["seguimientos_por_abrir"]}
    c = {e["oportunidad_id"] for e in cuerpo["seguimientos_abiertos"]}
    return b, c, cuerpo


def dia_visita():
    """Un día laborable al menos 2 días después de hoy (en Madrid)."""
    # Se empieza dos días después de hoy.
    dia = datetime.now(MADRID).date() + timedelta(days=2)
    # weekday(): sábado = 5, domingo = 6; se avanza hasta un lunes-viernes.
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return dia


def pedir_visita(token):
    """POST /visits como el Agente 2: a las 10:00 (franja de mañana)."""
    # Petición real a POST /visits con la cabecera de n8n.
    return requests.post(f"{BASE}/visits", headers=AUTH_WEBHOOK, timeout=60, json={
        "lead_token": token, "fecha": dia_visita().isoformat(), "hora": "10:00",
        "texto_cliente": "Me viene bien esa hora",
    })


# ======================================================================
# Antes de arrancar: puerto libre y fotos iniciales
# ======================================================================
# Si alguien escucha ya en el 8025, se para sin tocar nada (CLAUDE.md).
with socket.socket() as s:
    if s.connect_ex(("127.0.0.1", PUERTO)) == 0:
        print(f"El puerto {PUERTO} está ocupado por otro proceso: me paro sin tocarlo.")
        sys.exit(2)

# Restos de una ejecución anterior cortada (solo lo propio).
limpiar()
# Foto de lo ajeno AL EMPEZAR: id_base y recuento por tabla.
FOTO = foto_ajena()
print("FOTO DE LO AJENO AL EMPEZAR (tabla: id_base, filas ajenas con id <= id_base)")
for tabla, (id_base, n) in FOTO.items():
    print(f"  {tabla:16} id_base={id_base:<6} ajenas={n}")

# ======================================================================
# Servidor de verdad en un hilo de este proceso
# ======================================================================
# uvicorn.Server con la aplicación real, en el puerto propio, sin --reload.
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# daemon=True: si el script termina de golpe, el hilo no lo deja colgado.
hilo = threading.Thread(target=servidor.run, daemon=True)
hilo.start()
# Se pregunta a /health hasta que conteste, como mucho 30 s.
inicio_espera = time.time()
while time.time() - inicio_espera < 30:
    # Si /health contesta 200, el servidor ya está arriba.
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    # Todavía no escucha: se espera un cuarto de segundo y se reintenta.
    except requests.exceptions.RequestException:
        time.sleep(0.25)
# else del while: solo si pasaron los 30 s sin break.
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)

# try/finally: pase lo que pase, el finally apaga el servidor y limpia.
try:
    print(f"\nSERVIDOR (puerto {PUERTO}, arriba en {time.time() - inicio_espera:.2f} s)")
    # netstat: quién escucha en el 8025 (última columna, el PID).
    salida = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    pids = {linea.split()[-1] for linea in salida.splitlines() if f":{PUERTO} " in linea and "LISTENING" in linea}
    comprobar(f"quien escucha en el {PUERTO} es este proceso ({os.getpid()})", pids == {str(os.getpid())},
              f"({pids})")

    # Los seguimientos por abrir AJENOS de ahora (los reales de D25.16):
    # solo se miran; al final tienen que seguir igual (CASO 15).
    b_inicial, _, _ = listado()
    # SQL: estado y updated_at de esos ajenos, para compararlos al final.
    REALES = {fila[0]: fila[1:] for fila in todas(
        f"SELECT id, estado, updated_at FROM oportunidades WHERE id = ANY(%(ids)s) AND NOT ({PROPIO['oportunidades']});",
        {"ids": list(b_inicial), "patron": PATRON_EMAIL})}
    print(f"  Seguimientos por abrir ajenos al empezar: {len(REALES)} (solo se miran)")

    # ------------------------------------------------------------------
    print("\nPREPARACIÓN (todo con la marca propia)")
    # ------------------------------------------------------------------
    # La matriz (CASOS 4, 5, 6 y 7): etiqueta -> (oportunidad, código y
    # motivo esperados). Cada caso incumple UN criterio (o dos, en los
    # dobles del CASO 6), salvo los dos que cumplen todo.
    MATRIZ = {}
    # A: sin Gate, 49 h -> cumple todo (CASO 5).
    A, A_TOKEN = caso()
    retrasar(A, 49)
    MATRIZ["A cumple todo (49 h)"] = (A, 201, None)
    # Con SOLO una visita cancelada -> cumple todo (la cancelada no cuenta).
    op, _ = caso()
    retrasar(op, 49)
    insertar_visita(op, "cancelada")
    MATRIZ["solo visita cancelada"] = (op, 201, None)
    # Lead sin calcular ('nueva', sin presupuesto). Es también el doble
    # "sin presupuesto + estado distinto" del CASO 6.
    op, _ = caso(calcular=False)
    MATRIZ["'nueva' sin presupuesto (doble: + estado)"] = (op, 409, "sin_presupuesto")
    # Con Gate en 'pendiente_aprobacion' (doble del CASO 6: Gate + estado).
    op, _ = caso(gate=True)
    retrasar(op, 49)
    MATRIZ["Gate en pendiente_aprobacion (doble: + estado)"] = (op, 409, "con_gate")
    # Con Gate ya decidido (descartar -> 'perdida') por POST /gate-decisions.
    op, _ = caso(gate=True)
    retrasar(op, 49)
    r = requests.post(f"{BASE}/gate-decisions", headers=AUTH_GATE, timeout=30, json={
        "oportunidad_id": op, "decision": "descartar", "motivo": "precio", "informe": "Prueba del seguimiento."})
    assert r.status_code == 201, r.text
    MATRIZ["Gate decidido ('perdida')"] = (op, 409, "con_gate")
    # Con Gate puesto A MANO en 'presupuesto_enviado' (P1: el caso que el
    # criterio con_gate saca también de la lista).
    op, _ = caso(gate=True)
    retrasar(op, 49)
    poner_estado(op, "presupuesto_enviado")
    MATRIZ["Gate forzado a presupuesto_enviado"] = (op, 409, "con_gate")
    # Sin Gate con visita pedida por POST /visits -> 'visita_agendada'.
    op, token = caso()
    retrasar(op, 49)
    assert pedir_visita(token).status_code == 201
    MATRIZ["visita pedida por POST /visits"] = (op, 409, "estado_no_permitido")
    # Sin Gate forzado a 'perdida'.
    op, _ = caso()
    retrasar(op, 49)
    poner_estado(op, "perdida")
    MATRIZ["forzada a 'perdida'"] = (op, 409, "estado_no_permitido")
    # Con fecha_ultimo_contacto y plazo cumplido.
    op, _ = caso()
    retrasar(op, 49)
    poner_contacto(op)
    MATRIZ["con fecha_ultimo_contacto"] = (op, 409, "contacto_registrado")
    # Con visita 'solicitada' insertada sin cambiar el estado.
    op, _ = caso()
    retrasar(op, 49)
    insertar_visita(op, "solicitada")
    MATRIZ["visita 'solicitada' (por SQL)"] = (op, 409, "visita_existente")
    # Con visita 'completada' (sí cuenta: el cliente reservó y se hizo).
    op, _ = caso()
    retrasar(op, 49)
    insertar_visita(op, "completada")
    MATRIZ["visita 'completada' (por SQL)"] = (op, 409, "visita_existente")
    # Dobles del CASO 6: presupuesto recién creado (plazo no cumplido) Y
    # otro criterio; tiene que ganar el otro.
    op, _ = caso()
    poner_contacto(op)
    MATRIZ["contacto + plazo (doble)"] = (op, 409, "contacto_registrado")
    op, _ = caso()
    insertar_visita(op, "solicitada")
    MATRIZ["visita + plazo (doble)"] = (op, 409, "visita_existente")
    # Plazo no cumplido: su retraso (48 h - 10 min) se aplica al FINAL.
    PLAZO, _ = caso()
    MATRIZ["48 h - 10 min"] = (PLAZO, 409, "plazo_no_cumplido")

    # Oportunidades de los demás casos (todas sin Gate y con 49 h).
    # P5: estado puesto a mano en 'seguimiento_pendiente', sin log (CASO 9).
    P5, _ = caso()
    retrasar(P5, 49)
    poner_estado(P5, "seguimiento_pendiente")
    # R: la del 503 (CASO 10).
    R, _ = caso()
    retrasar(R, 49)
    # C: la de las 5 peticiones simultáneas (CASO 11).
    C, _ = caso()
    retrasar(C, 49)
    # K: la de la carrera a) (CASO 12): el cliente pide visita después de
    # salir en la lista.
    K, K_TOKEN = caso()
    retrasar(K, 49)
    # Tres para la carrera b), a la vez (CASO 12).
    CARRERA = []
    for _ in range(3):
        op, token = caso()
        retrasar(op, 49)
        CARRERA.append((op, token))
    # S: la de la regresión cruzada (CASO 13).
    S, S_TOKEN = caso()
    retrasar(S, 49)
    # Al FINAL de la preparación: el caso "fuera" con su margen de 10 min.
    retrasar(PLAZO, 47, 50)
    print(f"  {len(MATRIZ)} casos en la matriz; A={A}, P5={P5}, R={R}, C={C}, K={K}, S={S}, "
          f"carrera={[o for o, _ in CARRERA]}")

    # ------------------------------------------------------------------
    print("\nCASO 1 - Llave (401)")
    # ------------------------------------------------------------------
    antes = foto_op(A)
    # Sin cabecera y con cuerpo vacío: 401 (la llave va antes del 422).
    r = pedir(A, cabeceras={}, cuerpo={})
    comprobar("sin cabecera y cuerpo vacío -> 401", r.status_code == 401, f"({r.status_code})")
    # Con un X-Gate-Secret incorrecto.
    r = pedir(A, cabeceras={"X-Gate-Secret": "incorrecto"})
    comprobar("X-Gate-Secret incorrecto -> 401", r.status_code == 401, f"({r.status_code})")
    # El valor de WEBHOOK_SECRET en X-Gate-Secret: la llave del Agente 2
    # no abre esta puerta.
    r = pedir(A, cabeceras={"X-Gate-Secret": WEBHOOK_SECRET})
    comprobar("valor de WEBHOOK_SECRET en X-Gate-Secret -> 401", r.status_code == 401, f"({r.status_code})")
    # El valor de WEBHOOK_SECRET en su propia cabecera.
    r = pedir(A, cabeceras=AUTH_WEBHOOK)
    comprobar("valor de WEBHOOK_SECRET en X-Webhook-Secret -> 401", r.status_code == 401, f"({r.status_code})")
    # El 401 de /gate-decisions, para compararlo (cuerpo vacío, sin llave).
    r_gate = requests.post(f"{BASE}/gate-decisions", json={}, timeout=30)
    comprobar("cuerpo y WWW-Authenticate idénticos a los de /gate-decisions",
              r.text == r_gate.text and r.headers.get("WWW-Authenticate") == r_gate.headers.get("WWW-Authenticate")
              == "APIKey", f"({r.text})")
    comprobar("los 401 no han tocado nada", foto_op(A) == antes)

    # ------------------------------------------------------------------
    print("\nCASO 2 - Forma (422 de Pydantic)")
    # ------------------------------------------------------------------
    # Cada cuerpo mal formado, con su nombre.
    CUERPOS = [
        ("campo de más", {"oportunidad_id": A, "motivo": MOTIVO, "extra": 1}),
        ("sin oportunidad_id", {"motivo": MOTIVO}),
        ("sin motivo", {"oportunidad_id": A}),
        ("oportunidad_id 0", {"oportunidad_id": 0, "motivo": MOTIVO}),
        ("oportunidad_id -1", {"oportunidad_id": -1, "motivo": MOTIVO}),
        ("oportunidad_id 'abc'", {"oportunidad_id": "abc", "motivo": MOTIVO}),
        ("oportunidad_id 1.5", {"oportunidad_id": 1.5, "motivo": MOTIVO}),
        ("motivo 'sin_decision_post_visita' (N1, D25.13)", {"oportunidad_id": A, "motivo": "sin_decision_post_visita"}),
        ("motivo 'otro'", {"oportunidad_id": A, "motivo": "otro"}),
        ("cuerpo vacío", {}),
    ]
    # Cada uno: 422 con la forma de Pydantic (detail es una LISTA).
    for nombre, cuerpo_malo in CUERPOS:
        r = pedir(A, cuerpo=cuerpo_malo)
        comprobar(f"{nombre} -> 422 de Pydantic",
                  r.status_code == 422 and isinstance(json_o_vacio(r).get("detail"), list), f"({r.status_code})")
    comprobar("los 422 no han tocado nada", foto_op(A) == antes)

    # ------------------------------------------------------------------
    print("\nCASO 3 - 404")
    # ------------------------------------------------------------------
    # SQL: un id que no existe (el máximo + 1000).
    inexistente = uno("SELECT COALESCE(max(id), 0) + 1000 FROM oportunidades;")[0]
    # Tres ids inexistentes: uno normal y dos que no caben en INTEGER.
    for id_raro in (inexistente, 2147483648, 99999999999999999999):
        r = pedir(id_raro)
        comprobar(f"oportunidad_id {id_raro} -> 404 oportunidad_no_encontrada",
                  r.status_code == 404 and motivo(r) == "oportunidad_no_encontrada", f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 7 (antes de llamar) - foto de la lista diaria")
    # ------------------------------------------------------------------
    # Qué casos de la matriz salen en b) AHORA, antes de llamar a nadie.
    b_antes, c_antes, _ = listado()

    # ------------------------------------------------------------------
    print("\nCASOS 4 y 6 - cada 409 (y su orden), sin escribir nada")
    # ------------------------------------------------------------------
    # Resultado de cada caso de la matriz: etiqueta -> código.
    RESULTADO = {}
    for etiqueta, (op, codigo, motivo_esperado) in MATRIZ.items():
        # Los 201 (A y la cancelada) se comprueban en el CASO 5 y después.
        if codigo == 201:
            continue
        # Foto antes, petición, foto después.
        antes_op = foto_op(op)
        r = pedir(op)
        RESULTADO[etiqueta] = r.status_code
        comprobar(f"{etiqueta} -> 409 {motivo_esperado}",
                  r.status_code == 409 and motivo(r) == motivo_esperado, f"({r.status_code} {motivo(r)})")
        comprobar("    ... sin escribir nada", foto_op(op) == antes_op)

    # ------------------------------------------------------------------
    print("\nCASO 5 - 201 con todos sus efectos (A)")
    # ------------------------------------------------------------------
    # Antes: la oportunidad y su presupuesto (columnas que NO deben cambiar).
    antes_A = foto_op(A)
    # SQL: el presupuesto de A (Gate, fecha, importes y updated_at).
    presupuesto_antes = uno("SELECT requiere_aprobacion, created_at, importe_min_con_iva, importe_max_con_iva, "
                            "updated_at FROM presupuestos WHERE oportunidad_id = %s;", (A,))
    r = pedir(A)
    RESULTADO["A cumple todo (49 h)"] = r.status_code
    cuerpo = json_o_vacio(r)
    comprobar("201 y creado=true", r.status_code == 201 and cuerpo.get("creado") is True, f"({r.status_code})")
    comprobar("claves EXACTAS de la respuesta", set(cuerpo) == CLAVES_RESPUESTA, f"({sorted(cuerpo)})")
    comprobar("oportunidad_id, motivo y estado_oportunidad",
              cuerpo.get("oportunidad_id") == A and cuerpo.get("motivo") == MOTIVO
              and cuerpo.get("estado_oportunidad") == "seguimiento_pendiente")
    # La oportunidad después.
    despues_A = foto_op(A)
    comprobar("estado 'seguimiento_pendiente'", despues_A[0] == "seguimiento_pendiente")
    comprobar("updated_at ha avanzado", despues_A[1] > antes_A[1])
    comprobar("fecha_ultimo_contacto sigue NULL (pre-decisión 8)", despues_A[2] is None)
    comprobar("ninguna visita nueva", despues_A[3] == antes_A[3])
    # SQL: el presupuesto después: no ha cambiado nada.
    comprobar("presupuesto sin cambios (Gate, fecha, importes, updated_at)",
              uno("SELECT requiere_aprobacion, created_at, importe_min_con_iva, importe_max_con_iva, updated_at "
                  "FROM presupuestos WHERE oportunidad_id = %s;", (A,)) == presupuesto_antes)
    # El log de apertura.
    aperturas = logs_apertura(A)
    comprobar("exactamente UN log 'seguimiento_abierto' y un log más en total",
              len(aperturas) == 1 and despues_A[4] == antes_A[4] + 1)
    # Si no hay log (prueba en negativo), se usan valores vacíos para que
    # el script siga y dé un recuento.
    log_id, detalle, creado_log = aperturas[0] if aperturas else (None, {}, None)
    comprobar("log_id de la respuesta = id del log", cuerpo.get("log_id") == log_id and log_id is not None)
    comprobar("claves EXACTAS del detalle del log", set(detalle) == CLAVES_DETALLE, f"({sorted(detalle)})")
    # SQL: la regla tal como está en la tabla.
    regla = int(uno("SELECT valor FROM reglas_negocio WHERE clave = 'horas_seguimiento_presupuesto';")[0])
    comprobar("detalle: motivo, estado_anterior y horas de la regla",
              detalle.get("motivo") == MOTIVO and detalle.get("estado_anterior") == "presupuesto_enviado"
              and detalle.get("horas_seguimiento_presupuesto") == regla, f"(regla {regla})")
    # fecha_presupuesto del detalle = presupuestos.created_at (mismo instante).
    comprobar("detalle: fecha_presupuesto = presupuestos.created_at",
              "fecha_presupuesto" in detalle
              and datetime.fromisoformat(detalle["fecha_presupuesto"]) == presupuesto_antes[1])
    # fecha_apertura: mismo instante que el log y el desfase de Madrid de
    # ese instante (calculado con zoneinfo).
    apertura = datetime.fromisoformat(cuerpo["fecha_apertura"]) if cuerpo.get("fecha_apertura") else None
    comprobar("fecha_apertura = created_at del log, con el desfase de Madrid",
              apertura is not None and creado_log is not None and apertura == creado_log
              and apertura.utcoffset() == creado_log.astimezone(MADRID).utcoffset(), f"({cuerpo.get('fecha_apertura')})")

    # La otra que cumple todo (solo visita cancelada): 201.
    op_cancelada = MATRIZ["solo visita cancelada"][0]
    r = pedir(op_cancelada)
    RESULTADO["solo visita cancelada"] = r.status_code
    comprobar("solo visita cancelada -> 201 (la cancelada no cuenta)", r.status_code == 201, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 7 - Matriz de coherencia: 'sale en b)' <-> 'da 201'")
    # ------------------------------------------------------------------
    # Para cada caso: la foto de la lista (antes de llamar) frente al código.
    for etiqueta, (op, _, _) in MATRIZ.items():
        en_b = op in b_antes
        comprobar(f"{etiqueta}: en b) = {en_b}, código {RESULTADO.get(etiqueta)}",
                  en_b == (RESULTADO.get(etiqueta) == 201))

    # ------------------------------------------------------------------
    print("\nCASO 8 - La lista cambia: A sale de b) y entra en c)")
    # ------------------------------------------------------------------
    b_despues, c_despues, cuerpo_lista = listado()
    comprobar("antes: A en b) y no en c)", A in b_antes and A not in c_antes)
    comprobar("después: A NO en b) y SÍ en c)", A not in b_despues and A in c_despues)
    # Cuántas veces sale A en los cinco apartados juntos.
    veces = sum(1 for clave in ("gates_sin_decision", "seguimientos_por_abrir", "seguimientos_abiertos",
                                "visitas_sin_confirmar", "visitas_proximo_laborable")
                for e in cuerpo_lista[clave] if e["oportunidad_id"] == A)
    comprobar("A sale UNA sola vez en toda la lista", veces == 1, f"({veces})")
    # Su elemento de c) lleva el motivo del apartado.
    motivos_c = [e["motivo"] for e in cuerpo_lista["seguimientos_abiertos"] if e["oportunidad_id"] == A]
    comprobar("en c) con motivo 'seguimiento_abierto'", motivos_c == ["seguimiento_abierto"])

    # ------------------------------------------------------------------
    print("\nCASO 9 - 200 (repetición), sin escribir nada")
    # ------------------------------------------------------------------
    antes_rep = foto_op(A)
    r = pedir(A)
    repetido = json_o_vacio(r)
    comprobar("A otra vez -> 200 y creado=false", r.status_code == 200 and repetido.get("creado") is False,
              f"({r.status_code} {motivo(r)})")
    comprobar("mismo log_id y fecha_apertura que el 201",
              repetido.get("log_id") == cuerpo.get("log_id") and repetido.get("fecha_apertura") == cuerpo.get("fecha_apertura"))
    comprobar("0 logs nuevos y updated_at sin cambios", foto_op(A) == antes_rep)
    # P5: estado puesto a mano, sin log de apertura -> 200 con nulls.
    antes_p5 = foto_op(P5)
    r = pedir(P5)
    p5 = json_o_vacio(r)
    comprobar("P5 (estado a mano, sin log) -> 200 con log_id y fecha_apertura null",
              r.status_code == 200 and p5.get("creado") is False and p5.get("log_id") is None
              and p5.get("fecha_apertura") is None and "log_id" in p5, f"({r.status_code})")
    comprobar("P5 sin escribir nada", foto_op(P5) == antes_p5)

    # ------------------------------------------------------------------
    print("\nCASO 10 - 503 (cambios EN MEMORIA; la fila real no se toca)")
    # ------------------------------------------------------------------
    antes_R = foto_op(R)
    # Nombre falso y único; se guarda el real para restaurarlo.
    real = servicio.CLAVE_SEGUIMIENTO
    falsa = f"clave_falsa_{uuid.uuid4().hex[:8]}"
    # Lo que el servidor escriba en stderr ([AVISO]) cae en este búfer.
    stderr = io.StringIO()
    servicio.CLAVE_SEGUIMIENTO = falsa
    # try/finally: el nombre real vuelve aunque algo falle.
    try:
        # redirect_stderr: sys.stderr apunta al búfer mientras dura el bloque
        # (también para el hilo del servidor, que lo lee al escribir).
        with contextlib.redirect_stderr(stderr):
            r = pedir(R)
        # Con la configuración rota: la llave va primero (401)...
        r_llave = pedir(R, cabeceras={"X-Gate-Secret": "incorrecto"})
        # ... el 404 va antes que el 503...
        r_404 = pedir(inexistente)
        # ... y la repetición también (A ya está abierta).
        r_200 = pedir(A)
    finally:
        servicio.CLAVE_SEGUIMIENTO = real
    detalle_503 = json_o_vacio(r).get("detail", {}) if r.status_code == 503 else {}
    comprobar("clave falsa -> 503 configuracion_incompleta",
              r.status_code == 503 and detalle_503.get("motivo") == "configuracion_incompleta", f"({r.status_code})")
    comprobar("'faltan' nombra la clave falsa", any(falsa in p for p in detalle_503.get("faltan", [])),
              f"({detalle_503.get('faltan')})")
    comprobar("línea [AVISO] en stderr con la clave falsa", "[AVISO]" in stderr.getvalue() and falsa in stderr.getvalue())
    comprobar("con la configuración rota: llave mala -> 401", r_llave.status_code == 401, f"({r_llave.status_code})")
    comprobar("con la configuración rota: id inexistente -> 404 (404 antes que 503)", r_404.status_code == 404,
              f"({r_404.status_code})")
    comprobar("con la configuración rota: A ya abierta -> 200 (repetición antes que 503)", r_200.status_code == 200,
              f"({r_200.status_code})")
    # Valores fuera de rango INYECTADOS: se envuelve problema_horas en
    # memoria para que reciba ese valor en lugar del leído.
    original = servicio.problema_horas
    for valor in ("8761", "99999999"):
        # La envoltura: misma clave, valor inyectado; llama a la real.
        servicio.problema_horas = lambda clave, _leido, v=valor: original(clave, Decimal(v))
        # try/finally: la función real vuelve aunque algo falle.
        try:
            r = pedir(R)
        finally:
            servicio.problema_horas = original
        comprobar(f"regla = {valor} -> 503 (nunca 500)", r.status_code == 503 and motivo(r) == "configuracion_incompleta",
                  f"({r.status_code})")
    comprobar("los 503 no han escrito nada en R (ni log, P4)", foto_op(R) == antes_R)
    # Restaurado todo: vuelve el 201.
    r = pedir(R)
    comprobar("restaurado: R -> 201", r.status_code == 201, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 11 - Concurrencia: 5 peticiones simultáneas sobre C")
    # ------------------------------------------------------------------
    # Barrier(5): las 5 peticiones esperan a estar listas y salen a la vez.
    barrera = threading.Barrier(5)

    def a_la_vez(_):
        """Espera a las demás y lanza la petición."""
        barrera.wait()
        return pedir(C)

    # Cinco hilos, cada uno con su petición.
    with ThreadPoolExecutor(max_workers=5) as grupo:
        respuestas = list(grupo.map(a_la_vez, range(5)))
    # Los códigos, ordenados.
    cods = sorted(x.status_code for x in respuestas)
    comprobar("un 201 y cuatro 200", cods == [200, 200, 200, 200, 201], f"({cods})")
    # El mismo log_id en las cinco.
    ids_log = {json_o_vacio(x).get("log_id") for x in respuestas}
    comprobar("el MISMO log_id en las cinco", len(ids_log) == 1 and None not in ids_log, f"({ids_log})")
    comprobar("un solo log 'seguimiento_abierto'", len(logs_apertura(C)) == 1, f"({len(logs_apertura(C))})")

    # ------------------------------------------------------------------
    print("\nCASO 12 - Carrera lista -> llamada (con POST /visits)")
    # ------------------------------------------------------------------
    # a) K sale en la lista; el cliente pide visita; después llega WF3.
    b_k, _, _ = listado()
    comprobar("a) K sale en b) antes de la visita", K in b_k)
    comprobar("a) el cliente pide visita -> 201", pedir_visita(K_TOKEN).status_code == 201)
    r = pedir(K)
    comprobar("a) después, el endpoint -> 409 estado_no_permitido",
              r.status_code == 409 and motivo(r) == "estado_no_permitido", f"({r.status_code} {motivo(r)})")
    # SQL: las visitas de K (estado), para ver que siguen intactas.
    visitas_k = [f[0] for f in todas("SELECT estado FROM visitas WHERE oportunidad_id = %s;", (K,))]
    comprobar("a) sin log de apertura y la visita intacta", logs_apertura(K) == [] and visitas_k == ["solicitada"],
              f"({visitas_k})")
    # b) POST /visits y el endpoint A LA VEZ, en tres oportunidades.
    for op, token in CARRERA:
        # Barrier(2): las dos peticiones salen a la vez.
        barrera2 = threading.Barrier(2)

        def visita_a_la_vez(t=token, b=barrera2):
            """Espera a la otra y pide la visita."""
            b.wait()
            return pedir_visita(t)

        def seguimiento_a_la_vez(o=op, b=barrera2):
            """Espera a la otra y abre el seguimiento."""
            b.wait()
            return pedir(o)

        # Dos hilos: uno por petición.
        with ThreadPoolExecutor(max_workers=2) as grupo:
            f_visita = grupo.submit(visita_a_la_vez)
            f_seg = grupo.submit(seguimiento_a_la_vez)
            r_visita, r_seg = f_visita.result(), f_seg.result()
        # SQL: el estado final y las visitas activas de esa oportunidad.
        estado_final = uno("SELECT estado FROM oportunidades WHERE id = %s;", (op,))[0]
        activas = uno("SELECT count(*) FROM visitas WHERE oportunidad_id = %s AND estado IN ('solicitada', 'confirmada');",
                      (op,))[0]
        n_logs = len(logs_apertura(op))
        # Quién ganó: si el endpoint dio 201, fue primero.
        ganador = "seguimiento" if r_seg.status_code == 201 else "visita"
        comprobar(f"b) op {op} (primero: {ganador}): visita 201, seguimiento 201 o 409 estado_no_permitido",
                  r_visita.status_code == 201 and (r_seg.status_code == 201 or motivo(r_seg) == "estado_no_permitido"),
                  f"(visita {r_visita.status_code}, seguimiento {r_seg.status_code})")
        comprobar(f"b) op {op}: final coherente ('visita_agendada', 1 visita activa, log solo si dio 201)",
                  estado_final == "visita_agendada" and activas == 1 and n_logs == (1 if r_seg.status_code == 201 else 0),
                  f"({estado_final}, activas {activas}, logs {n_logs})")

    # ------------------------------------------------------------------
    print("\nCASO 13 - Regresión cruzada (/leads/session y POST /visits)")
    # ------------------------------------------------------------------
    r = pedir(S)
    comprobar("S -> 201", r.status_code == 201, f"({r.status_code})")
    # GET /leads/session: lo que verá el router de n8n (C5, P12).
    sesion = requests.get(f"{BASE}/leads/session/{S_TOKEN}", headers=AUTH_WEBHOOK, timeout=30).json()
    comprobar("GET /leads/session -> estado 'seguimiento_pendiente' y con presupuesto",
              sesion.get("estado_oportunidad") == "seguimiento_pendiente" and sesion["presupuesto"]["existe"] is True)
    # El cliente pide visita desde el chat: se acepta desde ese estado.
    comprobar("POST /visits desde 'seguimiento_pendiente' -> 201", pedir_visita(S_TOKEN).status_code == 201)
    # SQL: el estado de S después de la visita.
    comprobar("S pasa a 'visita_agendada'", uno("SELECT estado FROM oportunidades WHERE id = %s;", (S,))[0]
              == "visita_agendada")
    _, c_s, _ = listado()
    comprobar("y deja de salir en c)", S not in c_s)

    # ------------------------------------------------------------------
    print("\nCASO 14 - Ningún importe ni email; 201 <-> creado; ningún 500")
    # ------------------------------------------------------------------
    texto_total = "\n".join(textos)
    comprobar("ninguna respuesta contiene 'importe'", "importe" not in texto_total)
    comprobar("ninguna respuesta contiene un email propio", not any(e in texto_total for e in EMAILS))
    comprobar("201 <-> creado=true y 200 <-> creado=false en todas",
              all((c == 201) == (creado is True) for c, creado in pares_creado), f"({len(pares_creado)} respuestas 2xx)")
    comprobar("ninguna respuesta 500 del endpoint", 500 not in codigos, f"({codigos.count(500)} de {len(codigos)})")

finally:
    # Apagado ordenado del servidor propio (el hilo de ESTE proceso: no se
    # mata ningún proceso, ni ajeno ni propio).
    servidor.should_exit = True
    hilo.join(timeout=15)

    # Logs del manejador global (500) de esta ruta: no llevan marca propia;
    # se borran por id EXACTO solo si son tantos como respuestas 500.
    n_500 = codigos.count(500)
    # SQL: los logs error_no_controlado de esta ruta escritos durante la
    # prueba (id > el id_base de logs de la foto inicial).
    logs_500 = todas("SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' "
                     "AND detalle->>'ruta' = '/create-followup-task' ORDER BY id;", (FOTO["logs"][0],))
    print(f"\n  Respuestas 500: {n_500}; logs error_no_controlado de /create-followup-task: {[f[0] for f in logs_500]}")
    comprobar("los logs de 500 de /create-followup-task son exactamente los esperados", len(logs_500) == n_500,
              f"({len(logs_500)} logs, {n_500} respuestas 500)")
    # Se borran solo si son exactamente tantos como respuestas 500.
    if logs_500 and len(logs_500) == n_500:
        # SQL: se borran por sus ids exactos.
        cur.execute("DELETE FROM logs WHERE id = ANY(%s);", ([f[0] for f in logs_500],))
        print(f"  Borrados por id exacto: {[f[0] for f in logs_500]}")

    # Limpieza de lo propio y comprobación de que no queda nada.
    limpiar()
    # SQL: cuántos clientes propios quedan (tienen que ser 0).
    restos = uno(f"SELECT count(*) FROM clientes WHERE {PROPIO['clientes']};", {"patron": PATRON_EMAIL})[0]
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")
    comprobar("no queda ningún dato de prueba", restos == 0)

    # CASO 15: lo ajeno, con los mismos id_base que al empezar.
    print("\nCASO 15 - NO TOCA NADA AJENO (mismo id_base que al empezar)")
    for tabla, (id_base, antes_n) in FOTO.items():
        # SQL: filas ajenas con id <= id_base, ahora.
        ahora_n = uno(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                      {"base": id_base, "patron": PATRON_EMAIL})[0]
        comprobar(f"{tabla}: ajenas con id <= {id_base}: {antes_n} -> {ahora_n}", ahora_n == antes_n)
    # Los seguimientos por abrir reales siguen exactamente igual (D25.16).
    # "REALES" solo existe si el servidor llegó a arrancar; globals() es el
    # diccionario de las variables de este archivo.
    if "REALES" in globals():
        # SQL: estado y updated_at de esos ajenos, ahora.
        ahora_reales = {fila[0]: fila[1:] for fila in todas(
            "SELECT id, estado, updated_at FROM oportunidades WHERE id = ANY(%s);", (list(REALES),))}
        comprobar(f"los {len(REALES)} seguimientos por abrir ajenos siguen igual (estado y updated_at)",
                  ahora_reales == REALES)
    cn.close()

# ----------------------------------------------------------------------
# Resumen y código de salida (1 si algo falló, para la suite).
# ----------------------------------------------------------------------
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con algún fallo: se listan y se sale con código 1.
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
