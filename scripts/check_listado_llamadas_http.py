"""
Verificación por HTTP REAL de GET /llamadas-del-dia (plan:
docs/Plan_Endpoint_Listado_WF3.txt, sección 6.3, CASOS 1-17).

Qué hace, en pocas palabras:
  1. Levanta el servidor de verdad (uvicorn.Server en un hilo de este
     proceso, SIN --reload, en el puerto 8024: nunca el 8000, que es el
     uvicorn de n8n de Gabi). Si el 8024 está ocupado, se para sin tocar
     nada.
  2. Crea sus propios casos como lo hará n8n (POST /leads, POST
     /calculate-estimate, POST /visits y POST /gate-decisions), y por SQL,
     SOLO sobre filas propias, lo que la API no puede producir: mover
     presupuestos.created_at hacia atrás, el estado seguimiento_pendiente,
     fecha_ultimo_contacto, visitas en horas límite y el lead "antiguo" sin
     la clave 'contacto'.
  3. Prueba el contrato: llave, forma y minimización, reglas y día, cada
     apartado dentro y fuera del plazo, contacto del lead, fechas con el
     desfase de Madrid, orden, 503, solo lectura, repetición.
  4. Borra SOLO lo suyo y comprueba que no ha tocado nada ajeno.

La lista incluye TODO lo que hay en la base de datos (también los datos
reales). Las comprobaciones miran SOLO los elementos con oportunidad_id
propio; nunca se exige nada sobre la lista entera, salvo la forma (claves
exactas) y que no salga ningún email ni importe.

Márgenes por HTTP: "dentro" = 1 minuto más que el plazo; "fuera" = 10
minutos menos, aplicado al final de la preparación (ver allí por qué). El límite exacto se prueba en check_listado_llamadas_service.

Marca propia: emails http-listado-<8 caracteres>@example.com (dominio
reservado), tokens uuid4. El valor de los secretos NUNCA se imprime.
"""

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
# io: para capturar stderr durante las peticiones del 503.
import io
# socket: para comprobar que nadie escucha en el puerto.
import socket
# sys: sys.path, la salida estándar y el código de salida.
import sys
# threading: el servidor corre en un hilo de este proceso.
import threading
# time: para esperar a que el servidor arranque.
import time
# uuid: marcas y tokens únicos de cada ejecución.
import uuid
# Fechas. "time" se renombra a hora_del_dia para no chocar con el módulo time.
from datetime import date, datetime, time as hora_del_dia, timedelta
# Decimal: para inyectar valores de reglas en memoria.
from decimal import Decimal
# Path: rutas independientes del sistema operativo.
from pathlib import Path
# ZoneInfo: la zona Europe/Madrid, con el cambio de hora.
from zoneinfo import ZoneInfo

# La raíz del repositorio, para que "import app..." funcione.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Tildes bien en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: preparar, mirar y limpiar la base de datos.
import psycopg2
# Json: diccionario de Python -> valor JSONB.
from psycopg2.extras import Json
# requests: peticiones HTTP reales.
import requests
# uvicorn: el servidor.
import uvicorn

# Los valores del .env (solo en cabeceras; nunca se imprimen).
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET
# El módulo del servicio, para los cambios EN MEMORIA del CASO 13 (el
# servidor corre en este mismo proceso, así que los ve).
from app.services import listado_llamadas_service as servicio

# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------
# Puerto propio (nunca el 8000) y direcciones.
PUERTO = 8024
BASE = f"http://127.0.0.1:{PUERTO}"
URL = f"{BASE}/llamadas-del-dia"
# Cabeceras: la de n8n (para crear los datos) y la del Gate (la que exige
# este endpoint).
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# Patrón SQL (LIKE) de los clientes de ESTE script.
PATRON_EMAIL = "http-listado-%@example.com"
# Zona de Madrid y una marca corta de esta ejecución (para nombres únicos).
MADRID = ZoneInfo("Europe/Madrid")
RUN = uuid.uuid4().hex[:6]
# Las claves EXACTAS de cada nivel, copiadas del PLAN (1.5), no del código.
CLAVES_RAIZ = {"generado_en", "dia_visitas", "reglas", "gates_sin_decision", "seguimientos_por_abrir",
               "seguimientos_abiertos", "visitas_sin_confirmar", "visitas_proximo_laborable"}
# Las de reglas y las de cada elemento de a), b) y c).
CLAVES_REGLAS = {"horas_recordatorio_gate", "horas_seguimiento_presupuesto"}
CLAVES_OPORTUNIDAD = {"oportunidad_id", "motivo", "tipo_reforma", "contacto", "fecha_presupuesto"}
# Las de cada elemento de d) y e), y las del contacto (sin email).
CLAVES_VISITA = {"oportunidad_id", "motivo", "tipo_reforma", "contacto", "estado_visita",
                 "fecha_visita", "fecha_solicitud_visita"}
CLAVES_CONTACTO = {"nombre", "telefono", "origen"}
# Apartados de oportunidades y de visitas, con su motivo esperado.
APARTADOS_OP = {"gates_sin_decision": "gate_sin_decision", "seguimientos_por_abrir": "seguimiento_por_abrir",
                "seguimientos_abiertos": "seguimiento_abierto"}
APARTADOS_VI = {"visitas_sin_confirmar": "visita_sin_confirmar",
                "visitas_proximo_laborable": "visita_proximo_laborable"}
# Lo que NO debe salir nunca, en ningún nivel (plan, 1.5): claves que
# contengan alguno de estos textos.
TEXTOS_PROHIBIDOS = ("email", "importe", "iva", "umbral", "lead_token", "lead_id", "cliente_id",
                     "presupuesto_id", "visita_id", "m2", "fotos", "texto_cliente", "informe", "estado_oportunidad")

# ----------------------------------------------------------------------
# Contadores
# ----------------------------------------------------------------------
# Contador de comprobaciones correctas y lista de títulos de las que fallan.
ok = 0
fallos = []
# Código de CADA respuesta de /llamadas-del-dia (para contar los 500).
codigos = []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si condicion es verdadera, [FALLO] si no; se cuenta."""
    # global: se modifica la variable ok del archivo, no una copia local.
    global ok
    # Verdadera: se suma una correcta y se enseña [OK].
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta su título en la lista de fallos y se enseña [FALLO].
    else:
        fallos.append(titulo)
        print(f"    [FALLO] {titulo}  {detalle}")


# ----------------------------------------------------------------------
# Conexión propia (preparar, mirar y limpiar)
# ----------------------------------------------------------------------
# Conexión directa a la base de datos (no la del pool del servidor) y su
# cursor, el objeto con el que se envían las consultas.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una fila; el commit cierra la transacción de lectura para
    que la siguiente consulta vea lo último que escribió el servidor."""
    # SQL: el SELECT que llega en el parámetro sql (cada llamada lleva su
    # propio "# SQL" encima); params rellena los %s de forma segura.
    cur.execute(sql, params)
    # La primera fila, como tupla (o None); el commit cierra la transacción.
    fila = cur.fetchone()
    cn.commit()
    return fila


def consultar_todas(sql, params=()):
    """SELECT de varias filas, con el mismo commit de cierre."""
    # SQL: el SELECT que llega en el parámetro sql (cada llamada lleva su
    # propio "# SQL" encima); params rellena los %s de forma segura.
    cur.execute(sql, params)
    # Todas las filas, como lista de tuplas; el commit cierra la transacción.
    filas = cur.fetchall()
    cn.commit()
    return filas


def escribir(sql, params=()):
    """Una escritura SOBRE FILAS PROPIAS, confirmada (el servidor la tiene que ver)."""
    # SQL: el UPDATE o INSERT que llega en el parámetro sql (siempre sobre
    # filas propias; cada llamada lleva su "# SQL" encima). Se confirma
    # (commit) para que el servidor lo vea.
    cur.execute(sql, params)
    cn.commit()


# ----------------------------------------------------------------------
# Qué es "propio": todo lo que cuelga de los clientes de prueba
# ----------------------------------------------------------------------
# Subconsultas encadenadas: clientes propios -> sus leads -> sus
# oportunidades. %(patron)s se rellena con PATRON_EMAIL.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPORTUNIDADES = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# La condición de filas PROPIAS de cada tabla.
PROPIO = {
    "clientes": f"id IN ({SQL_CLIENTES})",
    "leads": f"id IN ({SQL_LEADS})",
    "oportunidades": f"id IN ({SQL_OPORTUNIDADES})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "visitas": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPORTUNIDADES})",
}
# Orden de borrado: primero lo que apunta a otras tablas.
ORDEN_BORRADO = ["logs", "decisiones_gate", "visitas", "presupuestos", "oportunidades", "leads", "clientes"]


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas."""
    # Una tabla cada vez, en el orden de ORDEN_BORRADO.
    for tabla in ORDEN_BORRADO:
        # SQL: borra las filas de esa tabla que cumplen la condición de PROPIO.
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    # Un solo commit para todos los borrados.
    cn.commit()


def foto_ajena():
    """Para cada tabla: (id_base, filas ajenas con id <= id_base)."""
    # Diccionario tabla -> (id_base, ajenas); se rellena en el bucle.
    foto = {}
    for tabla in ORDEN_BORRADO:
        # SQL: el id más alto ahora (0 si la tabla está vacía).
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        id_base = cur.fetchone()[0]
        # SQL: cuántas filas NO propias hay con id <= id_base.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                    {"patron": PATRON_EMAIL, "base": id_base})
        foto[tabla] = (id_base, cur.fetchone()[0])
    # Se cierra la transacción de lectura y se devuelve la foto.
    cn.commit()
    return foto


def recontar_ajeno(foto):
    """Vuelve a contar lo ajeno con los MISMOS id_base: (antiguas, nuevas)."""
    # Diccionario tabla -> (antiguas, nuevas); se rellena en el bucle.
    resultado = {}
    for tabla, (id_base, _) in foto.items():
        # Los valores de los %(...)s: el patrón propio y el id_base de esa tabla.
        params = {"patron": PATRON_EMAIL, "base": id_base}
        # SQL: ajenas antiguas (deben ser las mismas) y nuevas (solo información).
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});", params)
        antiguas = cur.fetchone()[0]
        # SQL: ajenas NUEVAS (id > id_base): las escribió otro proceso; solo se informa.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id > %(base)s AND NOT ({PROPIO[tabla]});", params)
        resultado[tabla] = (antiguas, cur.fetchone()[0])
    # Se cierra la transacción de lectura.
    cn.commit()
    return resultado


def foto_propia():
    """Recuentos de filas PROPIAS por tabla, y updated_at y
    fecha_ultimo_contacto de las oportunidades propias (CASO 14)."""
    # Diccionario tabla -> número de filas propias.
    recuentos = {}
    for tabla in ORDEN_BORRADO:
        # SQL: cuántas filas propias tiene esa tabla.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
        recuentos[tabla] = cur.fetchone()[0]
    # SQL: las dos fechas que escribiría una operación, de cada oportunidad propia.
    cur.execute(f"SELECT id, updated_at, fecha_ultimo_contacto FROM oportunidades "
                f"WHERE {PROPIO['oportunidades']} ORDER BY id;", {"patron": PATRON_EMAIL})
    # Todas las filas: (id, updated_at, fecha_ultimo_contacto) de cada una.
    fechas = cur.fetchall()
    cn.commit()
    return recuentos, fechas


# ----------------------------------------------------------------------
# Ayudas HTTP
# ----------------------------------------------------------------------
def listado(cabeceras=AUTH_GATE):
    """GET /llamadas-del-dia. TODAS las peticiones pasan por aquí, para
    contar los 500 al final."""
    # GET real con las cabeceras pedidas; se anota el código de la respuesta.
    r = requests.get(URL, headers=cabeceras, timeout=60)
    codigos.append(r.status_code)
    return r


def cuerpo(r):
    """JSON de la respuesta, o {} si no es JSON (así un fallo no revienta el script)."""
    # r.json() convierte el cuerpo en un diccionario; si no es JSON, lanza
    # ValueError y se devuelve {}.
    try:
        return r.json()
    except ValueError:
        return {}


# Emails propios creados (para comprobar que no salen en la respuesta).
EMAILS = []


def lead_http(etiqueta, estructural=False, email=None, nombre=None, telefono=None):
    """Crea un lead por POST /leads. Devuelve (oportunidad_id, lead_token,
    nombre, telefono)."""
    # Token único (clave de idempotencia de POST /leads) y email propio, que se
    # guarda en EMAILS para buscarlo después en las respuestas.
    token = str(uuid.uuid4())
    email = email or f"http-listado-{uuid.uuid4().hex[:8]}@example.com"
    EMAILS.append(email)
    # Nombre único de esta ejecución (lleva RUN).
    nombre = nombre or f"Listado {etiqueta} {RUN}"
    # Un teléfono distinto por lead: "6" + 8 cifras aleatorias.
    telefono = telefono or "6" + str(uuid.uuid4().int)[:8]
    # Petición real a POST /leads con la cabecera de n8n.
    r = requests.post(f"{BASE}/leads", headers=AUTH_WEBHOOK, timeout=30, json={
        "nombre": nombre, "email": email, "telefono": telefono, "tipo_reforma": "bano", "m2": 6,
        "nivel_acabados": "medio", "incluye_cambios_estructurales": estructural, "fotos": [],
        "lead_token": token,
    })
    # Sin el lead no tiene sentido seguir.
    assert r.status_code == 201, r.text
    # Se devuelven el id de la oportunidad y los datos de contacto enviados.
    return r.json()["oportunidad_id"], token, nombre, telefono


def calcular(op):
    """POST /calculate-estimate; devuelve el estado en que queda."""
    # Petición real a POST /calculate-estimate con la cabecera de n8n.
    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH_WEBHOOK,
                      json={"oportunidad_id": op}, timeout=30)
    # Tiene que dar 200; si no, el script se para con el error.
    assert r.status_code == 200, r.text
    return r.json()["status"]


def caso(etiqueta, gate):
    """Lead + cálculo: con Gate (cambios estructurales) queda en
    'pendiente_aprobacion'; sin Gate, en 'presupuesto_enviado'.
    Devuelve (oportunidad_id, token, nombre, telefono)."""
    # Lead propio; con Gate si gate es True (cambios estructurales).
    datos = lead_http(etiqueta, estructural=gate)
    # Cálculo, y comprobación del estado en que queda.
    estado = calcular(datos[0])
    assert estado == ("pendiente_aprobacion" if gate else "presupuesto_enviado"), estado
    return datos


def retrasar_presupuesto(op, horas, minutos=0):
    """Pone presupuestos.created_at = now() - (horas, minutos) en una
    oportunidad PROPIA."""
    # SQL: mueve la fecha del presupuesto hacia atrás; el AND con PROPIO
    # garantiza que solo puede tocar filas propias.
    escribir(f"UPDATE presupuestos SET created_at = now() - %(intervalo)s "
             f"WHERE oportunidad_id = %(op)s AND {PROPIO['presupuestos']};",
             {"intervalo": timedelta(hours=horas, minutes=minutos), "op": op, "patron": PATRON_EMAIL})


def poner_estado(op, estado):
    """Cambia el estado de una oportunidad PROPIA (lo que la API aún no hace)."""
    # SQL: UPDATE limitado a filas propias.
    escribir(f"UPDATE oportunidades SET estado = %(estado)s WHERE id = %(op)s AND {PROPIO['oportunidades']};",
             {"estado": estado, "op": op, "patron": PATRON_EMAIL})


def insertar_visita(op, fecha, estado):
    """Inserta una visita en una oportunidad PROPIA (comprobado antes)."""
    # Comprobación de propiedad: la oportunidad tiene que ser propia.
    assert consultar(f"SELECT count(*) FROM oportunidades WHERE id = %(op)s AND {PROPIO['oportunidades']};",
                     {"op": op, "patron": PATRON_EMAIL})[0] == 1
    # SQL: la visita, con un texto fijo.
    escribir("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
             "VALUES (%s, %s, %s, 'prueba del listado');", (op, fecha, estado))


def todas_las_claves(objeto):
    """Todas las claves de un JSON, en cualquier nivel."""
    # Un diccionario: sus claves y, recursivamente, las de sus valores.
    if isinstance(objeto, dict):
        # Las claves de este nivel, más las de cada valor (la función se
        # llama a sí misma: recursión).
        claves = set(objeto)
        for valor in objeto.values():
            claves |= todas_las_claves(valor)
        return claves
    # Una lista: las claves de cada elemento.
    if isinstance(objeto, list):
        # Conjunto vacío, al que se suman las claves de cada elemento.
        claves = set()
        for elemento in objeto:
            claves |= todas_las_claves(elemento)
        return claves
    # Un valor simple (texto, número, null) no tiene claves.
    return set()


def puerto_ocupado(puerto):
    """True si algo acepta conexiones en 127.0.0.1:puerto."""
    # Se intenta abrir una conexión TCP; connect_ex devuelve 0 si alguien
    # escucha. El with cierra el socket al terminar.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ----------------------------------------------------------------------
# Fechas CALCULADAS en cada ejecución (nunca caducan)
# ----------------------------------------------------------------------
def siguiente_laborable(hoy):
    """Propia de la PRUEBA (no se importa del servicio, que es lo probado)."""
    # Se empieza por mañana y, mientras sea sábado o domingo (weekday() >= 5),
    # se pasa al día siguiente.
    dia = hoy + timedelta(days=1)
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    # El primer día de lunes a viernes encontrado.
    return dia


# Ayuda de proximos_cambios: el desfase de un día.
def desfase(dia):
    """Desfase de Madrid a mediodía de ese día."""
    # combine une el día con las 12:00 en la zona de Madrid; utcoffset() da su
    # desfase (+1 h o +2 h). A mediodía, lejos de la hora del cambio (02:00-03:00).
    return datetime.combine(dia, hora_del_dia(12, 0), tzinfo=MADRID).utcoffset()


def proximos_cambios(hoy, cuantos=2):
    """Los próximos días en que cambia el desfase de Madrid, buscados con zoneinfo."""
    # Lista de días de cambio encontrados, y el día por el que va la búsqueda.
    cambios, dia = [], hoy
    # Se avanza día a día hasta encontrar los que se piden.
    while len(cambios) < cuantos:
        siguiente = dia + timedelta(days=1)
        # Si el desfase de mañana es distinto del de hoy, mañana hay cambio.
        if desfase(siguiente) != desfase(dia):
            cambios.append(siguiente)
        # Se pasa al día siguiente; al terminar, se devuelven los cambios.
        dia = siguiente
    return cambios


# Ayuda para elegir las fechas de las visitas del CASO 11.
def primer_lunes_desde(dia):
    """El primer lunes en ese día o después."""
    # weekday(): lunes = 0. (7 - weekday) % 7 son los días que faltan hasta el
    # lunes (0 si ya es lunes).
    return dia + timedelta(days=(7 - dia.weekday()) % 7)


# "Hoy" según el reloj de Postgres (el mismo que usa el servidor), en Madrid.
# SQL: now() de Postgres, pasado a Madrid y quedándose solo con la fecha.
HOY = consultar("SELECT now();")[0].astimezone(MADRID).date()
# El próximo laborable, con la función de la PRUEBA.
DIA = siguiente_laborable(HOY)
# Los límites del próximo laborable, calculados por la prueba.
INICIO = datetime.combine(DIA, hora_del_dia(0, 0), tzinfo=MADRID)
FIN = datetime.combine(DIA + timedelta(days=1), hora_del_dia(0, 0), tzinfo=MADRID)
# Dos lunes a los dos lados de un cambio de hora (CASO 11). Si el primero
# coincidiera con DIA (pasa si hoy es el sábado anterior al cambio), se
# usa el lunes siguiente, que está en el mismo lado del cambio.
# Los dos próximos cambios de hora y el primer lunes tras cada uno.
CAMBIO_1, CAMBIO_2 = proximos_cambios(HOY)
LUNES_1, LUNES_2 = primer_lunes_desde(CAMBIO_1), primer_lunes_desde(CAMBIO_2)
# El lunes 1 no puede ser el próximo laborable: sus visitas tienen que
# salir en d), no en e).
if LUNES_1 == DIA:
    LUNES_1 += timedelta(days=7)

# ======================================================================
# Antes de nada: el puerto tiene que estar LIBRE
# ======================================================================
# Si el puerto está ocupado, se para sin tocar nada (CLAUDE.md).
if puerto_ocupado(PUERTO):
    print(f"ABORTO: el puerto {PUERTO} está ocupado por otro proceso. No ejecuto nada ni mato nada.")
    sys.exit(2)

# Restos de una ejecución anterior cortada (solo lo propio).
# limpiar() borra restos de una ejecución anterior cortada; foto_ajena()
# anota cuántas filas ajenas hay ahora, para compararlo al final.
limpiar()
FOTO = foto_ajena()
# Se enseña la foto de lo ajeno, tabla por tabla, y las fechas calculadas.
print("=" * 78)
print("FOTO DE LO AJENO AL EMPEZAR (tabla: id_base, filas ajenas con id <= id_base)")
print("=" * 78)
# Una línea por tabla.
for tabla, (id_base, n) in FOTO.items():
    print(f"  {tabla:16} id_base={id_base:<6} ajenas={n}")
# Y las fechas calculadas para esta ejecución.
print(f"  Hoy {HOY}; próximo laborable {DIA}; visitas de desfase los lunes {LUNES_1} y {LUNES_2}")

# ======================================================================
# Servidor de verdad en un hilo de este proceso
# ======================================================================
# uvicorn.Server con la aplicación real, en el puerto propio, sin --reload.
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# daemon=True: si el script termina de golpe, el hilo no lo deja colgado.
hilo = threading.Thread(target=servidor.run, daemon=True)
hilo.start()
# Hora de inicio de la espera.
inicio_espera = time.time()
# Se pregunta a /health hasta que conteste, como mucho 30 s.
while time.time() - inicio_espera < 30:
    # Si /health contesta 200, el servidor ya está arriba: se sale del bucle.
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
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio_espera:.2f} s")

# try/finally: pase lo que pase, el finally apaga el servidor y limpia.
try:
    # ------------------------------------------------------------------
    print("\nPREPARACIÓN (todo con la marca propia)")
    # ------------------------------------------------------------------
    # a) Gates: dentro (24 h + 1 min), fuera (24 h - 1 min) y decidido.
    # G_DENTRO: con Gate y presupuesto de hace 24 h + 1 min.
    G_DENTRO = caso("G-dentro", gate=True)
    retrasar_presupuesto(G_DENTRO[0], 24, 1)
    # El retraso de G_FUERA se aplica al FINAL de la preparación (ver allí).
    G_FUERA = caso("G-fuera", gate=True)
    # G_DECIDIDO: con Gate, de hace 30 h, y ya decidido (descartar) por POST
    # /gate-decisions: su estado pasa a 'perdida' y no debe salir.
    G_DECIDIDO = caso("G-decidido", gate=True)
    retrasar_presupuesto(G_DECIDIDO[0], 30)
    r = requests.post(f"{BASE}/gate-decisions", headers=AUTH_GATE, timeout=30, json={
        "oportunidad_id": G_DECIDIDO[0], "decision": "descartar", "motivo": "precio",
        "informe": "prueba del listado"})
    assert r.status_code == 201, r.text
    # b) Seguimientos por abrir.
    # S_DENTRO: sin Gate, de hace 48 h + 1 min.
    S_DENTRO = caso("S-dentro", gate=False)
    retrasar_presupuesto(S_DENTRO[0], 48, 1)
    # El retraso de S_FUERA se aplica al FINAL de la preparación (ver allí).
    S_FUERA = caso("S-fuera", gate=False)
    # S_CONTACTO: de hace 48 h + 1 min, pero con fecha_ultimo_contacto puesta.
    S_CONTACTO = caso("S-contacto", gate=False)
    retrasar_presupuesto(S_CONTACTO[0], 48, 1)
    # SQL: pone fecha_ultimo_contacto = now() en esa oportunidad PROPIA.
    escribir(f"UPDATE oportunidades SET fecha_ultimo_contacto = now() WHERE id = %(op)s "
             f"AND {PROPIO['oportunidades']};", {"op": S_CONTACTO[0], "patron": PATRON_EMAIL})
    # Con una visita 'solicitada' insertada SIN cambiar el estado (defensa, P5).
    S_VISITA = caso("S-visita", gate=False)
    retrasar_presupuesto(S_VISITA[0], 48, 1)
    # SQL (en insertar_visita): visita 'solicitada' el martes siguiente al lunes 2.
    insertar_visita(S_VISITA[0], datetime.combine(LUNES_2 + timedelta(days=1), hora_del_dia(10), tzinfo=MADRID),
                    "solicitada")
    # Con una visita solo 'cancelada': cuenta como "sin visita".
    S_CANCELADA = caso("S-cancelada", gate=False)
    retrasar_presupuesto(S_CANCELADA[0], 48, 1)
    # SQL (en insertar_visita): una visita solo 'cancelada'.
    insertar_visita(S_CANCELADA[0], datetime.combine(LUNES_1, hora_del_dia(10), tzinfo=MADRID), "cancelada")
    # Base del plazo: oportunidad vieja y presupuesto nuevo (fuera), y al revés (dentro).
    S_OP_VIEJA = caso("S-op-vieja", gate=False)
    # SQL: la oportunidad PROPIA se hace "vieja" (creada hace 72 h)...
    escribir(f"UPDATE oportunidades SET created_at = now() - interval '72 hours' WHERE id = %(op)s "
             f"AND {PROPIO['oportunidades']};", {"op": S_OP_VIEJA[0], "patron": PATRON_EMAIL})
    # ...pero su presupuesto es de hace 1 h: no debe salir.
    retrasar_presupuesto(S_OP_VIEJA[0], 1)
    # S_PRES_VIEJO: oportunidad recién creada y presupuesto de hace 49 h: sí sale.
    S_PRES_VIEJO = caso("S-pres-viejo", gate=False)
    retrasar_presupuesto(S_PRES_VIEJO[0], 49)
    # Orden: tres más, de 50, 60 y 70 h.
    S_50, S_60, S_70 = caso("S-50", False), caso("S-60", False), caso("S-70", False)
    retrasar_presupuesto(S_50[0], 50)
    retrasar_presupuesto(S_60[0], 60)
    retrasar_presupuesto(S_70[0], 70)
    # Contacto del lead y no de la ficha: dos leads con el MISMO email; el
    # segundo (B2) en b).
    # Email compartido por B1 y B2. B1 crea la ficha de clientes con SU nombre y
    # teléfono; B2 llega con otros (la ficha no se sobrescribe, D2).
    EMAIL_B = f"http-listado-{uuid.uuid4().hex[:8]}@example.com"
    B1 = lead_http("B1", email=EMAIL_B, nombre=f"Ficha Primera {RUN}", telefono="600000101")
    B2 = lead_http("B2", email=EMAIL_B, nombre=f"Solicitud Segunda {RUN}", telefono="600000102")
    # B2 se calcula (queda sin Gate) y se atrasa a 48 h + 5 min: sale en b).
    assert calcular(B2[0]) == "presupuesto_enviado"
    retrasar_presupuesto(B2[0], 48, 5)
    # Lead "antiguo" sin 'contacto' (por SQL, con su ficha completa).
    # Email propio del lead antiguo, guardado en EMAILS para buscarlo después.
    EMAIL_L = f"http-listado-{uuid.uuid4().hex[:8]}@example.com"
    EMAILS.append(EMAIL_L)
    # Nombre y teléfono de SU FICHA: no deben aparecer nunca en la respuesta.
    NOMBRE_FICHA_L, TEL_FICHA_L = f"Ficha Antigua {RUN}", "600000103"
    # SQL: el cliente del lead antiguo, con su ficha completa; RETURNING id
    # devuelve su id.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                (NOMBRE_FICHA_L, EMAIL_L, TEL_FICHA_L))
    cliente_l = cur.fetchone()[0]
    # SQL: su lead, con los datos de la reforma pero SIN la clave 'contacto'
    # (como los leads de antes del 2026-09-25) y sin fotos; Json(...) convierte
    # el diccionario en JSONB; RETURNING id devuelve el id del lead.
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_listado', '[]'::jsonb, %s) RETURNING id;",
                (cliente_l, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                  "incluye_cambios_estructurales": False})))
    lead_l = cur.fetchone()[0]
    # SQL: su oportunidad, en 'nueva' (el único estado desde el que se calcula).
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano') RETURNING id;", (lead_l,))
    OP_L = cur.fetchone()[0]
    # commit: el servidor tiene que ver estas tres filas.
    cn.commit()
    # Se calcula por la API (queda sin Gate) y se atrasa a 48 h + 5 min.
    assert calcular(OP_L) == "presupuesto_enviado"
    retrasar_presupuesto(OP_L, 48, 5)
    # c) Seguimiento abierto (nadie escribe ese estado aún: por SQL).
    # C_ABIERTO: presupuesto de hace 1 h, puesto en 'seguimiento_pendiente'.
    C_ABIERTO = caso("C", gate=False)
    retrasar_presupuesto(C_ABIERTO[0], 1)
    poner_estado(C_ABIERTO[0], "seguimiento_pendiente")
    # d) Visitas pedidas por el chat (POST /visits) a los dos lados de un
    #    cambio de hora; una pasada (SQL, P9); y una confirmada de un Gate.
    # D_1 y D_2: sin Gate; cada uno pide su visita por POST /visits, uno en cada
    # lunes (a los dos lados del cambio de hora), a las 08:30.
    D_1, D_2 = caso("D1", gate=False), caso("D2", gate=False)
    for (op, token, _, _), lunes in ((D_1, LUNES_1), (D_2, LUNES_2)):
        # Petición real a POST /visits con el lead_token de ese caso.
        r = requests.post(f"{BASE}/visits", headers=AUTH_WEBHOOK, timeout=30, json={
            "lead_token": token, "fecha": lunes.isoformat(), "hora": "08:30", "texto_cliente": "prueba listado"})
        assert r.status_code == 201, r.text
    # D_PASADA: visita 'solicitada' de hace 3 días (por SQL; la API no deja
    # pedir fechas pasadas).
    D_PASADA = caso("D-pasada", gate=False)
    poner_estado(D_PASADA[0], "visita_agendada")
    insertar_visita(D_PASADA[0], datetime.combine(HOY - timedelta(days=3), hora_del_dia(10), tzinfo=MADRID),
                    "solicitada")
    # D_GATE_CONF: Gate decidido con visita_acordada: crea una visita
    # 'confirmada' (no debe salir en d).
    D_GATE_CONF = caso("D-gate", gate=True)
    r = requests.post(f"{BASE}/gate-decisions", headers=AUTH_GATE, timeout=30, json={
        "oportunidad_id": D_GATE_CONF[0], "decision": "visita_acordada", "fecha": LUNES_1.isoformat(),
        "hora": "09:30", "informe": "prueba del listado"})
    assert r.status_code == 201, r.text
    # e) Visitas en los extremos del próximo laborable (SQL, cada una en su
    #    oportunidad por el índice de una visita activa por oportunidad).
    # Diccionario etiqueta -> oportunidad_id de las visitas de e).
    VISITAS_E = {}
    # Cada caso: (etiqueta, instante de la visita, estado de la visita).
    for etiqueta, instante, estado in (
        ("E-inicio", INICIO, "solicitada"),
        ("E-0900", datetime.combine(DIA, hora_del_dia(9), tzinfo=MADRID), "solicitada"),
        ("E-1000", datetime.combine(DIA, hora_del_dia(10), tzinfo=MADRID), "confirmada"),
        ("E-ultimo", FIN - timedelta(seconds=1), "confirmada"),
        ("E-antes", INICIO - timedelta(seconds=1), "confirmada"),
        ("E-despues", FIN, "confirmada"),
        ("E-cancelada", datetime.combine(DIA, hora_del_dia(10), tzinfo=MADRID), "cancelada"),
    ):
        # Una oportunidad propia por visita, en 'visita_agendada' (como tras pedir
        # una visita), y su visita insertada por SQL.
        op = caso(etiqueta, gate=False)[0]
        poner_estado(op, "visita_agendada")
        insertar_visita(op, instante, estado)
        VISITAS_E[etiqueta] = op
    # Los dos casos "fuera", AL FINAL y con 10 minutos de margen: preparar
    # todo lo demás lleva más de un minuto, y con 1 minuto de margen (la
    # primera versión) podían cruzar el límite antes de consultar, o entre
    # las dos llamadas del CASO 15 (pasó en la prueba en negativo N2, y no se
    # repitió al reproducirla: condición de carrera).
    retrasar_presupuesto(G_FUERA[0], 23, 50)
    retrasar_presupuesto(S_FUERA[0], 47, 50)
    # Cuántos leads propios se han creado (solo información).
    print(f"  {len(EMAILS)} leads propios creados")

    # ==================================================================
    print("\nCASO 1 - Llave")
    # ==================================================================
    # Sin ninguna cabecera.
    r = listado({})
    comprobar("sin cabecera -> 401", r.status_code == 401, f"({r.status_code})")
    comprobar("cuerpo del 401 = {\"detail\": \"No autorizado\"}", cuerpo(r) == {"detail": "No autorizado"})
    comprobar("cabecera WWW-Authenticate: APIKey", r.headers.get("WWW-Authenticate") == "APIKey")
    # El 401 de GET /gate-avisos, para comparar: tiene que ser idéntico.
    # Sin cabecera: el 401 de /gate-avisos (la ruta del id da igual).
    r_av = requests.get(f"{BASE}/gate-avisos/1", timeout=20)
    comprobar("401 idéntico al de GET /gate-avisos (cuerpo y WWW-Authenticate)",
              r.status_code == r_av.status_code and r.content == r_av.content
              and r.headers.get("WWW-Authenticate") == r_av.headers.get("WWW-Authenticate"))
    # Llave incorrecta, y el valor de WEBHOOK_SECRET en cada una de las dos cabeceras.
    comprobar("X-Gate-Secret incorrecto -> 401", listado({"X-Gate-Secret": "no-es-el-secreto"}).status_code == 401)
    comprobar("valor de WEBHOOK_SECRET en X-Gate-Secret -> 401",
              listado({"X-Gate-Secret": WEBHOOK_SECRET}).status_code == 401)
    comprobar("valor de WEBHOOK_SECRET en X-Webhook-Secret -> 401",
              listado({"X-Webhook-Secret": WEBHOOK_SECRET}).status_code == 401)

    # La respuesta buena, que usan los CASOS 2 a 12.
    # Con la llave buena: 200.
    R = listado()
    comprobar("con X-Gate-Secret -> 200", R.status_code == 200, f"({R.status_code})")
    # Si no es un 200, el cuerpo se trata como vacío: las comprobaciones
    # siguientes FALLAN en vez de romper el script (así una prueba en
    # negativo da siempre un recuento, no una excepción a medias).
    L = cuerpo(R) if R.status_code == 200 else {}

    # Para cada apartado, sus elementos por oportunidad_id (así se busca un
    # caso propio con "op in A").
    def propios(apartado):
        """Los elementos de ese apartado, como {oportunidad_id: elemento}."""
        return {e["oportunidad_id"]: e for e in L.get(apartado, [])}

    # A, B, C, D y E: los cinco apartados, en el orden del contrato.
    A, B, C, D, E = (propios(n) for n in ("gates_sin_decision", "seguimientos_por_abrir", "seguimientos_abiertos",
                                          "visitas_sin_confirmar", "visitas_proximo_laborable"))

    # ==================================================================
    print("\nCASO 2 - Forma y minimización")
    # ==================================================================
    # Las claves de la raíz y de reglas, y que las cinco sean listas.
    comprobar("claves de la raíz exactas", set(L) == CLAVES_RAIZ, f"({sorted(L)})")
    comprobar("claves de reglas exactas", set(L.get("reglas", {})) == CLAVES_REGLAS)
    comprobar("las cinco son listas", all(isinstance(L.get(n), list) for n in (*APARTADOS_OP, *APARTADOS_VI)))
    # Las claves de CADA elemento (también los ajenos: la forma vale para todos).
    comprobar("claves exactas en cada elemento de a), b) y c), y en su contacto",
              all(set(e) == CLAVES_OPORTUNIDAD and set(e["contacto"]) == CLAVES_CONTACTO
                  for n in APARTADOS_OP for e in L.get(n, [])))
    comprobar("claves exactas en cada elemento de d) y e), y en su contacto",
              all(set(e) == CLAVES_VISITA and set(e["contacto"]) == CLAVES_CONTACTO
                  for n in APARTADOS_VI for e in L.get(n, [])))
    # Cada elemento lleva el motivo de su apartado.
    comprobar("motivo de cada elemento = el de su apartado",
              all(e["motivo"] == m for n, m in {**APARTADOS_OP, **APARTADOS_VI}.items() for e in L.get(n, [])))
    # Ninguna clave, en ningún nivel, contiene un texto prohibido.
    claves = todas_las_claves(L)
    comprobar("ninguna clave prohibida en ningún nivel (email, importes, ids...)",
              not any(t in c for c in claves for t in TEXTOS_PROHIBIDOS), f"({len(claves)} claves distintas)")
    # Ningún email propio aparece en el texto de la respuesta.
    comprobar("ningún email propio en el texto de la respuesta",
              not any(e.lower() in R.text.lower() for e in EMAILS), f"({len(EMAILS)} emails buscados)")
    # SQL: los importes con IVA de los presupuestos PROPIOS, como texto; UNION
    # quita los repetidos. Ninguno puede aparecer en la respuesta.
    importes = [f[0] for f in consultar_todas(
        f"SELECT importe_min_con_iva::text FROM presupuestos WHERE {PROPIO['presupuestos']} UNION "
        f"SELECT importe_max_con_iva::text FROM presupuestos WHERE {PROPIO['presupuestos']};",
        {"patron": PATRON_EMAIL}) if f[0]]
    comprobar("ningún importe propio en el texto de la respuesta",
              importes != [] and not any(i in R.text for i in importes), f"({len(importes)} importes buscados)")

    # ==================================================================
    print("\nCASO 3 - Reglas y día")
    # ==================================================================
    # SQL: los valores actuales de las dos reglas, como enteros.
    reglas_bd = dict(consultar_todas("SELECT clave, valor::int FROM reglas_negocio WHERE clave = ANY(%s);",
                                     (list(CLAVES_REGLAS),)))
    comprobar("reglas = los valores de reglas_negocio", L.get("reglas") == reglas_bd, f"({L.get('reglas')})")
    # Fecha de relleno si falta: la comprobación falla, el script sigue.
    generado = datetime.fromisoformat(L.get("generado_en", "2000-01-01T00:00:00+00:00"))
    comprobar("generado_en con el desfase de Madrid de ese instante",
              generado.utcoffset() == generado.astimezone(MADRID).utcoffset(), f"({L.get('generado_en')})")
    # El próximo laborable que calculó la prueba.
    comprobar("dia_visitas = el próximo laborable calculado por la prueba", L.get("dia_visitas") == DIA.isoformat(),
              f"({L.get('dia_visitas')})")

    # ==================================================================
    print("\nCASO 4 - a) Gates sin decisión registrada")
    # ==================================================================
    # Dentro, fuera por 10 minutos, y fuera por estar decidido.
    comprobar("Gate de 24 h + 1 min -> dentro", G_DENTRO[0] in A)
    comprobar("Gate de 24 h - 10 min -> fuera", G_FUERA[0] not in A)
    comprobar("Gate de 30 h ya decidido (descartar) -> fuera", G_DECIDIDO[0] not in A)
    # El elemento de G_DENTRO, y la fecha de su presupuesto leída por SQL.
    e = A.get(G_DENTRO[0], {})
    pres = consultar("SELECT created_at FROM presupuestos WHERE oportunidad_id = %s;", (G_DENTRO[0],))[0]
    # Su elemento completo: motivo, tipo, contacto de la solicitud y la misma
    # fecha de presupuesto (se comparan instantes).
    comprobar("su elemento: motivo, tipo, contacto de la solicitud y fecha_presupuesto",
              e.get("motivo") == "gate_sin_decision" and e.get("tipo_reforma") == "bano"
              and e.get("contacto") == {"nombre": G_DENTRO[2], "telefono": G_DENTRO[3], "origen": "solicitud"}
              and e.get("fecha_presupuesto") and datetime.fromisoformat(e["fecha_presupuesto"]) == pres)

    # ==================================================================
    print("\nCASO 5 - b) Seguimientos por abrir")
    # ==================================================================
    # Plazo: dentro por 1 minuto, fuera por 10.
    comprobar("48 h + 1 min -> dentro", S_DENTRO[0] in B)
    comprobar("48 h - 10 min -> fuera", S_FUERA[0] not in B)
    # Las dos condiciones extra: sin contacto registrado y sin visita.
    comprobar("48 h + 1 min con fecha_ultimo_contacto -> fuera", S_CONTACTO[0] not in B)
    comprobar("48 h + 1 min con visita 'solicitada' (estado sin cambiar) -> fuera", S_VISITA[0] not in B)
    comprobar("48 h + 1 min con visita solo 'cancelada' -> dentro", S_CANCELADA[0] in B)
    # La base del plazo es el presupuesto, no la oportunidad.
    comprobar("base del plazo: oportunidad de hace 72 h y presupuesto de hace 1 h -> fuera", S_OP_VIEJA[0] not in B)
    comprobar("base del plazo: oportunidad de ahora y presupuesto de hace 49 h -> dentro", S_PRES_VIEJO[0] in B)
    # Ninguna oportunidad de otro apartado se cuela en b).
    comprobar("ninguna oportunidad de a) ni de c) en b)",
              not any(op in B for op in (G_DENTRO[0], C_ABIERTO[0])))

    # ==================================================================
    print("\nCASO 6 - c) Seguimientos abiertos")
    # ==================================================================
    # Sin plazo: sale aunque el presupuesto sea de hace 1 h.
    comprobar("seguimiento_pendiente con presupuesto de hace 1 h -> dentro (sin plazo)", C_ABIERTO[0] in C)
    comprobar("  con motivo 'seguimiento_abierto'", C.get(C_ABIERTO[0], {}).get("motivo") == "seguimiento_abierto")

    # ==================================================================
    print("\nCASO 7 - d) Visitas sin confirmar")
    # ==================================================================
    # Las dos visitas pedidas por el chat y la de fecha pasada: dentro.
    comprobar("visita pedida por POST /visits (lunes 1) -> dentro", D_1[0] in D)
    comprobar("visita pedida por POST /visits (lunes 2) -> dentro", D_2[0] in D)
    comprobar("visita 'solicitada' con fecha pasada -> dentro (P9)", D_PASADA[0] in D)
    # La confirmada (de un Gate) y la cancelada: fuera.
    comprobar("visita 'confirmada' de un Gate (POST /gate-decisions) -> fuera", D_GATE_CONF[0] not in D)
    comprobar("visita 'cancelada' -> fuera", VISITAS_E["E-cancelada"] not in D)
    # SQL: fecha_propuesta y created_at de la visita de D_1, para compararlas.
    fila = consultar("SELECT fecha_propuesta, created_at FROM visitas WHERE oportunidad_id = %s;", (D_1[0],))
    # Su elemento: estado, motivo y las dos fechas (instantes iguales).
    e = D.get(D_1[0], {})
    comprobar("su elemento: estado_visita, fecha_visita y fecha_solicitud_visita",
              e.get("estado_visita") == "solicitada" and e.get("motivo") == "visita_sin_confirmar"
              and datetime.fromisoformat(e.get("fecha_visita", "2000-01-01T00:00:00+00:00")) == fila[0]
              and datetime.fromisoformat(e.get("fecha_solicitud_visita", "2000-01-01T00:00:00+00:00")) == fila[1])

    # ==================================================================
    print(f"\nCASO 8 - e) Visitas del próximo laborable ({DIA})")
    # ==================================================================
    # Dentro del próximo laborable: 00:00, 09:00, 10:00 y 23:59:59.
    comprobar("inicio exacto (00:00 de Madrid), 'solicitada' -> dentro", VISITAS_E["E-inicio"] in E)
    comprobar("09:00 'solicitada' -> dentro", VISITAS_E["E-0900"] in E)
    comprobar("10:00 'confirmada' -> dentro", VISITAS_E["E-1000"] in E)
    comprobar("23:59:59 'confirmada' -> dentro", VISITAS_E["E-ultimo"] in E)
    # Fuera: el segundo de antes, el 00:00 del día siguiente y la cancelada.
    comprobar("23:59:59 del día anterior -> fuera", VISITAS_E["E-antes"] not in E)
    comprobar("00:00 del día siguiente -> fuera", VISITAS_E["E-despues"] not in E)
    comprobar("10:00 'cancelada' -> fuera", VISITAS_E["E-cancelada"] not in E)
    # Las 'solicitada' del próximo laborable no se repiten en d).
    comprobar("las 'solicitada' del próximo laborable NO salen en d) (P4)",
              VISITAS_E["E-inicio"] not in D and VISITAS_E["E-0900"] not in D)
    # El estado de la visita llega bien en cada una.
    comprobar("estado_visita de cada una", E.get(VISITAS_E["E-0900"], {}).get("estado_visita") == "solicitada"
              and E.get(VISITAS_E["E-1000"], {}).get("estado_visita") == "confirmada")

    # ==================================================================
    print("\nCASO 9 - Sin repeticiones (D25.2)")
    # ==================================================================
    # SQL: los ids de las oportunidades propias; se convierten en un conjunto.
    mios = set(consultar_todas(f"SELECT id FROM oportunidades WHERE {PROPIO['oportunidades']};",
                               {"patron": PATRON_EMAIL}))
    mios = {f[0] for f in mios}
    # Todos los oportunidad_id de la respuesta y, de ellos, los propios.
    todos = [e["oportunidad_id"] for n in (*APARTADOS_OP, *APARTADOS_VI) for e in L.get(n, [])]
    propios_en_lista = [op for op in todos if op in mios]
    # Si un id saliera dos veces, el conjunto (set) tendría menos elementos que la lista.
    comprobar("ningún oportunidad_id propio sale dos veces en toda la respuesta",
              len(propios_en_lista) == len(set(propios_en_lista)), f"({len(propios_en_lista)} elementos propios)")

    # ==================================================================
    print("\nCASO 10 - El contacto es el del LEAD, no el de la ficha")
    # ==================================================================
    # B2: su contacto es el de SU solicitud, no el de la ficha (que es de B1).
    e = B.get(B2[0], {})
    comprobar("B2 (mismo email que B1): nombre y teléfono de SU solicitud, origen 'solicitud'",
              e.get("contacto") == {"nombre": B2[2], "telefono": B2[3], "origen": "solicitud"}, f"({e.get('contacto')})")
    # Ni el nombre ni el teléfono de B1 aparecen en el texto.
    comprobar("ni el nombre ni el teléfono de la ficha (los de B1) aparecen",
              B1[2] not in R.text and B1[3] not in R.text)
    # El lead sin 'contacto': todo null y origen no_disponible...
    e = B.get(OP_L, {})
    comprobar("lead sin 'contacto': nombre y telefono null, origen 'no_disponible'",
              e.get("contacto") == {"nombre": None, "telefono": None, "origen": "no_disponible"}, f"({e.get('contacto')})")
    # ...y nada de su ficha en el texto.
    comprobar("nada de la ficha de ese lead en la respuesta",
              NOMBRE_FICHA_L not in R.text and TEL_FICHA_L not in R.text)

    # ==================================================================
    print("\nCASO 11 - Fechas con el desfase de Madrid")
    # ==================================================================
    # fecha_presupuesto de G_DENTRO: su desfase tiene que ser el de Madrid en
    # ese instante. Si falta, una fecha de relleno hace fallar la comprobación.
    e = A.get(G_DENTRO[0], {})
    fp = datetime.fromisoformat(e.get("fecha_presupuesto", "2000-01-01T00:00:00+00:00"))
    comprobar("fecha_presupuesto con el desfase de Madrid de ese instante",
              fp.utcoffset() == fp.astimezone(MADRID).utcoffset(), f"({e.get('fecha_presupuesto')})")
    # Las fechas de las dos visitas de d) (o una de relleno si faltan).
    f1 = datetime.fromisoformat(D.get(D_1[0], {}).get("fecha_visita", "2000-01-01T00:00:00+00:00"))
    f2 = datetime.fromisoformat(D.get(D_2[0], {}).get("fecha_visita", "2000-01-01T00:00:00+00:00"))
    # Lo esperado: 08:30 de cada lunes, en hora de Madrid, con su desfase.
    esperado_1 = datetime.combine(LUNES_1, hora_del_dia(8, 30), tzinfo=MADRID)
    esperado_2 = datetime.combine(LUNES_2, hora_del_dia(8, 30), tzinfo=MADRID)
    # Cada fecha: el mismo instante y el mismo desfase que lo esperado.
    comprobar(f"lunes {LUNES_1} 08:30 con su desfase ({esperado_1.utcoffset()})",
              f1 == esperado_1 and f1.utcoffset() == esperado_1.utcoffset(), f"({f1.isoformat()})")
    comprobar(f"lunes {LUNES_2} 08:30 con su desfase ({esperado_2.utcoffset()})",
              f2 == esperado_2 and f2.utcoffset() == esperado_2.utcoffset(), f"({f2.isoformat()})")
    # Y los dos desfases son distintos (+01:00 y +02:00).
    comprobar("los dos desfases son distintos", f1.utcoffset() != f2.utcoffset())

    # ==================================================================
    print("\nCASO 12 - Orden (lo más antiguo primero)")
    # ==================================================================
    # Orden relativo de los tres propios de 50, 60 y 70 h en b).
    orden_b = [op for op in B if op in (S_50[0], S_60[0], S_70[0])]
    comprobar("b): 70 h, 60 h, 50 h", orden_b == [S_70[0], S_60[0], S_50[0]], f"({orden_b})")
    # Todas las fechas de b), en el orden en que llegan: tienen que estar ordenadas.
    fechas_b = [datetime.fromisoformat(e["fecha_presupuesto"]) for e in L.get("seguimientos_por_abrir", [])]
    comprobar("b) entero ordenado por fecha_presupuesto ascendente", fechas_b == sorted(fechas_b))
    # Orden relativo de las visitas propias de e).
    orden_e = [op for op in E if op in VISITAS_E.values()]
    comprobar("e): 00:00, 09:00, 10:00, 23:59:59",
              orden_e == [VISITAS_E[k] for k in ("E-inicio", "E-0900", "E-1000", "E-ultimo")], f"({orden_e})")

    # ==================================================================
    print("\nCASO 13 - 503 (cambios EN MEMORIA; la fila real no se toca)")
    # ==================================================================
    # SQL: el id más alto de logs ahora, y una marca única para la clave falsa.
    id_base_logs = consultar("SELECT COALESCE(max(id), 0) FROM logs;")[0]
    marca = uuid.uuid4().hex
    # Para cada una de las dos claves: se cambia EN MEMORIA por una falsa.
    for nombre_constante in ("CLAVE_SEGUIMIENTO", "CLAVE_RECORDATORIO_GATE"):
        # Se guarda el nombre real y se pone el falso (getattr y setattr leen y
        # escriben un atributo por su nombre).
        real = getattr(servicio, nombre_constante)
        falsa = f"clave_falsa_{marca}"
        setattr(servicio, nombre_constante, falsa)
        # Se captura stderr mientras duran las peticiones.
        captura, stderr_real = io.StringIO(), sys.stderr
        sys.stderr = captura
        # Dos peticiones: con la llave buena (503) y con una mala (401).
        try:
            r = listado()
            r_llave = listado({"X-Gate-Secret": "otra-cosa"})
        # Siempre: se devuelve stderr a su sitio y el nombre real a la clave.
        finally:
            sys.stderr = stderr_real
            setattr(servicio, nombre_constante, real)
        # detail del cuerpo; {} si no hay.
        d = cuerpo(r).get("detail", {})
        # 503 con su motivo, "faltan" con la clave falsa, 401 antes que 503, la
        # línea [AVISO] y ningún secreto en stderr.
        comprobar(f"{nombre_constante} falsa -> 503 configuracion_incompleta",
                  r.status_code == 503 and isinstance(d, dict) and d.get("motivo") == "configuracion_incompleta",
                  f"({r.status_code})")
        # "faltan" nombra la clave falsa.
        comprobar("  'faltan' nombra la clave falsa", isinstance(d, dict) and any(falsa in f for f in d.get("faltan", [])))
        # Con la llave mala, 401: la llave se comprueba antes que las reglas.
        comprobar("  llave mal + configuración rota -> 401 (la llave va primero)", r_llave.status_code == 401)
        # La línea [AVISO] llegó a stderr, y sin ningún secreto.
        comprobar("  línea [AVISO] en stderr con la clave",
                  "[AVISO] GET /llamadas-del-dia" in captura.getvalue() and falsa in captura.getvalue())
        comprobar("  stderr sin ningún secreto",
                  GATE_SECRET not in captura.getvalue() and WEBHOOK_SECRET not in captura.getvalue())
    # Valores fuera de rango (P6 corregida): se envuelve la validación EN
    # MEMORIA para que reciba ese valor en lugar del leído.
    # Se guarda la función real de validación.
    original_validar = servicio.validar_reglas_listado
    # Dos valores fuera de rango: 8761 y 99999999.
    for valor in ("8761.00", "99999999.00"):
        # Envoltorio: copia lo leído, pone el valor fuera de rango y llama a la
        # validación real (valor=valor fija el valor de ESTA vuelta del bucle).
        def inyectar(leidas, valor=valor):
            leidas = dict(leidas)
            leidas[servicio.CLAVE_SEGUIMIENTO] = Decimal(valor)
            return original_validar(leidas)
        # Se pone el envoltorio en lugar de la función real y se captura stderr.
        servicio.validar_reglas_listado = inyectar
        captura, stderr_real = io.StringIO(), sys.stderr
        sys.stderr = captura
        # Una petición con la llave buena.
        try:
            r = listado()
        # Siempre: stderr y la función real, a su sitio.
        finally:
            sys.stderr = stderr_real
            servicio.validar_reglas_listado = original_validar
        # Tiene que ser 503 con el rango en "faltan", nunca 500.
        d = cuerpo(r).get("detail", {})
        comprobar(f"horas_seguimiento_presupuesto = {valor} -> 503 (nunca 500)",
                  r.status_code == 503 and isinstance(d, dict)
                  and any("entre 1 y 8760" in f for f in d.get("faltan", [])), f"({r.status_code})")
    # SQL: ningún log nuevo que contenga la marca de la clave falsa.
    comprobar("ningún log escrito con la clave falsa",
              consultar("SELECT count(*) FROM logs WHERE id > %s AND detalle::text LIKE %s;",
                        (id_base_logs, f"%{marca}%"))[0] == 0)
    # Con todo restaurado, vuelve el 200.
    comprobar("tras restaurar, vuelve el 200", listado().status_code == 200)

    # ==================================================================
    print("\nCASO 14 - Solo lectura: nada cambia al consultar")
    # ==================================================================
    # Foto de lo propio ANTES.
    antes = foto_propia()
    # Una tanda: 200, 401 y 503 (clave falsa).
    # 200 y 401.
    listado()
    listado({})
    # 503 con una clave falsa EN MEMORIA, con stderr capturado; después todo se
    # restaura.
    real = servicio.CLAVE_SEGUIMIENTO
    servicio.CLAVE_SEGUIMIENTO = f"clave_falsa_{marca}"
    # stderr se cambia por un StringIO (y se guarda el real) para que el
    # [AVISO] del 503 no ensucie la salida.
    sys.stderr, stderr_real = io.StringIO(), sys.stderr
    try:
        listado()
    finally:
        # Siempre: stderr y el nombre real de la clave, a su sitio.
        sys.stderr = stderr_real
        servicio.CLAVE_SEGUIMIENTO = real
    # Otro 200, y foto de lo propio DESPUÉS: tiene que ser igual.
    listado()
    despues = foto_propia()
    comprobar("recuentos de filas propias (7 tablas, logs incluidos) iguales", antes[0] == despues[0],
              f"({antes[0]} -> {despues[0]})")
    comprobar("updated_at y fecha_ultimo_contacto de las oportunidades propias iguales", antes[1] == despues[1])

    # ==================================================================
    print("\nCASO 15 - Repetición: el mismo cuerpo salvo generado_en")
    # ==================================================================
    def solo_propio(c):
        """reglas, dia_visitas y, de cada apartado, SOLO los elementos propios:
        un dato real que cruce su plazo entre las dos llamadas no es asunto
        de esta prueba (nunca se exige nada sobre la lista entera)."""
        # Diccionario con reglas, día y, por cada apartado, solo los elementos propios.
        return {"reglas": c.get("reglas"), "dia_visitas": c.get("dia_visitas"),
                **{n: [e for e in c.get(n, []) if e["oportunidad_id"] in mios]
                   for n in (*APARTADOS_OP, *APARTADOS_VI)}}
    # Dos llamadas seguidas, filtradas igual: tienen que coincidir.
    r1, r2 = solo_propio(cuerpo(listado())), solo_propio(cuerpo(listado()))
    comprobar("dos llamadas seguidas dan lo mismo (reglas, día y elementos propios)",
              r1 == r2 and r1["reglas"] is not None, f"({sum(len(v) for k, v in r1.items() if isinstance(v, list))} propios)")

    # ==================================================================
    print("\nCASO 16 - Ningún 500")
    # ==================================================================
    # codigos tiene el código de TODAS las peticiones a /llamadas-del-dia.
    comprobar("ninguna respuesta 500 de /llamadas-del-dia", 500 not in codigos,
              f"({len(codigos)} peticiones, códigos {sorted(set(codigos))})")

finally:
    # Apagado ordenado del servidor propio (el hilo de este proceso).
    servidor.should_exit = True
    hilo.join(timeout=15)

    # Logs del manejador global (500) de esta ruta: no llevan marca propia;
    # se borran por id EXACTO solo si son tantos como respuestas 500 ha
    # recibido este script (regla de CLAUDE.md).
    # Cuántas respuestas 500 recibió este script.
    n_500 = codigos.count(500)
    # SQL: los logs error_no_controlado de esta ruta escritos durante la prueba
    # (id > el id_base de logs de la foto inicial).
    logs_500 = consultar_todas("SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' "
                               "AND detalle->>'ruta' = '/llamadas-del-dia' ORDER BY id;", (FOTO["logs"][0],))
    print(f"\n  Respuestas 500: {n_500}; logs error_no_controlado de /llamadas-del-dia: {[f[0] for f in logs_500]}")
    comprobar("los logs de 500 de /llamadas-del-dia son exactamente los esperados", len(logs_500) == n_500,
              f"({len(logs_500)} logs, {n_500} respuestas 500)")
    # Se borran solo si son exactamente tantos como respuestas 500.
    if logs_500 and len(logs_500) == n_500:
        # SQL: se borran por sus ids exactos.
        cur.execute("DELETE FROM logs WHERE id = ANY(%s);", ([f[0] for f in logs_500],))
        cn.commit()
        print(f"  Borrados por id exacto: {[f[0] for f in logs_500]}")

    # Limpieza de lo propio y comprobación de que no queda nada.
    limpiar()
    # SQL: cuántos clientes propios quedan (tienen que ser 0).
    restos = consultar(f"SELECT count(*) FROM clientes WHERE {PROPIO['clientes']};", {"patron": PATRON_EMAIL})[0]
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")
    comprobar("no queda ningún dato de prueba", restos == 0)

    # CASO 17: lo ajeno, con los mismos id_base que al empezar.
    print("\nCASO 17 - NO TOCA NADA AJENO (mismo id_base que al empezar)")
    # Se recuenta lo ajeno con los mismos id_base y se compara con la foto inicial.
    recuento = recontar_ajeno(FOTO)
    for tabla, (id_base, antes_n) in FOTO.items():
        # Las ajenas antiguas tienen que ser las mismas; las nuevas solo se enseñan.
        antiguas, nuevas = recuento[tabla]
        comprobar(f"{tabla}: ajenas con id <= {id_base}: {antes_n} -> {antiguas}", antiguas == antes_n,
                  f"(ajenas NUEVAS durante la prueba, solo información: {nuevas})")
    # Se cierra la conexión propia.
    cn.close()

# Resumen final y código de salida: 1 si algo falló (para la suite).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con algún fallo: se listan y se sale con código 1.
if fallos:
    # Título de cada comprobación fallida, una por línea.
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    # Código de salida 1: la suite lo cuenta como fallo sin leer la salida.
    sys.exit(1)
