"""
Verificación por HTTP REAL de GET /gate-avisos/{oportunidad_id} (plan:
docs/Plan_Endpoint_Aviso_Gate.txt, sección 4.1, CASOS 1-13).

Qué hace, en pocas palabras:
  1. Levanta el servidor de verdad (uvicorn.Server en un hilo de este
     proceso, SIN --reload, en el puerto 8023: nunca el 8000, que es el
     uvicorn de n8n de Gabi). Si el 8023 está ocupado, se para sin tocar
     nada.
  2. Crea sus propios casos como lo hará n8n (POST /leads, POST
     /calculate-estimate y POST /gate-decisions), salvo el lead "antiguo"
     sin la clave 'contacto', que se crea por SQL porque POST /leads ya no
     puede crearlo así.
  3. Prueba el contrato entero: llave, forma, 404 y 409, que nunca sale
     nada de un caso sin Gate, contacto del lead y no de la ficha,
     decisión, fotos, fechas con el desfase de Madrid, importes exactos,
     minimización, solo lectura y repetición.
  4. Borra SOLO lo suyo y comprueba que no ha tocado nada ajeno.

Marca propia de los datos: emails http-aviso-<8 caracteres>@example.com
(dominio reservado), con tokens uuid4 nuevos en cada ejecución.

"NO TOCA NADA AJENO": al empezar se cuentan, en cada tabla, las filas que
NO son de los clientes de prueba y que ya existían (id <= id_base). Al
terminar se vuelven a contar con el mismo id_base: si el script hubiera
borrado algo ajeno, el recuento bajaría -> FALLO. Lo que escriba otro
proceso durante la prueba tiene id > id_base y solo se enseña.

Las fechas de las visitas se CALCULAN en cada ejecución (primer lunes
08:30 tras el próximo cambio de hora de Europe/Madrid y tras el siguiente;
plan, CASO 8): nunca caducan.

El valor de los secretos NUNCA se imprime: solo se usan en las cabeceras.
"""

# ----------------------------------------------------------------------
# Imports: piezas de otras bibliotecas que este script necesita
# ----------------------------------------------------------------------
# json: para convertir los cuerpos y los detalles de los logs a texto.
import json
# re: para sacar los 8 estados del CHECK de docs/schema_actual.sql.
import re
# socket: para comprobar, antes de nada, que nadie escucha en el 8023.
import socket
# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# threading: para ejecutar el servidor en un hilo de este mismo proceso.
import threading
# time: para esperar a que el servidor arranque.
import time
# uuid: para generar tokens únicos en cada ejecución.
import uuid
# date/datetime/timedelta: fechas. "time" se renombra a hora_del_dia para
# no chocar con el módulo time de arriba.
from datetime import date, datetime, time as hora_del_dia, timedelta
# Path: rutas de archivos independientes del sistema operativo.
from pathlib import Path
# ZoneInfo: la zona horaria oficial Europe/Madrid (con cambio de hora).
from zoneinfo import ZoneInfo

# La raíz del repositorio es la carpeta padre de scripts/. Se añade al
# principio de sys.path para que "import app..." encuentre el paquete app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: para preparar el lead antiguo, leer y limpiar la base de datos.
import psycopg2
# Json: convierte un diccionario de Python en un valor JSONB.
from psycopg2.extras import Json
# requests: para hacer peticiones HTTP reales al servidor.
import requests
# uvicorn: el servidor que ejecuta la aplicación FastAPI.
import uvicorn

# Los valores del .env (app.config los carga). Se usan solo en cabeceras;
# nunca se imprimen.
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET

# ----------------------------------------------------------------------
# Constantes de la prueba
# ----------------------------------------------------------------------
# Puerto del servidor de prueba (plan, B6). Nunca el 8000.
PUERTO = 8023
# Dirección base de todas las peticiones.
BASE = f"http://127.0.0.1:{PUERTO}"
# Cabecera de n8n para /leads y /calculate-estimate.
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
# Cabecera del Gate, la que exige GET /gate-avisos (y POST /gate-decisions).
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# Patrón SQL (LIKE) que identifica a los clientes de ESTE script.
PATRON_EMAIL = "http-aviso-%@example.com"
# Zona horaria de Madrid, para las fechas esperadas.
MADRID = ZoneInfo("Europe/Madrid")
# Las claves EXACTAS de cada nivel de la respuesta (plan, 1.5), copiadas
# del PLAN y no del código, para no depender de lo que se está probando.
CLAVES_RAIZ = {"oportunidad_id", "estado_oportunidad", "fecha_solicitud", "contacto",
               "reforma", "fotos", "presupuesto", "decision"}
CLAVES_CONTACTO = {"nombre", "email", "telefono", "origen"}
CLAVES_REFORMA = {"tipo_reforma", "m2", "nivel_acabados", "incluye_cambios_estructurales"}
CLAVES_PRESUPUESTO = {"fecha_presupuesto", "motivo_gate", "iva_pct_aplicado", "importe_min_con_iva",
                      "importe_max_con_iva", "importe_min_sin_iva", "importe_max_sin_iva",
                      "umbral_gate_vigente"}
CLAVES_DECISION = {"decision", "fecha_decision", "motivo", "fecha_visita"}
# Lo que NO debe salir nunca, en ningún nivel (plan, 1.5, "lo que NO sale").
CLAVES_PROHIBIDAS = {"lead_token", "cliente_id", "lead_id", "presupuesto_id", "canal",
                     "mensaje_original", "confianza_ia", "prioridad", "aprobado_por",
                     "duracion_estimada_dias", "informe", "visita_id", "decision_id"}
# Los 8 estados posibles, leídos del CHECK real de docs/schema_actual.sql
# (no de app/schemas/common.py, que es lo que se prueba). findall saca cada
# 'valor'::character varying de esa línea, en orden.
LINEA_CHECK = next(l for l in (RAIZ_REPO / "docs" / "schema_actual.sql").read_text(encoding="utf-8").splitlines()
                   if "oportunidades_estado_check" in l)
ESTADOS_CHECK = re.findall(r"'([a-z_]+)'::character varying", LINEA_CHECK)

# ----------------------------------------------------------------------
# Contadores y registros globales
# ----------------------------------------------------------------------
# Número de comprobaciones correctas.
ok = 0
# Lista de textos de las comprobaciones que han fallado.
fallos = []
# Código HTTP de CADA respuesta de /gate-avisos (para contar los 500).
codigos_aviso = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: se modifica la variable ok del archivo, no una copia local.
    global ok
    # Verdadera: se suma una correcta.
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta el título en fallos.
    else:
        fallos.append(titulo)
        print(f"    [FALLO] {titulo}  {detalle}")


# ----------------------------------------------------------------------
# Conexión propia a la base de datos (para preparar, mirar y limpiar)
# ----------------------------------------------------------------------
# Una conexión directa, aparte del pool del servidor.
cn = psycopg2.connect(DATABASE_URL)
# El cursor es el objeto con el que se envían las consultas.
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una sola fila. El commit cierra la transacción de lectura,
    para que la siguiente consulta vea lo último que haya escrito el servidor."""
    # Se envía la consulta; params rellena los %s de forma segura.
    cur.execute(sql, params)
    # La primera fila, como tupla (o None).
    fila = cur.fetchone()
    # commit: cierra la transacción de lectura.
    cn.commit()
    return fila


def consultar_todas(sql, params=()):
    """SELECT de varias filas, con el mismo commit de cierre."""
    cur.execute(sql, params)
    # fetchall(): todas las filas, como lista de tuplas.
    filas = cur.fetchall()
    cn.commit()
    return filas


# ----------------------------------------------------------------------
# Qué es "propio": todo lo que cuelga de los clientes de prueba
# ----------------------------------------------------------------------
# Subconsultas encadenadas: clientes propios -> sus leads -> sus
# oportunidades. %(patron)s se rellena con PATRON_EMAIL.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPORTUNIDADES = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# Para cada tabla, la condición que identifica sus filas PROPIAS.
PROPIO = {
    "clientes": f"id IN ({SQL_CLIENTES})",
    "leads": f"id IN ({SQL_LEADS})",
    "oportunidades": f"id IN ({SQL_OPORTUNIDADES})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "visitas": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPORTUNIDADES})",
}
# Orden de borrado: primero lo que apunta a otras tablas (decisiones_gate
# antes que visitas, por su clave foránea doble).
ORDEN_BORRADO = ["logs", "decisiones_gate", "visitas", "presupuestos", "oportunidades", "leads", "clientes"]


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas, en
    una sola transacción."""
    for tabla in ORDEN_BORRADO:
        # SQL: borra las filas de esa tabla que cumplen la condición de PROPIO.
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    # Un solo commit para todos los borrados.
    cn.commit()


def foto_ajena():
    """Para cada tabla: (id_base, filas ajenas con id <= id_base)."""
    foto = {}
    for tabla in ORDEN_BORRADO:
        # SQL: el id más alto ahora (0 si la tabla está vacía).
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        id_base = cur.fetchone()[0]
        # SQL: cuántas filas NO propias hay con id <= id_base.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                    {"patron": PATRON_EMAIL, "base": id_base})
        foto[tabla] = (id_base, cur.fetchone()[0])
    cn.commit()
    return foto


def recontar_ajeno(foto):
    """Vuelve a contar lo ajeno con los MISMOS id_base: (antiguas, nuevas)."""
    resultado = {}
    for tabla, (id_base, _) in foto.items():
        params = {"patron": PATRON_EMAIL, "base": id_base}
        # SQL: ajenas ANTIGUAS (deben ser las mismas que al empezar).
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});", params)
        antiguas = cur.fetchone()[0]
        # SQL: ajenas NUEVAS (las escribió otro proceso; solo se informa).
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id > %(base)s AND NOT ({PROPIO[tabla]});", params)
        resultado[tabla] = (antiguas, cur.fetchone()[0])
    cn.commit()
    return resultado


def foto_propia():
    """Recuento de filas PROPIAS por tabla, y updated_at y
    fecha_ultimo_contacto de las oportunidades propias (CASO 11)."""
    recuentos = {}
    for tabla in ORDEN_BORRADO:
        # SQL: cuántas filas propias tiene la tabla.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
        recuentos[tabla] = cur.fetchone()[0]
    # SQL: las dos fechas que escribiría una operación, de cada oportunidad propia.
    cur.execute(f"SELECT id, updated_at, fecha_ultimo_contacto FROM oportunidades "
                f"WHERE {PROPIO['oportunidades']} ORDER BY id;", {"patron": PATRON_EMAIL})
    fechas = cur.fetchall()
    cn.commit()
    return recuentos, fechas


# ----------------------------------------------------------------------
# Ayudas HTTP
# ----------------------------------------------------------------------
def cuerpo(r):
    """JSON de la respuesta, o {} si no es JSON (así un fallo no revienta el script)."""
    try:
        return r.json()
    except ValueError:
        return {}


def motivo(r):
    """El motivo de un rechazo de negocio: {"detail": {"motivo": ...}}."""
    detalle = cuerpo(r).get("detail")
    # El 422 de Pydantic trae una LISTA en detail, no un objeto.
    return detalle.get("motivo") if isinstance(detalle, dict) else None


def aviso(op, cabeceras=AUTH_GATE):
    """GET /gate-avisos/{op}. TODAS las peticiones a este endpoint pasan por
    aquí, para contar los 500 al final."""
    r = requests.get(f"{BASE}/gate-avisos/{op}", headers=cabeceras, timeout=30)
    codigos_aviso.append(r.status_code)
    return r


def lead_http(nombre, email, telefono, estructural, m2=6, fotos=None):
    """Crea un lead por POST /leads. Devuelve el oportunidad_id."""
    # Token único de esta ejecución (clave de idempotencia de POST /leads).
    token = str(uuid.uuid4())
    # Petición real a POST /leads, con la cabecera de n8n.
    r = requests.post(
        f"{BASE}/leads",
        headers=AUTH_WEBHOOK,
        json={
            "nombre": nombre,
            "email": email,
            "telefono": telefono,
            "tipo_reforma": "bano",
            "m2": m2,
            "nivel_acabados": "medio",
            "incluye_cambios_estructurales": estructural,
            "fotos": fotos or [],
            "lead_token": token,
        },
        timeout=20,
    )
    # Sin el lead no tiene sentido seguir: assert para el script con el error.
    assert r.status_code == 201, r.text
    return r.json()["oportunidad_id"]


def calcular(op):
    """POST /calculate-estimate. Devuelve su status ('pendiente_aprobacion'...)."""
    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH_WEBHOOK,
                      json={"oportunidad_id": op}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def caso_con_gate(etiqueta, m2=6, fotos=None):
    """Lead con cambios estructurales + cálculo: queda con Gate. Devuelve
    (oportunidad_id, email, nombre, telefono)."""
    # Email con la marca propia y 8 caracteres aleatorios.
    email = f"http-aviso-{uuid.uuid4().hex[:8]}@example.com"
    nombre, telefono = f"Aviso {etiqueta}", "611222333"
    op = lead_http(nombre, email, telefono, estructural=True, m2=m2, fotos=fotos)
    # Con cambios estructurales, el cálculo siempre deja Gate.
    assert calcular(op) == "pendiente_aprobacion"
    return op, email, nombre, telefono


def decidir(op, cuerpo_peticion):
    """POST /gate-decisions con la llave del Gate; debe dar 201."""
    r = requests.post(f"{BASE}/gate-decisions", headers=AUTH_GATE,
                      json={"oportunidad_id": op, **cuerpo_peticion}, timeout=20)
    assert r.status_code == 201, r.text
    return r.json()


def claves_anidadas(objeto):
    """Todas las claves de un JSON, en cualquier nivel (para CLAVES_PROHIBIDAS)."""
    # Un diccionario: sus claves y, recursivamente, las de sus valores.
    if isinstance(objeto, dict):
        claves = set(objeto)
        for valor in objeto.values():
            claves |= claves_anidadas(valor)
        return claves
    # Una lista: las claves de cada elemento.
    if isinstance(objeto, list):
        claves = set()
        for elemento in objeto:
            claves |= claves_anidadas(elemento)
        return claves
    # Un valor simple no tiene claves.
    return set()


def foto_calculo(op):
    """El detalle de la foto 'presupuesto_calculado' de esa oportunidad en
    logs (lo escribió estimate_service): los importes que se calcularon."""
    # SQL: el detalle (JSONB, que psycopg2 entrega como diccionario) de la
    # última foto de esa oportunidad.
    fila = consultar("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                     "AND accion = 'presupuesto_calculado' ORDER BY id DESC LIMIT 1;", (op,))
    return fila[0] if fila else {}


def puerto_ocupado(puerto):
    """True si algo acepta conexiones en 127.0.0.1:puerto."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        # connect_ex devuelve 0 si la conexión se pudo abrir.
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ----------------------------------------------------------------------
# Fechas de las visitas: a los dos lados de un cambio de hora, CALCULADAS
# ----------------------------------------------------------------------
def desfase(dia):
    """Desfase de Madrid a mediodía de ese día (lejos de las 02:00-03:00 del cambio)."""
    return datetime.combine(dia, hora_del_dia(12, 0), tzinfo=MADRID).utcoffset()


def proximos_cambios(hoy, cuantos=2):
    """Los próximos 'cuantos' días en que cambia el desfase de Madrid,
    buscados día a día con zoneinfo (nunca escritos a mano)."""
    cambios, dia = [], hoy
    while len(cambios) < cuantos:
        siguiente = dia + timedelta(days=1)
        # Si el desfase de mañana es distinto del de hoy, mañana hay cambio.
        if desfase(siguiente) != desfase(dia):
            cambios.append(siguiente)
        dia = siguiente
    return cambios


def primer_lunes_desde(dia):
    """El primer lunes en ese día o después (weekday(): lunes = 0)."""
    return dia + timedelta(days=(7 - dia.weekday()) % 7)


# Hoy en Madrid, los dos próximos cambios y los dos lunes elegidos (plan,
# CASO 8, opción a): siempre futuros, siempre en la franja de mañana y con
# desfases opuestos.
HOY = datetime.now(MADRID).date()
CAMBIO_1, CAMBIO_2 = proximos_cambios(HOY)
LUNES_1, LUNES_2 = primer_lunes_desde(CAMBIO_1), primer_lunes_desde(CAMBIO_2)

# ======================================================================
# Antes de nada: el puerto tiene que estar LIBRE. Si otro proceso lo usa,
# se para y se avisa (CLAUDE.md): no se mata nada.
# ======================================================================
if puerto_ocupado(PUERTO):
    print(f"ABORTO: el puerto {PUERTO} está ocupado por otro proceso. No ejecuto nada ni mato nada.")
    sys.exit(2)

# Restos de una ejecución anterior cortada (solo lo propio).
limpiar()
# Foto de lo ajeno: id_base y recuento por tabla.
FOTO = foto_ajena()
print("=" * 78)
print("FOTO DE LO AJENO AL EMPEZAR (tabla: id_base, filas ajenas con id <= id_base)")
print("=" * 78)
for tabla, (id_base, n) in FOTO.items():
    print(f"  {tabla:16} id_base={id_base:<6} ajenas={n}")
print(f"  Hoy {HOY}; cambios de hora {CAMBIO_1} y {CAMBIO_2}; visitas los lunes {LUNES_1} y {LUNES_2}")
print(f"  Estados del CHECK (schema_actual.sql): {ESTADOS_CHECK}")

# ======================================================================
# Servidor de verdad en un hilo de este proceso
# ======================================================================
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# daemon=True: si el script termina de golpe, el hilo no lo deja colgado.
hilo = threading.Thread(target=servidor.run, daemon=True)
hilo.start()
inicio = time.time()
# Se pregunta a /health hasta que conteste, como mucho 30 s.
while time.time() - inicio < 30:
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    except requests.exceptions.RequestException:
        time.sleep(0.25)
# else del while: solo si pasaron los 30 s sin break.
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio:.2f} s")

# try/finally: pase lo que pase dentro, el finally apaga el servidor y limpia.
try:
    # ------------------------------------------------------------------
    # Preparación de los casos (todo con la marca propia)
    # ------------------------------------------------------------------
    print("\nPREPARACIÓN")
    # A: con Gate, m2 con decimal y 5 fotos (CASOS 6, 7, 8, 9 y 10).
    FOTOS_A = [f"leads/prueba-aviso/foto-{i}.jpg" for i in range(1, 6)]
    OP_A, EMAIL_A, NOMBRE_A, TEL_A = caso_con_gate("A", m2=8.7, fotos=FOTOS_A)
    # B1 y B2: MISMO email. B1 sin Gate (crea la ficha con su nombre y su
    # teléfono); B2 con Gate y OTRO nombre y teléfono (CASOS 4 y 5).
    EMAIL_B = f"http-aviso-{uuid.uuid4().hex[:8]}@example.com"
    OP_B1 = lead_http("Ficha Primera", EMAIL_B, "600000001", estructural=False, m2=4)
    assert calcular(OP_B1) == "presupuesto_enviado"
    OP_B2 = lead_http("Solicitud Segunda", EMAIL_B, "600000002", estructural=True, m2=5)
    assert calcular(OP_B2) == "pendiente_aprobacion"
    # SP: lead sin calcular, para el 409 sin_presupuesto (CASO 3).
    OP_SP = lead_http("Aviso SP", f"http-aviso-{uuid.uuid4().hex[:8]}@example.com", "611222334",
                      estructural=True)
    # L: lead "antiguo" sin la clave 'contacto' (CASO 5, P1). POST /leads ya
    # siempre la guarda, así que se crea por SQL, con la marca propia y una
    # ficha CON nombre, email y teléfono (que no deben aparecer en el aviso).
    EMAIL_L = f"http-aviso-{uuid.uuid4().hex[:8]}@example.com"
    # SQL: el cliente del lead antiguo; RETURNING id devuelve su id.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Ficha Antigua", EMAIL_L, "600000003"))
    cliente_l = cur.fetchone()[0]
    # SQL: su lead, con los datos de la reforma pero SIN 'contacto', y sin fotos.
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_aviso', '[]'::jsonb, %s) RETURNING id;",
                (cliente_l, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                  "incluye_cambios_estructurales": True})))
    lead_l = cur.fetchone()[0]
    # SQL: su oportunidad, en 'nueva' (el único estado desde el que se calcula).
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano') RETURNING id;", (lead_l,))
    OP_L = cur.fetchone()[0]
    # commit: el servidor tiene que ver estas filas.
    cn.commit()
    assert calcular(OP_L) == "pendiente_aprobacion"
    # X e Y: visitas acordadas a los dos lados de un cambio de hora; Z: descarte.
    OP_X = caso_con_gate("X")[0]
    OP_Y = caso_con_gate("Y")[0]
    OP_Z = caso_con_gate("Z")[0]
    # Informes únicos de esta ejecución: no deben aparecer nunca en el aviso.
    INFORME_X = f"INFORME-PRIVADO-X-{uuid.uuid4().hex}"
    INFORME_Z = f"INFORME-PRIVADO-Z-{uuid.uuid4().hex}"
    decidir(OP_X, {"decision": "visita_acordada", "fecha": LUNES_1.isoformat(), "hora": "08:30",
                   "informe": INFORME_X})
    decidir(OP_Y, {"decision": "visita_acordada", "fecha": LUNES_2.isoformat(), "hora": "08:30",
                   "informe": f"INFORME-PRIVADO-Y-{uuid.uuid4().hex}"})
    decidir(OP_Z, {"decision": "descartar", "motivo": "precio", "informe": INFORME_Z})
    print(f"  A={OP_A} B1={OP_B1} B2={OP_B2} SP={OP_SP} L={OP_L} X={OP_X} Y={OP_Y} Z={OP_Z}")

    # ==================================================================
    print("\nCASO 1 - Llave (pre-decisión 2)")
    # ==================================================================
    r = aviso(OP_A, {})
    comprobar("sin cabecera -> 401", r.status_code == 401, f"({r.status_code})")
    comprobar("cuerpo del 401 = {\"detail\": \"No autorizado\"}", cuerpo(r) == {"detail": "No autorizado"})
    comprobar("cabecera WWW-Authenticate: APIKey", r.headers.get("WWW-Authenticate") == "APIKey")
    # El 401 de POST /gate-decisions, para comparar: tiene que ser idéntico.
    r_gd = requests.post(f"{BASE}/gate-decisions", json={}, timeout=20)
    comprobar("401 idéntico al de POST /gate-decisions (cuerpo y WWW-Authenticate)",
              r.status_code == r_gd.status_code and r.content == r_gd.content
              and r.headers.get("WWW-Authenticate") == r_gd.headers.get("WWW-Authenticate"))
    r = aviso(OP_A, {"X-Gate-Secret": "no-es-el-secreto"})
    comprobar("X-Gate-Secret incorrecto -> 401", r.status_code == 401, f"({r.status_code})")
    r = aviso(OP_A, {"X-Gate-Secret": WEBHOOK_SECRET})
    comprobar("valor de WEBHOOK_SECRET en X-Gate-Secret -> 401", r.status_code == 401, f"({r.status_code})")
    r = aviso(OP_A, {"X-Webhook-Secret": WEBHOOK_SECRET})
    comprobar("valor de WEBHOOK_SECRET en X-Webhook-Secret -> 401", r.status_code == 401, f"({r.status_code})")
    r = aviso("abc", {})
    comprobar("sin cabecera e id 'abc' -> 401 (antes que el 422)", r.status_code == 401, f"({r.status_code})")

    # ==================================================================
    print("\nCASO 2 - Forma (422 de Pydantic)")
    # ==================================================================
    for valor in ("0", "-1", "abc"):
        r = aviso(valor)
        detalle = cuerpo(r).get("detail")
        # El 422 de Pydantic es una lista de errores; loc dice dónde está el problema.
        loc = detalle[0].get("loc") if isinstance(detalle, list) and detalle else None
        comprobar(f"id '{valor}' -> 422 que nombra oportunidad_id", r.status_code == 422
                  and loc == ["path", "oportunidad_id"], f"({r.status_code}, loc={loc})")

    # ==================================================================
    print("\nCASO 3 - 404 y 409 sin_presupuesto")
    # ==================================================================
    # Un id que no existe: el máximo actual más un millón.
    inexistente = consultar("SELECT max(id) FROM oportunidades;")[0] + 1_000_000
    r = aviso(inexistente)
    comprobar("id inexistente -> 404 oportunidad_no_encontrada", r.status_code == 404
              and motivo(r) == "oportunidad_no_encontrada", f"({r.status_code}, {motivo(r)})")
    # Encargo del Bloque C: el primer id que no cabe en SERIAL (int4) y uno
    # que no cabe ni en bigint. Deben dar 404, NUNCA 500.
    for enorme in ("2147483648", "99999999999999999999"):
        r = aviso(enorme)
        comprobar(f"id {enorme} (no cabe en int4{' ni en bigint' if len(enorme) > 19 else ''}) -> 404, nunca 500",
                  r.status_code == 404 and motivo(r) == "oportunidad_no_encontrada", f"({r.status_code}, {motivo(r)})")
    r = aviso(OP_SP)
    comprobar("caso sin presupuesto -> 409 sin_presupuesto", r.status_code == 409
              and motivo(r) == "sin_presupuesto", f"({r.status_code}, {motivo(r)})")

    # ==================================================================
    print("\nCASO 4 - 409 sin_gate: NUNCA sale nada de un caso sin Gate")
    # ==================================================================
    r = aviso(OP_B1)
    comprobar("caso sin Gate -> 409 sin_gate", r.status_code == 409 and motivo(r) == "sin_gate",
              f"({r.status_code}, {motivo(r)})")
    comprobar("el 409 solo trae detail {motivo, mensaje}",
              set(cuerpo(r)) == {"detail"} and set(cuerpo(r).get("detail", {})) == {"motivo", "mensaje"})
    # SQL: los importes de B1, para buscarlos en el texto de la respuesta.
    imp_b1 = consultar("SELECT importe_min_con_iva, importe_max_con_iva FROM presupuestos "
                       "WHERE oportunidad_id = %s;", (OP_B1,))
    secretos_b1 = ["Ficha Primera", EMAIL_B, "600000001", str(imp_b1[0]), str(imp_b1[1])]
    comprobar("ni nombre, ni email, ni teléfono, ni importes de B1 en la respuesta",
              all(s not in r.text for s in secretos_b1))

    # ==================================================================
    print("\nCASO 5 - El contacto es el del LEAD, no el de la ficha (P1)")
    # ==================================================================
    r = aviso(OP_B2)
    b2 = cuerpo(r)
    comprobar("B2 (mismo email que B1, con Gate) -> 200", r.status_code == 200, f"({r.status_code})")
    comprobar("contacto de B2 = el de SU solicitud, con origen 'solicitud'",
              b2.get("contacto") == {"nombre": "Solicitud Segunda", "email": EMAIL_B,
                                     "telefono": "600000002", "origen": "solicitud"}, f"({b2.get('contacto')})")
    # SQL: la ficha del cliente de B2 (debe seguir siendo la de B1: no se sobrescribe).
    ficha_b = consultar("SELECT nombre, telefono FROM clientes WHERE email = %s;", (EMAIL_B,))
    comprobar("la ficha sigue siendo la de B1 (no se sobrescribió)", ficha_b == ("Ficha Primera", "600000001"),
              f"({ficha_b})")
    comprobar("ni el nombre ni el teléfono de la ficha aparecen en el aviso de B2",
              "Ficha Primera" not in r.text and "600000001" not in r.text)
    r = aviso(OP_L)
    lgy = cuerpo(r)
    comprobar("L (lead sin 'contacto') -> 200", r.status_code == 200, f"({r.status_code})")
    comprobar("contacto de L: tres null y origen 'no_disponible'",
              lgy.get("contacto") == {"nombre": None, "email": None, "telefono": None, "origen": "no_disponible"},
              f"({lgy.get('contacto')})")
    comprobar("nada de la ficha de L (nombre, email, teléfono) en el aviso",
              all(s not in r.text for s in ("Ficha Antigua", EMAIL_L, "600000003")))

    # ==================================================================
    print("\nCASO 6 - Decisión: null y con datos, y nunca el informe")
    # ==================================================================
    r = aviso(OP_A)
    a = cuerpo(r)
    comprobar("A (sin decisión) -> decision null", r.status_code == 200 and a.get("decision") is None)
    r_x = aviso(OP_X)
    x = cuerpo(r_x)
    dx = x.get("decision") or {}
    # SQL: la decisión de X tal como quedó guardada.
    fila_x = consultar("SELECT decision, motivo, created_at FROM decisiones_gate WHERE oportunidad_id = %s;", (OP_X,))
    comprobar("X: decision 'visita_acordada' y motivo null",
              dx.get("decision") == "visita_acordada" and dx.get("motivo") is None, f"({dx})")
    comprobar("X: fecha_decision = decisiones_gate.created_at (mismo instante)",
              dx.get("fecha_decision") is not None
              and datetime.fromisoformat(dx["fecha_decision"]) == fila_x[2])
    comprobar("X: fecha_visita presente", dx.get("fecha_visita") is not None)
    r_z = aviso(OP_Z)
    dz = cuerpo(r_z).get("decision") or {}
    comprobar("Z: decision 'descartar', motivo 'precio' y fecha_visita null",
              dz.get("decision") == "descartar" and dz.get("motivo") == "precio" and dz.get("fecha_visita") is None,
              f"({dz})")
    comprobar("el informe no aparece (ni la clave ni el texto) en X ni en Z",
              "informe" not in r_x.text and INFORME_X not in r_x.text
              and "informe" not in r_z.text and INFORME_Z not in r_z.text)
    comprobar("estado de X 'visita_agendada' y de Z 'perdida'",
              x.get("estado_oportunidad") == "visita_agendada"
              and cuerpo(r_z).get("estado_oportunidad") == "perdida")

    # ==================================================================
    print("\nCASO 7 - Fotos (D1)")
    # ==================================================================
    comprobar("A: las 5 rutas, en el mismo orden", a.get("fotos") == FOTOS_A, f"({len(a.get('fotos') or [])})")
    comprobar("B2 (sin fotos): lista vacía", b2.get("fotos") == [], f"({b2.get('fotos')})")

    # ==================================================================
    print("\nCASO 8 - Fechas con el desfase de Madrid")
    # ==================================================================
    # SQL: las dos fechas de A tal como están guardadas (TIMESTAMPTZ).
    f_lead, f_pres = consultar("SELECT l.created_at, p.created_at FROM oportunidades o "
                               "JOIN leads l ON l.id = o.lead_id JOIN presupuestos p ON p.oportunidad_id = o.id "
                               "WHERE o.id = %s;", (OP_A,))
    for nombre_campo, texto, guardada in (("fecha_solicitud", a.get("fecha_solicitud"), f_lead),
                                          ("fecha_presupuesto", (a.get("presupuesto") or {}).get("fecha_presupuesto"),
                                           f_pres)):
        devuelta = datetime.fromisoformat(texto) if texto else None
        # Mismo instante, y escrito con el desfase que tenía Madrid en ESE instante.
        comprobar(f"{nombre_campo}: mismo instante que la columna y desfase de Madrid",
                  devuelta == guardada and devuelta.utcoffset() == guardada.astimezone(MADRID).utcoffset(),
                  f"({texto})")
    # Las visitas: la hora local esperada, con el desfase que zoneinfo da para ese día.
    esperada_x = datetime.combine(LUNES_1, hora_del_dia(8, 30), tzinfo=MADRID).isoformat()
    esperada_y = datetime.combine(LUNES_2, hora_del_dia(8, 30), tzinfo=MADRID).isoformat()
    fv_y = (cuerpo(aviso(OP_Y)).get("decision") or {}).get("fecha_visita")
    comprobar(f"X: fecha_visita = {esperada_x}", dx.get("fecha_visita") == esperada_x, f"({dx.get('fecha_visita')})")
    comprobar(f"Y: fecha_visita = {esperada_y}", fv_y == esperada_y, f"({fv_y})")
    comprobar("los dos desfases son distintos (+01:00 y +02:00)",
              fv_y is not None and dx.get("fecha_visita") is not None and fv_y[-6:] != dx["fecha_visita"][-6:])

    # ==================================================================
    print("\nCASO 9 - Importes exactos (D4)")
    # ==================================================================
    for etiqueta, op, datos in (("A", OP_A, a), ("B2", OP_B2, b2), ("L", OP_L, lgy), ("X", OP_X, x)):
        p = datos.get("presupuesto") or {}
        # SQL: los importes, el IVA y el umbral vigente de su categoría, de las tablas.
        fila = consultar("SELECT p.importe_min_con_iva, p.importe_max_con_iva, p.iva_pct_aplicado, u.umbral "
                         "FROM presupuestos p JOIN oportunidades o ON o.id = p.oportunidad_id "
                         "LEFT JOIN umbrales_gate u ON u.tipo_reforma = o.tipo_reforma "
                         "WHERE p.oportunidad_id = %s;", (op,))
        # La foto del cálculo: los importes sin IVA que calculó estimate_service.
        foto = foto_calculo(op)
        comprobar(f"{etiqueta}: con IVA e IVA aplicado = columnas, como TEXTO",
                  [p.get("importe_min_con_iva"), p.get("importe_max_con_iva"), p.get("iva_pct_aplicado")]
                  == [str(fila[0]), str(fila[1]), str(fila[2])], f"({p.get('importe_min_con_iva')}, {p.get('importe_max_con_iva')})")
        comprobar(f"{etiqueta}: sin IVA = el de la foto del cálculo",
                  [p.get("importe_min_sin_iva"), p.get("importe_max_sin_iva")]
                  == [foto.get("importe_min_sin_iva"), foto.get("importe_max_sin_iva")],
                  f"({p.get('importe_min_sin_iva')}, {p.get('importe_max_sin_iva')})")
        comprobar(f"{etiqueta}: umbral_gate_vigente = umbrales_gate.umbral", p.get("umbral_gate_vigente") == str(fila[3]),
                  f"({p.get('umbral_gate_vigente')})")
    comprobar("A: m2 como texto exacto '8.7'", (a.get("reforma") or {}).get("m2") == "8.7",
              f"({(a.get('reforma') or {}).get('m2')})")

    # ==================================================================
    print("\nCASO 10 - Minimización y contrato")
    # ==================================================================
    for etiqueta, datos in (("A", a), ("X", x)):
        comprobar(f"{etiqueta}: claves del primer nivel = las del contrato", set(datos) == CLAVES_RAIZ, f"({sorted(datos)})")
        comprobar(f"{etiqueta}: claves de contacto, reforma y presupuesto = las del contrato",
                  set(datos.get("contacto") or {}) == CLAVES_CONTACTO
                  and set(datos.get("reforma") or {}) == CLAVES_REFORMA
                  and set(datos.get("presupuesto") or {}) == CLAVES_PRESUPUESTO)
        comprobar(f"{etiqueta}: ninguna clave prohibida en ningún nivel",
                  not (claves_anidadas(datos) & CLAVES_PROHIBIDAS), f"({claves_anidadas(datos) & CLAVES_PROHIBIDAS})")
    comprobar("X: claves de decision = las del contrato", set(dx) == CLAVES_DECISION, f"({sorted(dx)})")
    # Corrección del tutor sobre el Bloque B: el estado es uno de los 8 del CHECK.
    comprobar("se leyeron 8 estados del CHECK de schema_actual.sql", len(ESTADOS_CHECK) == 8, f"({len(ESTADOS_CHECK)})")
    comprobar("estado_oportunidad es uno de los 8 del CHECK (A, B2, L, X, Z)",
              all(d.get("estado_oportunidad") in ESTADOS_CHECK for d in (a, b2, lgy, x, cuerpo(r_z))))
    comprobar("A: estado 'pendiente_aprobacion'", a.get("estado_oportunidad") == "pendiente_aprobacion")

    # ==================================================================
    print("\nCASO 11 - Solo lectura: nada cambia al consultar")
    # ==================================================================
    antes = foto_propia()
    # Una tanda de consultas de todos los tipos: 200, 404, 409, 401 y 422.
    for op, cab in ((OP_A, AUTH_GATE), (OP_X, AUTH_GATE), (OP_Z, AUTH_GATE), (OP_L, AUTH_GATE),
                    (inexistente, AUTH_GATE), (OP_B1, AUTH_GATE), (OP_SP, AUTH_GATE), (OP_A, {}), ("abc", AUTH_GATE)):
        aviso(op, cab)
    despues = foto_propia()
    comprobar("recuentos de filas propias (7 tablas, logs incluidos) iguales", antes[0] == despues[0],
              f"({antes[0]} -> {despues[0]})")
    comprobar("updated_at y fecha_ultimo_contacto de las oportunidades propias iguales", antes[1] == despues[1])

    # ==================================================================
    print("\nCASO 12 - Repetición: el mismo aviso, byte a byte")
    # ==================================================================
    r1, r2 = aviso(OP_A), aviso(OP_A)
    comprobar("dos llamadas seguidas dan el mismo cuerpo", r1.content == r2.content and r1.status_code == 200)

    # Ningún 500 en ninguna respuesta de /gate-avisos de todo el script.
    comprobar("ninguna respuesta 500 de /gate-avisos", 500 not in codigos_aviso,
              f"({len(codigos_aviso)} peticiones, códigos {sorted(set(codigos_aviso))})")

finally:
    # Apagado ordenado del servidor propio (el hilo de este proceso).
    servidor.should_exit = True
    hilo.join(timeout=15)

    # Logs del manejador global (500) de /gate-avisos escritos durante la
    # prueba: no llevan marca propia. Se borran por id EXACTO solo si son
    # tantos como respuestas 500 ha recibido este script (regla de CLAUDE.md).
    n_500 = codigos_aviso.count(500)
    # SQL: logs error_no_controlado posteriores a la foto, de rutas /gate-avisos/.
    logs_500 = consultar_todas("SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' "
                               "AND detalle->>'ruta' LIKE '/gate-avisos/%%' ORDER BY id;", (FOTO["logs"][0],))
    print(f"\n  Respuestas 500 de /gate-avisos: {n_500}; logs error_no_controlado de esa ruta: {logs_500}")
    comprobar("los logs de 500 de /gate-avisos son exactamente los esperados", len(logs_500) == n_500,
              f"({len(logs_500)} logs, {n_500} respuestas 500)")
    if logs_500 and len(logs_500) == n_500:
        # SQL: se borran por sus ids exactos.
        cur.execute("DELETE FROM logs WHERE id = ANY(%s);", ([f[0] for f in logs_500],))
        cn.commit()
        print(f"  Borrados por id exacto: {[f[0] for f in logs_500]}")

    # Limpieza de lo propio y comprobación de que no queda nada.
    limpiar()
    restos = consultar(f"SELECT count(*) FROM clientes WHERE {PROPIO['clientes']};", {"patron": PATRON_EMAIL})[0]
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")
    comprobar("no queda ningún dato de prueba", restos == 0)

    # CASO 13: lo ajeno, con los mismos id_base que al empezar.
    print("\nCASO 13 - NO TOCA NADA AJENO (mismo id_base que al empezar)")
    recuento = recontar_ajeno(FOTO)
    for tabla, (id_base, antes_n) in FOTO.items():
        antiguas, nuevas = recuento[tabla]
        comprobar(f"{tabla}: ajenas con id <= {id_base}: {antes_n} -> {antiguas}", antiguas == antes_n,
                  f"(ajenas NUEVAS durante la prueba, solo información: {nuevas})")
    cn.close()

# Resumen final y código de salida: 1 si algo falló (para la suite).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
