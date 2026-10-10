"""
Verificación por HTTP REAL de GET /oportunidades/{oportunidad_id}/ficha
(plan: docs/Plan_Endpoint_Ficha_Oportunidad.txt, sección 8.2).

  1. Levanta el servidor de verdad (uvicorn.Server en un hilo de este
     proceso, SIN --reload, en el puerto 8026: nunca el 8000, que es el
     uvicorn de n8n de Gabi). Si el 8026 está ocupado, se para sin tocar
     nada.
  2. Crea sus casos como lo hará n8n (POST /leads, /calculate-estimate,
     /visits, /gate-decisions y /create-followup-task) y por SQL cuando no
     hay otro camino (lead sin 'contacto', IVA 10/4/0, fechas fijas,
     cambio a mano, logs que no son cambio de estado, desempate por id).
  3. CASOS 1-14 del plan: llave, forma, 404, cada tipo de oportunidad (T1
     a T10), importes, fechas, claves exactas, logs.detalle, otras
     oportunidades, solo lectura, repetición, coherencia con
     /gate-avisos, ningún 500 y nada ajeno tocado.

Marca propia: emails http-ficha-<8 caracteres>@example.com y tokens uuid4.
Las claves esperadas están copiadas del PLAN (2.5 y 2.6), no del código.

LAS 4 PREGUNTAS (regla de CLAUDE.md, para la base de datos REAL):
  (1) ¿Escribe en una tabla real? Sí, SOLO filas propias: clientes con la
      marca y lo que cuelga de ellos (leads, oportunidades, presupuestos,
      visitas, decisiones_gate y logs de esas oportunidades), por los
      endpoints y por SQL, con commit. Además, UN log con entity_type
      'sistema' (T10), que no cubre la marca: se anota su id al crearlo y
      se borra por ese id exacto. Nunca toca reglas_negocio, tarifas_base
      ni umbrales_gate (los IVA distintos de 21 son presupuestos propios).
  (2) ¿Puede el código bajo prueba (también una versión rota) confirmar,
      cerrar o reutilizar una conexión del script? No: el código es el
      servidor, con su propio pool; el script no le pasa ninguna conexión.
      Una versión rota que escriba (N9b) solo puede escribir para los ids
      que se le piden: propios (los borra la limpieza por la marca) o
      inexistentes (CASO 3). Por eso, al terminar, se listan los logs
      error_no_controlado de la ruta /oportunidades/ con id > id_base y se
      borran por id exacto solo si son tantos como respuestas 500.
  (3) Si se corta a mitad, ¿cómo vuelve todo a su sitio y quién lo
      comprueba? Quedan filas propias con la marca: limpiar() las borra al
      EMPEZAR la siguiente ejecución, y la comprobación final cuenta 0
      restos. Lo que no lleva marca (el log 'sistema' de T10) se imprime
      con su id al crearlo, para borrarlo a mano por id exacto si el script
      no llega al final.
  (4) ¿Toca algo en uso? No toca el uvicorn del 8000 (usa el 8026; si
      está ocupado, se para). Los datos ajenos solo se leen para la foto
      de lo ajeno; nunca se pide la ficha de una oportunidad ajena.
"""

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
# re: para sacar los estados de los CHECK de docs/schema_actual.sql.
import re
# socket: para comprobar, antes de nada, que nadie escucha en el 8026.
import socket
# sys: sys.path, la salida y el código de salida.
import sys
# threading y time: el servidor en un hilo y la espera a que arranque.
import threading
import time
# uuid: tokens, emails y textos únicos de esta ejecución.
import uuid
# contextmanager: para escribir aislado() como un "with".
from contextlib import contextmanager
# Fechas. "time" se renombra a hora_del_dia para no chocar con el módulo.
from datetime import datetime, time as hora_del_dia, timedelta, timezone
# Decimal: para construir importes exactos (CASO 5).
from decimal import Decimal
# Path: rutas de archivos.
from pathlib import Path
# ZoneInfo: la zona horaria Europe/Madrid (con cambio de hora).
from zoneinfo import ZoneInfo

# La raíz del repositorio es la carpeta padre de scripts/; primera en
# sys.path para que "import app..." encuentre el paquete.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: preparar casos por SQL, leer y limpiar. Json: dict -> JSONB.
import psycopg2
from psycopg2.extras import Json
# requests: peticiones HTTP reales. uvicorn: el servidor.
import requests
import uvicorn

# Los valores del .env: solo en cabeceras, nunca se imprimen.
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET
# El MISMO redondeo del cálculo, para construir los importes con IVA de
# los presupuestos propios del CASO 5 como lo haría estimate_service.
from app.services.estimate_service import CIEN, redondear

# ----------------------------------------------------------------------
# Constantes de la prueba
# ----------------------------------------------------------------------
# Puerto de la ficha (pre-decisión 9). Nunca el 8000.
PUERTO = 8026
BASE = f"http://127.0.0.1:{PUERTO}"
# Las cabeceras: la de n8n (/leads, /calculate-estimate, /visits) y la del
# Gate (la ficha, /gate-avisos, /gate-decisions, /create-followup-task).
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# Patrón LIKE de los clientes de ESTE script.
PATRON_EMAIL = "http-ficha-%@example.com"
# Zona de Madrid, para las fechas esperadas.
MADRID = ZoneInfo("Europe/Madrid")

# Claves EXACTAS de cada nivel, copiadas del PLAN (2.5).
CLAVES_RAIZ = {"oportunidad_id", "estado_oportunidad", "fecha_solicitud", "contacto", "reforma", "fotos",
               "presupuesto", "visitas", "llamadas", "historial", "historial_cuadra",
               "otras_oportunidades_mismo_email"}
CLAVES_CONTACTO = {"nombre", "email", "telefono", "origen"}
CLAVES_REFORMA = {"tipo_reforma", "m2", "nivel_acabados", "incluye_cambios_estructurales"}
CLAVES_PRESUPUESTO = {"fecha_presupuesto", "motivo_gate", "iva_pct_aplicado", "importe_min_con_iva",
                      "importe_max_con_iva", "importe_min_sin_iva", "importe_max_sin_iva",
                      "umbral_gate_vigente", "requiere_aprobacion"}
CLAVES_VISITA = {"estado_visita", "fecha_visita", "fecha_solicitud_visita", "texto_cliente"}
CLAVES_LLAMADA = {"tipo_llamada", "resultado", "motivo", "fecha_registro", "fecha_visita", "informe"}
CLAVES_EVENTO = {"evento", "estado", "fecha"}
CLAVES_OTRA = {"oportunidad_id", "tipo_reforma", "estado_oportunidad", "fecha_solicitud"}
# Lo que NO sale nunca, en ningún nivel (plan, 2.6).
CLAVES_PROHIBIDAS = {"lead_token", "cliente_id", "lead_id", "presupuesto_id", "visita_id", "decision_id",
                     "log_id", "detalle", "accion", "canal", "mensaje_original", "confianza_ia", "prioridad",
                     "datos_completos", "aprobado_por", "duracion_estimada_dias", "updated_at",
                     "fecha_ultimo_contacto"}
# Claves de los detalles de logs que no deben aparecer (CASO 8).
CLAVES_DETALLE = {"sustituye_a", "fecha_anterior", "horas_seguimiento_presupuesto", "estado_anterior",
                  "problemas", "faltan", "decision_id", "presupuesto_id", "precio_m2", "margen_empresa_pct"}
# Los estados de los CHECK reales de docs/schema_actual.sql (no de
# app/schemas/common.py, que es lo que se prueba).
ESQUEMA = (RAIZ_REPO / "docs" / "schema_actual.sql").read_text(encoding="utf-8").splitlines()
ESTADOS_CHECK = re.findall(r"'([a-z_]+)'::character varying",
                           next(l for l in ESQUEMA if "oportunidades_estado_check" in l))
ESTADOS_VISITA_CHECK = re.findall(r"'([a-z_]+)'::character varying",
                                  next(l for l in ESQUEMA if "visitas_estado_check" in l))

# ----------------------------------------------------------------------
# Contadores y registros
# ----------------------------------------------------------------------
# Comprobaciones correctas y títulos de las que fallan.
ok = 0
fallos = []
# Código HTTP de CADA respuesta de la ficha (CASO 13).
codigos_ficha = []
# Todas las respuestas 200 de la ficha (CASO 7).
cuerpos_200 = []


def comprobar(titulo, condicion, detalle=""):
    """Imprime [OK] o [FALLO] y lleva la cuenta."""
    global ok
    if condicion:
        ok += 1
    else:
        fallos.append(titulo)
    print(f"    [{'OK' if condicion else 'FALLO'}] {titulo} {detalle}")


@contextmanager
def aislado(nombre):
    """Cada CASO va dentro de un "with aislado(...)": si revienta (por
    ejemplo, una versión rota de una prueba en negativo da 401 donde se
    esperaba 200), la excepción se cuenta como UN FALLO con su nombre y el
    script sigue con el CASO siguiente, para que el recuento sea exacto.
    Con el código bueno no cambia nada: solo actúa si hay una excepción."""
    try:
        yield
    except Exception as error:
        comprobar(f"{nombre}: termina sin excepción", False, f"({type(error).__name__}: {str(error)[:150]})")


# Conexión propia del script (NUNCA se pasa al servidor).
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una fila; el commit cierra la lectura para ver lo último."""
    # SQL: la consulta que se recibe, con sus parámetros aparte.
    cur.execute(sql, params)
    fila = cur.fetchone()
    cn.commit()
    return fila


def consultar_todas(sql, params=()):
    """SELECT de varias filas, con el mismo commit de cierre."""
    # SQL: la consulta que se recibe, con sus parámetros aparte.
    cur.execute(sql, params)
    filas = cur.fetchall()
    cn.commit()
    return filas


def escribir(sql, params=()):
    """INSERT/UPDATE propio con RETURNING opcional; commit para que el
    servidor lo vea. Devuelve la primera columna de la fila devuelta."""
    # SQL: la escritura que se recibe (siempre sobre filas propias).
    cur.execute(sql, params)
    fila = cur.fetchone() if cur.description else None
    cn.commit()
    return fila[0] if fila else None


# ----------------------------------------------------------------------
# Qué es "propio": todo lo que cuelga de los clientes de prueba
# ----------------------------------------------------------------------
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPS = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
PROPIO = {
    "clientes": f"id IN ({SQL_CLIENTES})",
    "leads": f"id IN ({SQL_LEADS})",
    "oportunidades": f"id IN ({SQL_OPS})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPS})",
    "visitas": f"oportunidad_id IN ({SQL_OPS})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPS})",
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})",
}
# Orden de borrado: primero lo que apunta a otras tablas.
ORDEN_BORRADO = ["logs", "decisiones_gate", "visitas", "presupuestos", "oportunidades", "leads", "clientes"]


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas."""
    for tabla in ORDEN_BORRADO:
        # SQL: borra las filas propias de esa tabla.
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    cn.commit()


def foto_ajena():
    """Para cada tabla: (id_base, filas ajenas con id <= id_base)."""
    foto = {}
    for tabla in ORDEN_BORRADO:
        id_base = consultar(f"SELECT COALESCE(max(id), 0) FROM {tabla};")[0]
        foto[tabla] = (id_base, consultar(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                                          {"patron": PATRON_EMAIL, "base": id_base})[0])
    return foto


def foto_propia():
    """Recuento de filas propias por tabla, y updated_at y
    fecha_ultimo_contacto de cada oportunidad propia (CASO 10)."""
    recuentos = {t: consultar(f"SELECT count(*) FROM {t} WHERE {PROPIO[t]};", {"patron": PATRON_EMAIL})[0]
                 for t in ORDEN_BORRADO}
    marcas = consultar_todas(f"SELECT id, updated_at, fecha_ultimo_contacto FROM oportunidades "
                             f"WHERE {PROPIO['oportunidades']} ORDER BY id;", {"patron": PATRON_EMAIL})
    return recuentos, marcas


# ----------------------------------------------------------------------
# Peticiones
# ----------------------------------------------------------------------
def ficha(op, cabeceras=AUTH_GATE):
    """GET de la ficha; anota el código (CASO 13) y los cuerpos 200 (CASO 7)."""
    r = requests.get(f"{BASE}/oportunidades/{op}/ficha", headers=cabeceras, timeout=30)
    codigos_ficha.append(r.status_code)
    if r.status_code == 200:
        cuerpos_200.append(r.json())
    return r


def ficha_ok(op):
    """La ficha de una oportunidad propia, que tiene que dar 200."""
    r = ficha(op)
    assert r.status_code == 200, (op, r.status_code, r.text[:300])
    return r.json()


def nuevo_email():
    """Un email con la marca propia y 8 caracteres aleatorios."""
    return f"http-ficha-{uuid.uuid4().hex[:8]}@example.com"


def lead_http(nombre, email, telefono, estructural=False, m2=6, fotos=None):
    """POST /leads como n8n. Devuelve (oportunidad_id, lead_token)."""
    token = str(uuid.uuid4())
    r = requests.post(f"{BASE}/leads", headers=AUTH_WEBHOOK, timeout=30, json={
        "nombre": nombre, "email": email, "telefono": telefono, "tipo_reforma": "bano", "m2": m2,
        "nivel_acabados": "medio", "incluye_cambios_estructurales": estructural, "fotos": fotos or [],
        "lead_token": token,
    })
    assert r.status_code == 201, r.text
    return r.json()["oportunidad_id"], token


def calcular(op):
    """POST /calculate-estimate. Devuelve el estado en que queda."""
    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH_WEBHOOK, json={"oportunidad_id": op}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def caso(etiqueta, estructural=False, m2=6, fotos=None):
    """Lead + cálculo. Devuelve (op, token, email, nombre, telefono)."""
    email, nombre, telefono = nuevo_email(), f"Ficha {etiqueta}", "611000026"
    op, token = lead_http(nombre, email, telefono, estructural, m2, fotos)
    # Con cambios estructurales, siempre Gate; sin ellos y con 6 m2, nunca.
    esperado = "pendiente_aprobacion" if estructural else "presupuesto_enviado"
    assert calcular(op) == esperado
    return op, token, email, nombre, telefono


def decidir(op, cuerpo):
    """POST /gate-decisions con la llave del Gate; tiene que dar 201."""
    r = requests.post(f"{BASE}/gate-decisions", headers=AUTH_GATE, json={"oportunidad_id": op, **cuerpo}, timeout=30)
    assert r.status_code == 201, r.text
    return r.json()


def pedir_visita(token, dia, texto):
    """POST /visits como el Agente 2, a las 10:00; tiene que dar 201."""
    r = requests.post(f"{BASE}/visits", headers=AUTH_WEBHOOK, timeout=30, json={
        "lead_token": token, "fecha": dia.isoformat(), "hora": "10:00", "texto_cliente": texto})
    assert r.status_code == 201, r.text
    return r.json()


def claves_anidadas(objeto):
    """Todas las claves de un JSON, en cualquier nivel."""
    if isinstance(objeto, dict):
        claves = set(objeto)
        for valor in objeto.values():
            claves |= claves_anidadas(valor)
        return claves
    if isinstance(objeto, list):
        claves = set()
        for elemento in objeto:
            claves |= claves_anidadas(elemento)
        return claves
    return set()


def pares(c):
    """[(evento, estado)] del historial de una ficha."""
    return [(e["evento"], e["estado"]) for e in c["historial"]]


def madrid(dia, hora, minuto):
    """El texto ISO esperado de ese día y hora de Madrid, con su desfase
    calculado por zoneinfo (nunca escrito a mano)."""
    return datetime.combine(dia, hora_del_dia(hora, minuto), tzinfo=MADRID).isoformat()


def puerto_ocupado(puerto):
    """True si algo acepta conexiones en 127.0.0.1:puerto."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# Días de visita: el primer lunes a 14 días o más, y los tres siguientes
# laborables (lunes a jueves de esa semana), siempre futuros.
HOY = datetime.now(MADRID).date()
LUNES = HOY + timedelta(days=14 + (7 - (HOY + timedelta(days=14)).weekday()) % 7)
MARTES, MIERCOLES, JUEVES = (LUNES + timedelta(days=n) for n in (1, 2, 3))

# ======================================================================
# Arranque
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
print(f"  Visitas: {LUNES} a {JUEVES}. Estados del CHECK: {ESTADOS_CHECK}; de visita: {ESTADOS_VISITA_CHECK}")

# El servidor de verdad, en un hilo de este proceso, SIN --reload.
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
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
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio:.2f} s")

# El id del log 'sistema' de T10 (sin marca): se borra por id exacto.
LOG_SISTEMA = None

try:
    # ==================================================================
    print("\nPREPARACIÓN (todo con la marca propia)")
    # ==================================================================
    with aislado("PREPARACIÓN"):
        # T1: solo POST /leads (sin presupuesto).
        OP_T1, _ = lead_http("Ficha T1", nuevo_email(), "611000001")
        # T2: sin Gate.
        OP_T2 = caso("T2")[0]
        # T3: con Gate sin decidir, m2 con decimal y 5 fotos (CASOS 5, 7, 12).
        FOTOS_T3 = [f"leads/prueba-ficha/foto-{i}.jpg" for i in range(1, 6)]
        OP_T3 = caso("T3", estructural=True, m2=8.7, fotos=FOTOS_T3)[0]
        # T4: Gate con visita acordada e informe único; T4b: Gate descartado.
        OP_T4 = caso("T4", estructural=True)[0]
        INFORME_T4 = f"INFORME-T4-{uuid.uuid4().hex}"
        decidir(OP_T4, {"decision": "visita_acordada", "fecha": JUEVES.isoformat(), "hora": "08:30", "informe": INFORME_T4})
        OP_T4B = caso("T4b", estructural=True)[0]
        INFORME_T4B = f"INFORME-T4B-{uuid.uuid4().hex}"
        decidir(OP_T4B, {"decision": "descartar", "motivo": "precio", "informe": INFORME_T4B})
        # T5: seguimiento abierto. El presupuesto PROPIO se mueve a hace 49 h
        # para cumplir el plazo de 48 h, y se llama a POST /create-followup-task.
        OP_T5 = caso("T5")[0]
        # SQL: mueve el created_at del presupuesto propio a hace 49 h.
        escribir("UPDATE presupuestos SET created_at = now() - interval '49 hours' WHERE oportunidad_id = %s;", (OP_T5,))
        r = requests.post(f"{BASE}/create-followup-task", headers=AUTH_GATE, timeout=30,
                          json={"oportunidad_id": OP_T5, "motivo": "sin_respuesta_visita"})
        assert r.status_code == 201, r.text
        # T6: una visita solicitada desde el chat.
        OP_T6, TOKEN_T6 = caso("T6")[:2]
        TEXTO_T6 = f"TEXTO-VISITA-T6-{uuid.uuid4().hex}"
        pedir_visita(TOKEN_T6, LUNES, TEXTO_T6)
        # T7: una visita sustituida por otra (una cancelada y otra activa).
        OP_T7, TOKEN_T7 = caso("T7")[:2]
        TEXTO_T7A, TEXTO_T7B = f"TEXTO-VISITA-T7A-{uuid.uuid4().hex}", f"TEXTO-VISITA-T7B-{uuid.uuid4().hex}"
        pedir_visita(TOKEN_T7, MARTES, TEXTO_T7A)
        assert pedir_visita(TOKEN_T7, MIERCOLES, TEXTO_T7B)["sustituye_a"] is not None
        # T8: lead "antiguo" sin 'contacto', por SQL, con una ficha de clientes
        # CON nombre y teléfono (que no deben aparecer en la respuesta).
        NOMBRE_FICHA_T8, TEL_FICHA_T8 = f"FichaClientesT8{uuid.uuid4().hex[:6]}", "699000008"
        # SQL: el cliente propio (RETURNING id devuelve su id).
        cliente_t8 = escribir("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                              (NOMBRE_FICHA_T8, nuevo_email(), TEL_FICHA_T8))
        # SQL: su lead, con la reforma pero SIN 'contacto'.
        lead_t8 = escribir("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                           "VALUES (%s, 'check_ficha', '[]'::jsonb, %s) RETURNING id;",
                           (cliente_t8, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                              "incluye_cambios_estructurales": False})))
        # SQL: su oportunidad, en 'nueva'.
        OP_T8 = escribir("INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano') RETURNING id;", (lead_t8,))
        # T9: sin Gate, que luego se pasa A MANO a 'ganada'.
        OP_T9 = caso("T9")[0]
        # T10: sin Gate, con logs que NO son cambio de estado.
        OP_T10 = caso("T10")[0]
        for accion in ("presupuesto_requiere_revision", "visita_configuracion_incompleta"):
            # SQL: un log propio que no cambia el estado.
            escribir("INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES ('oportunidad', %s, %s, %s);",
                     (OP_T10, accion, Json({"estado": "perdida"})))
        # SQL: un log del manejador global con entity_id = la oportunidad propia.
        LOG_SISTEMA = escribir("INSERT INTO logs (entity_type, entity_id, accion, detalle) "
                               "VALUES ('sistema', %s, 'error_no_controlado', %s) RETURNING id;",
                               (OP_T10, Json({"estado": "perdida"})))
        print(f"  log 'sistema' de T10 (sin marca, se borra por id exacto): {LOG_SISTEMA}")
        # CASO 5: IVA 10, 4 y 0 en presupuestos PROPIOS por SQL, con los sin IVA
        # ELEGIDOS y los con IVA construidos como el cálculo.
        SIN_IVA = (Decimal("8765.43"), Decimal("12345.67"))
        OPS_IVA = {}
        for iva in (Decimal("10"), Decimal("4"), Decimal("0")):
            op, _ = lead_http(f"Ficha IVA {iva}", nuevo_email(), "611000005")
            # Con IVA = redondear(sin IVA × (1 + iva/100)), como estimate_service.
            con_iva = [redondear(s * (1 + iva / CIEN)) for s in SIN_IVA]
            # SQL: el presupuesto propio con ese IVA.
            escribir("INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva, "
                     "requiere_aprobacion, iva_pct_aplicado) VALUES (%s, %s, %s, false, %s);", (op, *con_iva, iva))
            OPS_IVA[iva] = op
        # CASO 6: fechas fijas a los dos lados del cambio de hora, por SQL.
        OP_C6, _ = lead_http("Ficha C6", nuevo_email(), "611000006")
        for instante, estado in ((datetime(2026, 10, 23, 6, 30, tzinfo=timezone.utc), "cancelada"),
                                 (datetime(2026, 10, 26, 7, 30, tzinfo=timezone.utc), "completada")):
            # SQL: una visita propia con fecha fija (no activa: no choca con el
            # índice de una activa por oportunidad).
            escribir("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) VALUES (%s, %s, %s, %s);",
                     (OP_C6, instante, estado, f"TEXTO-C6-{estado}"))
        for instante, accion, detalle in ((datetime(2027, 3, 26, 8, 0, tzinfo=timezone.utc), "presupuesto_calculado",
                                           {"estado": "presupuesto_enviado"}),
                                          (datetime(2027, 3, 29, 8, 0, tzinfo=timezone.utc), "seguimiento_abierto", {})):
            # SQL: un log de cambio propio con created_at fijo.
            escribir("INSERT INTO logs (entity_type, entity_id, accion, detalle, created_at) "
                     "VALUES ('oportunidad', %s, %s, %s, %s);", (OP_C6, accion, Json(detalle), instante))
        # Desempate por id (punto a de la revisión de B2): dos logs con el
        # MISMO created_at; el orden tiene que ser el de su id.
        OP_ORDEN, _ = lead_http("Ficha Orden", nuevo_email(), "611000007")
        MISMO_INSTANTE = datetime(2027, 1, 15, 10, 0, tzinfo=timezone.utc)
        for accion, detalle in (("presupuesto_calculado", {"estado": "pendiente_aprobacion"}),
                                ("gate_decision_registrada", {"decision": "descartar"})):
            # SQL: dos logs propios, en este orden (ids crecientes), mismo instante.
            escribir("INSERT INTO logs (entity_type, entity_id, accion, detalle, created_at) "
                     "VALUES ('oportunidad', %s, %s, %s, %s);", (OP_ORDEN, accion, Json(detalle), MISMO_INSTANTE))
        # SQL: el estado que dejan esos dos logs, puesto en la oportunidad propia.
        escribir("UPDATE oportunidades SET estado = 'perdida' WHERE id = %s;", (OP_ORDEN,))
        # CASO 9: dos solicitudes con el MISMO email y una tercera de otro.
        EMAIL_9 = nuevo_email()
        OP_9A, _ = lead_http("Nueve Primera", EMAIL_9, "600000091")
        OP_9B, _ = lead_http("Nueve Segunda", EMAIL_9, "600000092")
        OP_9C, _ = lead_http("Nueve Otra", nuevo_email(), "600000093")
        print("  casos preparados")

    # ==================================================================
    print("\nCASO 1 - Llave")
    # ==================================================================
    with aislado("CASO 1"):
        # Referencia: el 401 de /gate-avisos sin cabecera.
        REF = requests.get(f"{BASE}/gate-avisos/{OP_T3}", timeout=30)
        for titulo, cab in (("sin cabecera", {}), ("X-Gate-Secret incorrecto", {"X-Gate-Secret": "no-es-la-llave"}),
                            ("WEBHOOK_SECRET en X-Gate-Secret", {"X-Gate-Secret": WEBHOOK_SECRET}),
                            ("WEBHOOK_SECRET en X-Webhook-Secret", AUTH_WEBHOOK)):
            r = ficha(OP_T2, cab)
            comprobar(f"{titulo} -> 401, cuerpo y WWW-Authenticate iguales a /gate-avisos",
                      r.status_code == 401 and r.content == REF.content
                      and r.headers.get("WWW-Authenticate") == REF.headers.get("WWW-Authenticate") == "APIKey",
                      f"({r.status_code})")
        # La llave va antes que el 422.
        r = ficha("abc", {})
        comprobar("sin cabecera y 'abc' -> 401 (antes que el 422)", r.status_code == 401, f"({r.status_code})")

    # ==================================================================
    print("\nCASO 2 - Forma")
    # ==================================================================
    with aislado("CASO 2"):
        for valor in ("0", "-1", "abc"):
            r = ficha(valor)
            comprobar(f"'{valor}' -> 422 que nombra oportunidad_id",
                      r.status_code == 422 and "oportunidad_id" in r.text, f"({r.status_code})")

    # ==================================================================
    print("\nCASO 3 - 404 (nunca 500)")
    # ==================================================================
    with aislado("CASO 3"):
        MAXIMO = consultar("SELECT max(id) FROM oportunidades;")[0]
        for valor in (MAXIMO + 1_000_000, 2147483648, "99999999999999999999"):
            r = ficha(valor)
            comprobar(f"{valor} -> 404 oportunidad_no_encontrada",
                      r.status_code == 404 and r.json()["detail"]["motivo"] == "oportunidad_no_encontrada",
                      f"({r.status_code})")

    # ==================================================================
    print("\nCASO 4 - 200 en cada tipo de oportunidad")
    # ==================================================================
    with aislado("CASO 4"):
        # T1: sin presupuesto.
        c = ficha_ok(OP_T1)
        comprobar("T1 sin presupuesto: presupuesto null, listas vacías, [alta/nueva], cuadra",
                  c["presupuesto"] is None and c["visitas"] == [] and c["llamadas"] == []
                  and pares(c) == [("alta", "nueva")] and c["historial_cuadra"] is True, f"({pares(c)})")
        # T2: sin Gate.
        C_T2 = ficha_ok(OP_T2)
        comprobar("T2 sin Gate: presupuesto_enviado, requiere_aprobacion false, motivo_gate null",
                  C_T2["estado_oportunidad"] == "presupuesto_enviado" and C_T2["presupuesto"]["requiere_aprobacion"] is False
                  and C_T2["presupuesto"]["motivo_gate"] is None)
        comprobar("T2: historial [alta, presupuesto_calculado/presupuesto_enviado], cuadra",
                  pares(C_T2) == [("alta", "nueva"), ("presupuesto_calculado", "presupuesto_enviado")]
                  and C_T2["historial_cuadra"] is True, f"({pares(C_T2)})")
        # T3: con Gate sin decidir.
        C_T3 = ficha_ok(OP_T3)
        comprobar("T3 con Gate: pendiente_aprobacion, requiere_aprobacion true, motivo_gate, llamadas []",
                  C_T3["estado_oportunidad"] == "pendiente_aprobacion" and C_T3["presupuesto"]["requiere_aprobacion"] is True
                  and C_T3["presupuesto"]["motivo_gate"] in ("cambios_estructurales", "ambos") and C_T3["llamadas"] == [],
                  f"({C_T3['presupuesto']['motivo_gate']})")
        comprobar("T3: las 5 fotos, tal cual", C_T3["fotos"] == FOTOS_T3, f"({len(C_T3['fotos'])})")
        # T4: Gate con visita acordada y su informe.
        C_T4 = ficha_ok(OP_T4)
        LL = C_T4["llamadas"]
        comprobar("T4: una llamada 'gate' visita_acordada, sin motivo, con el INFORME exacto",
                  len(LL) == 1 and LL[0]["tipo_llamada"] == "gate" and LL[0]["resultado"] == "visita_acordada"
                  and LL[0]["motivo"] is None and LL[0]["informe"] == INFORME_T4, f"({len(LL)} llamadas)")
        comprobar("T4: fecha_visita de la llamada = la acordada, en hora de Madrid",
                  LL and LL[0]["fecha_visita"] == madrid(JUEVES, 8, 30), f"({LL and LL[0]['fecha_visita']})")
        comprobar("T4: una visita 'confirmada' con el texto fijo del sistema",
                  len(C_T4["visitas"]) == 1 and C_T4["visitas"][0]["estado_visita"] == "confirmada"
                  and C_T4["visitas"][0]["texto_cliente"] == consultar(
                      "SELECT texto_cliente FROM visitas WHERE oportunidad_id = %s;", (OP_T4,))[0])
        comprobar("T4: historial termina en gate_decision_registrada/visita_agendada, cuadra",
                  pares(C_T4)[-1] == ("gate_decision_registrada", "visita_agendada") and C_T4["historial_cuadra"] is True,
                  f"({pares(C_T4)})")
        # T4b: Gate descartado.
        C_T4B = ficha_ok(OP_T4B)
        LL = C_T4B["llamadas"]
        comprobar("T4b: descartar con motivo 'precio', fecha_visita null, informe exacto, 'perdida'",
                  len(LL) == 1 and LL[0]["resultado"] == "descartar" and LL[0]["motivo"] == "precio"
                  and LL[0]["fecha_visita"] is None and LL[0]["informe"] == INFORME_T4B
                  and C_T4B["estado_oportunidad"] == "perdida" and C_T4B["visitas"] == [])
        comprobar("T4b: historial termina en gate_decision_registrada/perdida, cuadra",
                  pares(C_T4B)[-1] == ("gate_decision_registrada", "perdida") and C_T4B["historial_cuadra"] is True)
        # T5: seguimiento abierto.
        c = ficha_ok(OP_T5)
        comprobar("T5: historial [alta, presupuesto_calculado, seguimiento_abierto/seguimiento_pendiente], cuadra",
                  pares(c) == [("alta", "nueva"), ("presupuesto_calculado", "presupuesto_enviado"),
                               ("seguimiento_abierto", "seguimiento_pendiente")] and c["historial_cuadra"] is True,
                  f"({pares(c)})")
        # T6: visita solicitada.
        C_T6 = ficha_ok(OP_T6)
        comprobar("T6: una visita 'solicitada' con su texto_cliente, a las 10:00 de Madrid",
                  len(C_T6["visitas"]) == 1 and C_T6["visitas"][0]["estado_visita"] == "solicitada"
                  and C_T6["visitas"][0]["texto_cliente"] == TEXTO_T6
                  and C_T6["visitas"][0]["fecha_visita"] == madrid(LUNES, 10, 0))
        comprobar("T6: historial con visita_solicitada/visita_agendada, cuadra",
                  pares(C_T6)[-1] == ("visita_solicitada", "visita_agendada") and C_T6["historial_cuadra"] is True,
                  f"({pares(C_T6)})")
        # T7: cancelada y activa, en ese orden.
        C_T7 = ficha_ok(OP_T7)
        comprobar("T7: visitas [cancelada, solicitada] en ese orden, con sus textos",
                  [(v["estado_visita"], v["texto_cliente"]) for v in C_T7["visitas"]]
                  == [("cancelada", TEXTO_T7A), ("solicitada", TEXTO_T7B)])
        comprobar("T7: UN solo visita_solicitada en el historial (la sustitución no es un cambio)",
                  [e for e, _ in pares(C_T7)].count("visita_solicitada") == 1 and C_T7["historial_cuadra"] is True,
                  f"({pares(C_T7)})")
        # T8: sin 'contacto'.
        r = ficha(OP_T8)
        c = r.json()
        comprobar("T8: contacto con tres null y 'no_disponible'",
                  c["contacto"] == {"nombre": None, "email": None, "telefono": None, "origen": "no_disponible"})
        comprobar("T8: ni el nombre ni el teléfono de la ficha de clientes aparecen",
                  NOMBRE_FICHA_T8 not in r.text and TEL_FICHA_T8 not in r.text)
        # T9: cambio a mano a 'ganada'.
        ANTES_T9 = ficha_ok(OP_T9)
        # SQL: la oportunidad propia, a 'ganada' a mano (como en N0).
        escribir("UPDATE oportunidades SET estado = 'ganada' WHERE id = %s;", (OP_T9,))
        c = ficha_ok(OP_T9)
        comprobar("T9: 'ganada', historial IGUAL al de antes (nada inventado) y NO cuadra",
                  c["estado_oportunidad"] == "ganada" and c["historial"] == ANTES_T9["historial"]
                  and c["historial_cuadra"] is False, f"({pares(c)}, {c['historial_cuadra']})")
        # T10: logs que no son cambio de estado.
        c = ficha_ok(OP_T10)
        comprobar("T10: los logs que no son cambio de estado no salen; cuadra",
                  pares(c) == [("alta", "nueva"), ("presupuesto_calculado", "presupuesto_enviado")]
                  and c["historial_cuadra"] is True, f"({pares(c)})")

    # ==================================================================
    print("\nCASO 5 - Importes exactos, como TEXTO")
    # ==================================================================
    with aislado("CASO 5"):
        # IVA 21 por /calculate-estimate: el sin IVA es el de la foto del cálculo.
        p = C_T3["presupuesto"]
        # SQL: la foto del cálculo de T3 en logs (la escribió estimate_service).
        FOTO_T3 = consultar("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                            "AND accion = 'presupuesto_calculado' ORDER BY id DESC LIMIT 1;", (OP_T3,))[0]
        comprobar("IVA 21: sin IVA = el de la foto del cálculo",
                  [p["importe_min_sin_iva"], p["importe_max_sin_iva"]]
                  == [FOTO_T3.get("importe_min_sin_iva"), FOTO_T3.get("importe_max_sin_iva")],
                  f"({p['importe_min_sin_iva']}, {p['importe_max_sin_iva']})")
        # IVA 10, 4 y 0: el sin IVA devuelto es exactamente el elegido.
        for iva, op in OPS_IVA.items():
            p = ficha_ok(op)["presupuesto"]
            comprobar(f"IVA {iva}: sin IVA = el elegido, exacto",
                      [p["importe_min_sin_iva"], p["importe_max_sin_iva"]] == [str(s) for s in SIN_IVA],
                      f"({p['importe_min_sin_iva']}, {p['importe_max_sin_iva']})")
        # Todos los números como texto, y el m2 de T3 exacto.
        p = C_T3["presupuesto"]
        textos = [p[k] for k in ("importe_min_con_iva", "importe_max_con_iva", "importe_min_sin_iva",
                                 "importe_max_sin_iva", "iva_pct_aplicado", "umbral_gate_vigente")]
        comprobar("importes, iva_pct_aplicado y umbral_gate_vigente son TEXTO", all(isinstance(t, str) for t in textos),
                  f"({textos})")
        comprobar("m2 de T3 es el texto exacto '8.7'", C_T3["reforma"]["m2"] == "8.7", f"({C_T3['reforma']['m2']!r})")

    # ==================================================================
    print("\nCASO 6 - Fechas en hora de Madrid a los dos lados del cambio de hora")
    # ==================================================================
    with aislado("CASO 6"):
        c = ficha_ok(OP_C6)
        comprobar("visitas: 2026-10-23 +02:00 y 2026-10-26 +01:00",
                  [v["fecha_visita"] for v in c["visitas"]] == ["2026-10-23T08:30:00+02:00", "2026-10-26T08:30:00+01:00"],
                  f"({[v['fecha_visita'] for v in c['visitas']]})")
        comprobar("logs: 2027-03-26 +01:00 y 2027-03-29 +02:00",
                  [e["fecha"] for e in c["historial"][1:]] == ["2027-03-26T09:00:00+01:00", "2027-03-29T10:00:00+02:00"],
                  f"({[e['fecha'] for e in c['historial'][1:]]})")
        # Desempate por id: mismo instante, orden de inserción.
        c = ficha_ok(OP_ORDEN)
        comprobar("a igual created_at, el historial sale por id (calculado y luego decisión), cuadra",
                  pares(c) == [("alta", "nueva"), ("presupuesto_calculado", "pendiente_aprobacion"),
                               ("gate_decision_registrada", "perdida")] and c["historial_cuadra"] is True,
                  f"({pares(c)})")

    # ==================================================================
    print("\nCASO 9 - Otras oportunidades del mismo email")
    # ==================================================================
    with aislado("CASO 9"):
        C_9A, C_9B = ficha_ok(OP_9A), ficha_ok(OP_9B)
        comprobar("9A lista solo a 9B, y 9B solo a 9A (nunca a sí misma ni a 9C)",
                  [o["oportunidad_id"] for o in C_9A["otras_oportunidades_mismo_email"]] == [OP_9B]
                  and [o["oportunidad_id"] for o in C_9B["otras_oportunidades_mismo_email"]] == [OP_9A])
        comprobar("cada elemento con sus 4 claves y el estado 'nueva'",
                  all(set(o) == CLAVES_OTRA and o["estado_oportunidad"] == "nueva"
                      for o in C_9A["otras_oportunidades_mismo_email"] + C_9B["otras_oportunidades_mismo_email"]))
        comprobar("el contacto de cada ficha es el de SU solicitud",
                  C_9A["contacto"]["nombre"] == "Nueve Primera" and C_9B["contacto"]["nombre"] == "Nueve Segunda"
                  and C_9A["contacto"]["telefono"] == "600000091" and C_9B["contacto"]["telefono"] == "600000092")
        comprobar("9C (otro email) no lista a nadie", ficha_ok(OP_9C)["otras_oportunidades_mismo_email"] == [])

    # ==================================================================
    print("\nCASO 7 - Claves EXACTAS en cada nivel (copiadas del plan)")
    # ==================================================================
    with aislado("CASO 7"):
        # Sobre TODAS las fichas 200 recibidas hasta aquí.
        malas = []
        for c in cuerpos_200:
            niveles = [(set(c), CLAVES_RAIZ), (set(c["contacto"]), CLAVES_CONTACTO), (set(c["reforma"]), CLAVES_REFORMA)]
            niveles += [(set(c["presupuesto"]), CLAVES_PRESUPUESTO)] if c["presupuesto"] is not None else []
            niveles += [(set(v), CLAVES_VISITA) for v in c["visitas"]] + [(set(x), CLAVES_LLAMADA) for x in c["llamadas"]]
            niveles += [(set(e), CLAVES_EVENTO) for e in c["historial"]]
            niveles += [(set(o), CLAVES_OTRA) for o in c["otras_oportunidades_mismo_email"]]
            if any(tiene != esperadas for tiene, esperadas in niveles):
                malas.append(c["oportunidad_id"])
        comprobar(f"claves exactas en todos los niveles de las {len(cuerpos_200)} fichas", not malas, f"({malas})")
        prohibidas = set().union(*(claves_anidadas(c) for c in cuerpos_200)) & CLAVES_PROHIBIDAS
        comprobar("ninguna clave prohibida (2.6) en ningún nivel", not prohibidas, f"({sorted(prohibidas)})")
        estados = {c["estado_oportunidad"] for c in cuerpos_200} | {e["estado"] for c in cuerpos_200 for e in c["historial"]}
        comprobar("estados de oportunidad dentro de los 8 del CHECK", estados <= set(ESTADOS_CHECK) and len(ESTADOS_CHECK) == 8,
                  f"({sorted(estados)})")
        estados_v = {v["estado_visita"] for c in cuerpos_200 for v in c["visitas"]}
        comprobar("estados de visita dentro de los 4 del CHECK",
                  estados_v <= set(ESTADOS_VISITA_CHECK) and len(ESTADOS_VISITA_CHECK) == 4, f"({sorted(estados_v)})")

    # ==================================================================
    print("\nCASO 8 - logs.detalle no sale")
    # ==================================================================
    with aislado("CASO 8"):
        for etiqueta, op, textos in (("T6", OP_T6, [TEXTO_T6]), ("T7", OP_T7, [TEXTO_T7A, TEXTO_T7B])):
            r = ficha(op)
            comprobar(f"{etiqueta}: cada texto_cliente aparece UNA vez (en su visita)",
                      all(r.text.count(t) == 1 for t in textos), f"({[r.text.count(t) for t in textos]})")
            detalle = claves_anidadas(r.json()) & CLAVES_DETALLE
            comprobar(f"{etiqueta}: ninguna clave de un detalle de log", not detalle, f"({sorted(detalle)})")

    # ==================================================================
    print("\nCASO 10 - Solo lectura")
    # ==================================================================
    with aislado("CASO 10"):
        ANTES = foto_propia()
        # Una tanda de 200, 404, 401 y 422 sobre oportunidades propias.
        for op in (OP_T1, OP_T2, OP_T3, OP_T4, OP_T5, OP_T6, OP_T7, OP_T8, OP_T10, OP_C6, OP_9A):
            ficha(op)
        ficha(MAXIMO + 1_000_000)
        ficha(OP_T2, AUTH_WEBHOOK)
        ficha("0")
        DESPUES = foto_propia()
        comprobar("recuentos propios de las 7 tablas iguales", ANTES[0] == DESPUES[0], f"({DESPUES[0]})")
        comprobar("updated_at y fecha_ultimo_contacto de las oportunidades propias iguales", ANTES[1] == DESPUES[1],
                  f"({len(DESPUES[1])} oportunidades)")

    # ==================================================================
    print("\nCASO 11 - Repetición")
    # ==================================================================
    with aislado("CASO 11"):
        r1, r2 = ficha(OP_T4), ficha(OP_T4)
        comprobar("dos peticiones seguidas dan el mismo cuerpo, byte a byte",
                  r1.status_code == 200 and r1.content == r2.content)

    # ==================================================================
    print("\nCASO 12 - Coherencia con /gate-avisos")
    # ==================================================================
    with aislado("CASO 12"):
        AVISO = requests.get(f"{BASE}/gate-avisos/{OP_T3}", headers=AUTH_GATE, timeout=30).json()
        comprobar("T3: contacto, reforma y fotos iguales a los de /gate-avisos",
                  [C_T3["contacto"], C_T3["reforma"], C_T3["fotos"]] == [AVISO["contacto"], AVISO["reforma"], AVISO["fotos"]])
        comprobar("T3: los 8 campos de presupuesto iguales a los de /gate-avisos",
                  {k: v for k, v in C_T3["presupuesto"].items() if k != "requiere_aprobacion"} == AVISO["presupuesto"])
        r = requests.get(f"{BASE}/gate-avisos/{OP_T2}", headers=AUTH_GATE, timeout=30)
        comprobar("/gate-avisos sigue dando 409 sin_gate a T2",
                  r.status_code == 409 and r.json()["detail"]["motivo"] == "sin_gate", f"({r.status_code})")

    # ==================================================================
    print("\nCASO 13 - Ningún 500")
    # ==================================================================
    with aislado("CASO 13"):
        comprobar("ninguna respuesta 500 de la ficha", 500 not in codigos_ficha,
                  f"({len(codigos_ficha)} peticiones, códigos {sorted(set(codigos_ficha))})")

finally:
    # Apagado ordenado del servidor propio (el hilo de este proceso).
    servidor.should_exit = True
    hilo.join(timeout=15)

    # Logs del manejador global (500) de la ficha: sin marca. Se borran por
    # id EXACTO solo si son tantos como respuestas 500.
    n_500 = codigos_ficha.count(500)
    # SQL: logs error_no_controlado de rutas /oportunidades/ desde la foto.
    logs_500 = [f[0] for f in consultar_todas(
        "SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' "
        "AND detalle->>'ruta' LIKE '/oportunidades/%%' ORDER BY id;", (FOTO["logs"][0],))]
    print(f"\n  Respuestas 500 de la ficha: {n_500}; logs error_no_controlado de esa ruta: {logs_500}")
    comprobar("los logs de 500 de la ficha son exactamente los esperados", len(logs_500) == n_500,
              f"({len(logs_500)} logs, {n_500} respuestas 500)")
    if logs_500 and len(logs_500) == n_500:
        # SQL: se borran por sus ids exactos.
        cur.execute("DELETE FROM logs WHERE id = ANY(%s);", (logs_500,))
        cn.commit()
        print(f"  Borrados por id exacto: {logs_500}")
    # El log 'sistema' de T10, por su id exacto (y solo si es el esperado).
    if LOG_SISTEMA is not None:
        # SQL: borra ese log, comprobando que es el 'sistema' de T10.
        cur.execute("DELETE FROM logs WHERE id = %s AND entity_type = 'sistema' AND entity_id = %s;",
                    (LOG_SISTEMA, OP_T10))
        borrados = cur.rowcount
        cn.commit()
        comprobar("log 'sistema' de T10 borrado por id exacto", borrados == 1, f"(id {LOG_SISTEMA})")

    # Limpieza de lo propio y comprobación de restos.
    limpiar()
    restos = consultar(f"SELECT count(*) FROM clientes WHERE {PROPIO['clientes']};", {"patron": PATRON_EMAIL})[0]
    comprobar("no queda ningún dato propio", restos == 0, f"({restos})")

    # ==================================================================
    print("\nCASO 14 - No toca nada ajeno")
    # ==================================================================
    for tabla, (id_base, n) in FOTO.items():
        ahora = consultar(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                          {"patron": PATRON_EMAIL, "base": id_base})[0]
        nuevas = consultar(f"SELECT count(*) FROM {tabla} WHERE id > %(base)s AND NOT ({PROPIO[tabla]});",
                           {"patron": PATRON_EMAIL, "base": id_base})[0]
        comprobar(f"{tabla}: ajenas antiguas iguales ({n})", ahora == n, f"(ahora {ahora}; nuevas de otros: {nuevas})")
    cn.close()

# Resultado final y código de salida (0 solo si todo está bien).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLAN:", fallos)
sys.exit(0 if not fallos else 1)
