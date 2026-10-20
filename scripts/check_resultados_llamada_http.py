"""
Verificación por HTTP REAL de POST /resultados-llamada (plan
docs/Plan_Resultado_Llamadas.txt, secciones 8.2 y 8.3 con sus notas).

Levanta el servidor en un hilo de este proceso (sin --reload, puerto 8028,
nunca el 8000), crea sus propios clientes por POST /leads y
/calculate-estimate (y, según el caso, POST /visits y POST
/create-followup-task, como lo harán n8n y el Agente 2) y prueba:
  CASO 1  autenticación (401)
  CASO 2  forma (422 de Pydantic), cada combinación prohibida
  CASO 3  404
  CASO 4  409 de estado: sin_presupuesto, estado_no_permitido y los
          criterios de b)
  CASO 5  Gate: visita_acordada (todos sus efectos), descartar con
          'sin_decision', fechas 422 y visita_activa_existente
  CASO 6  seguimiento abierto (c) y por abrir (b): visita_acordada y descartar
  CASO 7  confirmación: confirmar_hora, otra_hora, cliente_cancela,
          visita_cambiada, misma_hora_pedida y la pedida ya pasada
  CASO 8  resultado_no_admitido en los dos sentidos
  CASO 9  repetición (200 sin escribir) y resultado_ya_registrado
  CASO 10 el alias: misma fila, sus motivos de siempre, 'sin_decision' 422
  CASO 11 la ficha: llamadas, visita cancelada, historial y llamada_registrable
  CASO 12 la matriz lista <-> registro (pre-decisión 4 con C1)
  CASO 13 concurrencia
  CASO 14 ningún 500, 201 <-> creado, claves exactas, 0 importes
  (D33, 2026-10-14, plan docs/Plan_Fecha_Fin_Visita_D33.txt: fecha_fin_visita
  en cada 201 de verificar_201, en las repeticiones del CASO 9 y en el alias
  del CASO 10: fecha_visita + la duración leída aquí de reglas_negocio, con el
  mismo texto y desfase; null sin visita.)

Las 4 preguntas: (1) escribe en tablas reales SOLO filas propias (emails
http-resultado-<8>@example.com, tokens uuid4), con commit; envejece a mano
el presupuesto de oportunidades PROPIAS (UPDATE de created_at) y, en dos
casos, cambia a mano el estado o el contacto de una oportunidad PROPIA;
(2) no pasa ninguna conexión al código (todo por HTTP); (3) si se corta, la
siguiente ejecución borra lo propio al empezar, y "no toca nada ajeno"
(id_base) lo comprueba; los logs de un 500 se borran por id exacto tras
comprobarlos; (4) puerto 8028, ningún dato real (nunca los 8 de D25.16 ni
1522).
"""

# json: buscar importes en el texto. socket, threading, time, uuid: puerto,
# servidor, esperas y tokens. ThreadPoolExecutor: peticiones a la vez.
import json
import socket
import sys
# threading: el servidor corre en un hilo; time: esperas.
import threading
import time
# uuid: tokens únicos en cada ejecución.
import uuid
# ThreadPoolExecutor: varias peticiones a la vez (concurrencia).
from concurrent.futures import ThreadPoolExecutor
# Fechas en Madrid; Path para las rutas (timezone: el fin se suma en UTC, D33).
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# La raíz del repositorio (carpeta padre de scripts/) en sys.path.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: preparar, mirar y limpiar. requests: HTTP. uvicorn: servidor.
import psycopg2
import requests
import uvicorn

# Los secretos del .env: solo en cabeceras, nunca se imprimen.
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET

# B5-0 (P21): las fechas propias de este script, en scripts/fechas_prueba.py.
import fechas_prueba

# Puerto propio (8028, plan K11) y dirección base.
PUERTO = 8028
BASE = f"http://127.0.0.1:{PUERTO}"
# Llave de n8n (leads, cálculo, visitas) y de administración (el resto).
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# La marca propia.
PATRON = "http-resultado-%@example.com"
# Las claves EXACTAS de la respuesta (plan, 2.5; D33 añade fecha_fin_visita).
CLAVES_RESPUESTA = {"llamada_id", "oportunidad_id", "tipo_llamada", "resultado", "motivo", "estado_oportunidad",
                    "visita_id", "fecha_visita", "fecha_fin_visita", "visita_cancelada_id", "creado"}
# D33: los resultados que dejan una visita confirmada (y, por tanto, un fin).
CON_VISITA = ("visita_acordada", "confirmar_hora", "otra_hora")
# Los textos fijos de las visitas confirmadas por teléfono (P2 y P8), copiados
# del PLAN, no del código.
TEXTO_GATE = "Visita acordada por teléfono por administración tras la revisión del Gate."
TEXTO_SEGUIMIENTO = "Visita acordada por teléfono por administración en la llamada de seguimiento."
TEXTO_OTRA_HORA = "Visita confirmada por teléfono por administración a otra hora; la pedida desde el chat queda cancelada."
TEXTO_CLIENTE = "Prefiero por la mañana."

# Contadores, y registros de todas las respuestas de la ruta nueva.
ok, fallos, codigos, cuerpos, pares = 0, [], [], [], []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si la condición es verdadera; [FALLO] (y se apunta) si no."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta: se suma y se imprime.
    if condicion:
        ok += 1
        print(f"  [OK] {titulo}")
    # Incorrecta: se apunta y se imprime con el detalle.
    else:
        fallos.append(titulo)
        print(f"  [FALLO] {titulo} {detalle}")


# Conexión propia para preparar, mirar y limpiar.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def consultar(sql, params=()):
    """Todas las filas de un SELECT, y commit para ver lo último escrito."""
    # SQL: el que llega como argumento (textos fijos de este archivo).
    cur.execute(sql, params)
    # Las filas, y se cierra la transacción de lectura.
    filas = cur.fetchall()
    cn.commit()
    return filas


# Qué es propio: lo que cuelga de los clientes con la marca.
SQL_OPS = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
# Por tabla, la condición de sus filas propias, en el orden de borrado.
PROPIO = {
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPS})",
    "visitas": f"oportunidad_id IN ({SQL_OPS})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPS})",
    "oportunidades": f"id IN ({SQL_OPS})",
    "leads": "cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s)",
    "clientes": "email LIKE %(p)s",
}


def limpiar():
    """Borra SOLO lo propio, en el orden de las claves foráneas, y commit."""
    # Una tabla tras otra.
    for tabla, condicion in PROPIO.items():
        # SQL: DELETE de las filas propias de esa tabla.
        cur.execute(f"DELETE FROM {tabla} WHERE {condicion};", {"p": PATRON})
    # Un solo commit.
    cn.commit()


def foto_ajena():
    """Por tabla: (id_base, filas NO propias con id <= id_base)."""
    # Diccionario tabla -> (id_base, ajenas).
    foto = {}
    for tabla, condicion in PROPIO.items():
        # SQL: el id más alto ahora.
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        base = cur.fetchone()[0]
        # SQL: cuántas filas NO propias hay hasta él.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(b)s AND NOT ({condicion});", {"p": PATRON, "b": base})
        foto[tabla] = (base, cur.fetchone()[0])
    # commit: cierra la transacción de lectura.
    cn.commit()
    return foto


def pedir(metodo, ruta, cuerpo=None, cabeceras=AUTH_GATE):
    """Una petición HTTP real; apunta el código. Devuelve la respuesta."""
    # requests.request: GET o POST, con el cuerpo JSON si lo hay.
    r = requests.request(metodo, f"{BASE}{ruta}", headers=cabeceras, json=cuerpo, timeout=60)
    # Se apunta el código, para "ningún 500".
    codigos.append(r.status_code)
    return r


def registrar(op, cuerpo, cabeceras=AUTH_GATE):
    """POST /resultados-llamada; guarda el cuerpo y el par (código, creado)."""
    # El cuerpo con el oportunidad_id y un informe (si no lo trae ya).
    r = pedir("POST", "/resultados-llamada", {"oportunidad_id": op, "informe": "Llamada de prueba.", **cuerpo}, cabeceras)
    # Para el CASO 14: cada cuerpo y, en los éxitos, el par código/creado.
    j = cuerpo_de(r)
    cuerpos.append(j)
    # En los éxitos, el par (código, creado) para comprobar que coinciden.
    if r.status_code in (200, 201):
        pares.append((r.status_code, j.get("creado")))
    return r


def cuerpo_de(r):
    """El JSON de la respuesta, o {} si no es JSON."""
    # Si no es JSON, r.json() lanza ValueError.
    try:
        return r.json()
    except ValueError:
        return {}


def motivo(r):
    """El motivo de un rechazo de negocio ({"detail": {"motivo": ...}}), o None."""
    # Un 422 de Pydantic trae una lista, no un objeto.
    d = cuerpo_de(r).get("detail")
    return d.get("motivo") if isinstance(d, dict) else None


def lead(estructural=False, calcular=True):
    """Lead propio por POST /leads (y su cálculo). Devuelve (token, oportunidad_id)."""
    # Token único; sus 8 primeros caracteres van en el email (la marca).
    token = str(uuid.uuid4())
    r = pedir("POST", "/leads", {
        "nombre": "Prueba resultado", "email": f"http-resultado-{token[:8]}@example.com", "telefono": "600000000",
        "tipo_reforma": "bano", "m2": 6, "nivel_acabados": "medio",
        "incluye_cambios_estructurales": estructural, "lead_token": token}, AUTH_WEBHOOK)
    # Sin datos de prueba no tiene sentido seguir: se para con el error.
    assert r.status_code == 201, r.text
    op = r.json()["oportunidad_id"]
    # El cálculo (con cambios estructurales queda con Gate).
    # El cálculo: con cambios estructurales queda con Gate; sin el cálculo, sin presupuesto.
    if calcular:
        c = pedir("POST", "/calculate-estimate", {"oportunidad_id": op}, AUTH_WEBHOOK)
        # Sin cálculo no hay caso: se para con el error.
        assert c.status_code == 200, c.text
    return token, op


def envejecer(op, horas):
    """El presupuesto de una oportunidad PROPIA, creado hace esas horas."""
    # SQL: UPDATE del presupuesto, solo si la oportunidad es propia.
    cur.execute(f"UPDATE presupuestos SET created_at = now() - make_interval(hours => %(h)s) "
                f"WHERE oportunidad_id = %(op)s AND oportunidad_id IN ({SQL_OPS});", {"h": horas, "op": op, "p": PATRON})
    # Se confirma, para que el servidor lo vea.
    cn.commit()


def forzar(op, columna_valor):
    """Cambia a mano UNA columna de una oportunidad PROPIA ("estado = ..." o
    "fecha_ultimo_contacto = now()"); textos fijos de este archivo."""
    # SQL: UPDATE de la oportunidad, solo si es propia.
    cur.execute(f"UPDATE oportunidades SET {columna_valor} WHERE id = %(op)s AND id IN ({SQL_OPS});", {"op": op, "p": PATRON})
    # Se confirma.
    cn.commit()


def visita_pedida(token, dia, hora):
    """POST /visits del cliente (como el Agente 2). Devuelve la respuesta."""
    # La visita pedida desde el chat, con su texto.
    return pedir("POST", "/visits", {"lead_token": token, "fecha": str(dia), "hora": hora,
                                     "texto_cliente": TEXTO_CLIENTE}, AUTH_WEBHOOK)


def seguimiento_abierto():
    """Un cliente sin Gate con su seguimiento ABIERTO (c). Devuelve (token, op)."""
    # Presupuesto de hace 50 h y create-followup-task.
    token, op = lead()
    envejecer(op, 50)
    # Y el seguimiento abierto por su endpoint (sin él, el caso no existe: se para).
    r = pedir("POST", "/create-followup-task", {"oportunidad_id": op, "motivo": "sin_respuesta_visita"})
    assert r.status_code == 201, r.text
    return token, op


# D32: la hora por defecto es 09:45, de la rejilla (antes 10:00).
def confirmacion(dia, hora="09:45"):
    """Un cliente sin Gate con una visita 'solicitada' ese día. Devuelve (token, op)."""
    # El lead y su visita pedida.
    token, op = lead()
    # Su visita pedida desde el chat (sin ella no hay nada que confirmar: se para).
    r = visita_pedida(token, dia, hora)
    assert r.status_code == 201, r.text
    return token, op


def foto_op(op):
    """Todo lo que un registro puede cambiar en una oportunidad (si dos fotos
    son iguales, entre una y otra no se ha escrito nada)."""
    # SQL: filas, visitas (estado y fecha), logs, estado y fechas.
    return consultar(
        """
        SELECT (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s),
               (SELECT string_agg(estado || '@' || fecha_propuesta::text, ',' ORDER BY id) FROM visitas WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s),
               o.estado, o.fecha_ultimo_contacto, o.updated_at
        FROM oportunidades o WHERE o.id = %(op)s;
        """, {"op": op})[0]


def fila_resultado(op):
    """(tipo, resultado, motivo, visita_id, visita_cancelada_id, informe) de su fila, o None."""
    # SQL: la fila de decisiones_gate de la oportunidad.
    filas = consultar("SELECT tipo_llamada, decision, motivo, visita_id, visita_cancelada_id, informe "
                      "FROM decisiones_gate WHERE oportunidad_id = %s;", (op,))
    return filas[0] if filas else None


def logs_de(op):
    """[(acción, detalle)] de los logs de resultado de la oportunidad."""
    # SQL: los logs de las dos acciones de resultado, en orden.
    return consultar("SELECT accion, detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                     "AND accion IN ('gate_decision_registrada', 'llamada_registrada') ORDER BY id;", (op,))


def madrid(dia: date, hh: int, mm: int) -> str:
    """Ese instante en Madrid, como lo escribe la API ("...T10:00:00+01:00")."""
    # combine con la zona: el desfase de ese día; isoformat lo escribe.
    return datetime.combine(dia, datetime.min.time().replace(hour=hh, minute=mm), tzinfo=MADRID).isoformat()


def fin_correcto(j, con_visita):
    """
    D33: True si fecha_fin_visita es la que toca. Con visita: el texto de
    fecha_visita + DURACION minutos reales (en UTC), en Madrid, escrito igual
    que la API (mismo formato y desfase). Sin visita: las dos claves null.
    """
    # Sin visita: las dos presentes y null.
    if not con_visita:
        return "fecha_fin_visita" in j and j["fecha_fin_visita"] is None and j.get("fecha_visita") is None
    # Con visita y sin fecha_visita: mal (no hay de dónde partir).
    if not j.get("fecha_visita"):
        return False
    # El inicio como datetime y el fin esperado, sumado en UTC.
    inicio_v = datetime.fromisoformat(j["fecha_visita"])
    esperado = (inicio_v.astimezone(timezone.utc) + timedelta(minutes=DURACION)).astimezone(MADRID)
    # El mismo texto: mismo instante, desfase y formato.
    return j.get("fecha_fin_visita") == esperado.isoformat()


def verificar_201(titulo, r, op, foto_antes, tipo, resultado, estado, visitas, texto=None, motivo_esperado=None):
    """
    Todo lo de un 201: respuesta (claves, tipo, estado), fila, visitas, estado,
    fecha_ultimo_contacto, log con 'estado' y sin informe, presupuesto intacto.
    visitas: la lista de "estado" de las visitas de la oportunidad, en orden.
    """
    # La respuesta.
    j = cuerpo_de(r)
    comprobar(f"{titulo}: 201, creado, tipo {tipo}, '{estado}', claves exactas",
              r.status_code == 201 and j.get("creado") is True and j.get("tipo_llamada") == tipo
              and j.get("resultado") == resultado and j.get("estado_oportunidad") == estado and set(j) == CLAVES_RESPUESTA,
              f"({r.status_code} {r.text[:200]})")
    # D33: el fin de la visita (o null sin visita).
    comprobar(f"{titulo}: fecha_fin_visita " + (f"= fecha_visita + {DURACION} min" if resultado in CON_VISITA else "null"),
              fin_correcto(j, resultado in CON_VISITA), f"({j.get('fecha_visita')} -> {j.get('fecha_fin_visita')})")
    # La fila: tipo, resultado, motivo, informe recortado.
    f = fila_resultado(op)
    comprobar(f"{titulo}: fila con tipo, resultado, motivo e informe",
              f is not None and f[:3] == (tipo, resultado, motivo_esperado) and f[5] == "Llamada de prueba."
              and f[0] == j.get("tipo_llamada") and f[3] == j.get("visita_id") and f[4] == j.get("visita_cancelada_id"), f"({f})")
    # Las visitas, su estado y (si se dice) el texto de la confirmada.
    filas_v = consultar("SELECT estado, texto_cliente FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (op,))
    comprobar(f"{titulo}: visitas {visitas}" + (f" y texto fijo" if texto else ""),
              [e for e, _ in filas_v] == visitas and (texto is None or (filas_v and filas_v[-1][1] == texto)), f"({filas_v})")
    # La oportunidad: estado y fecha_ultimo_contacto (antes NULL, ahora escrita).
    o = consultar("SELECT estado, fecha_ultimo_contacto IS NOT NULL FROM oportunidades WHERE id = %s;", (op,))[0]
    comprobar(f"{titulo}: oportunidad '{estado}' y fecha_ultimo_contacto escrita (P7)",
              o == (estado, True), f"({o})")
    # El log: uno, con 'estado' y sin el informe; la acción según el tipo (L-b).
    logs = logs_de(op)
    accion = "gate_decision_registrada" if tipo == "gate" else "llamada_registrada"
    comprobar(f"{titulo}: 1 log {accion} con 'estado' y sin informe",
              len(logs) == 1 and logs[0][0] == accion and logs[0][1].get("estado") == estado
              and "informe" not in json.dumps(logs[0][1], ensure_ascii=False), f"({logs})")
    # El presupuesto no se toca (aprobado_por sigue NULL).
    p = consultar("SELECT aprobado_por FROM presupuestos WHERE oportunidad_id = %s;", (op,))
    comprobar(f"{titulo}: presupuesto intacto (aprobado_por NULL)", p == [(None,)], f"({p})")
    return j


def puerto_ocupado(puerto):
    """True si algo escucha en 127.0.0.1:puerto."""
    # "with" cierra el socket; connect_ex da 0 si alguien contesta.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# Fechas: el próximo lunes (siempre futuro), su sábado y ayer, en Madrid.
MADRID = ZoneInfo("Europe/Madrid")
HOY = datetime.now(MADRID).date()
# B5-0 (2026-10-12, P21): los días laborables del bloque de semanas lejanas
# de este script (fechas_prueba.py), uno por oportunidad que crea o confirma
# una visita: con D31, dos visitas de oportunidades distintas en el mismo
# hueco darían 409 hueco_ocupado. El lunes es el primero (antes, "el lunes
# de dentro de dos semanas"); en él solo quedan los 422 de fecha y la visita
# hecha a mano del Gate g4.
DIAS = fechas_prueba.Dias("check_resultados_llamada_http", HOY)
LUNES = DIAS.siguiente()
SABADO, AYER = LUNES + timedelta(days=5), HOY - timedelta(days=1)
# B5-0: un día propio para cada oportunidad con visita (en este orden).
D_G1, D_S1, D_S3, D_K1, D_K2, D_K3, D_K4, D_K5, D_C1, D_C2, D_C3, D_C4 = (DIAS.siguiente() for _ in range(12))
# B5-0: antes de crear nada, ninguna visita activa AJENA en el bloque.
fechas_prueba.exigir_libres("check_resultados_llamada_http", PATRON,
                            [fechas_prueba.tramo_del_bloque("check_resultados_llamada_http", HOY)])
# D33: la duración REAL de la visita, leída aquí aparte (no con el código
# probado), para el fin esperado (fin_correcto).
DURACION = int(consultar("SELECT valor FROM reglas_negocio WHERE clave = 'duracion_visita_min';")[0][0])

# El puerto tiene que estar libre: si no, se para y se avisa.
if puerto_ocupado(PUERTO):
    print(f"ABORTO: el puerto {PUERTO} está ocupado. No ejecuto nada ni mato nada.")
    sys.exit(2)
# Restos propios de una ejecución anterior, y la foto de lo ajeno.
limpiar()
FOTO = foto_ajena()
print(f"Fechas: hoy {HOY}, lunes de prueba {LUNES}")

# El servidor en un hilo de este proceso (sin --reload).
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
hilo = threading.Thread(target=servidor.run, daemon=True)
hilo.start()
# Se espera a /health como mucho 30 s; el "else" del while: no contestó.
inicio = time.time()
while time.time() - inicio < 30:
    # Si /health da 200, el servidor está listo.
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    # Todavía no escucha: se espera un poco y se repite.
    except requests.exceptions.RequestException:
        time.sleep(0.25)
# 30 s sin respuesta: se para.
else:
    print("El servidor no respondió en 30 s")
    sys.exit(1)
print(f"Servidor arriba en el {PUERTO}")

# try/finally: pase lo que pase, se apaga el servidor y se limpia.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación")
    # Un caso válido: si la puerta dejara pasar la llave equivocada, registraría.
    _, op_auth = lead(estructural=True)
    foto = foto_op(op_auth)
    valido = {"resultado": "descartar", "motivo": "precio"}
    # Sin cabecera, con la llave del Agente 2 en las dos cabeceras.
    for nombre, cab in (("sin cabecera", {}), ("WEBHOOK_SECRET en X-Gate-Secret", {"X-Gate-Secret": WEBHOOK_SECRET}),
                        ("X-Webhook-Secret", AUTH_WEBHOOK)):
        r = registrar(op_auth, valido, cab)
        comprobar(f"401 {nombre}", r.status_code == 401 and cuerpo_de(r) == {"detail": "No autorizado"}, f"({r.status_code})")
    # 401 antes del cuerpo: sin cabecera y con un cuerpo inválido, 401.
    r = registrar(op_auth, {"resultado": "no_existe"}, {})
    comprobar("401 antes de validar el cuerpo", r.status_code == 401, f"({r.status_code})")
    comprobar("ningún 401 ha escrito nada", foto_op(op_auth) == foto)

    # ------------------------------------------------------------------
    print("\nCASO 2 - Forma (422 de Pydantic)")
    # Cada combinación prohibida de 2.3 (con sus notas).
    L = str(LUNES)
    # Cada combinación prohibida de 2.3 (con sus notas): (nombre, cuerpo).
    CASOS_FORMA = [
        ("campo de más 'tipo'", {"resultado": "descartar", "motivo": "precio", "tipo": "gate"}),
        ("resultado fuera de lista ('aplazar')", {"resultado": "aplazar"}),
        ("motivo fuera de lista", {"resultado": "descartar", "motivo": "caro"}),
        ("visita_acordada sin fecha", {"resultado": "visita_acordada", "hora": "10:00"}),
        ("visita_acordada con motivo", {"resultado": "visita_acordada", "fecha": L, "hora": "10:00", "motivo": "precio"}),
        ("confirmar_hora sin hora", {"resultado": "confirmar_hora", "fecha": L}),
        ("otra_hora con motivo", {"resultado": "otra_hora", "fecha": L, "hora": "10:00", "motivo": "otro"}),
        ("descartar sin motivo", {"resultado": "descartar"}),
        ("descartar con fecha", {"resultado": "descartar", "motivo": "precio", "fecha": L}),
        ("cliente_cancela sin motivo", {"resultado": "cliente_cancela"}),
        ("cliente_cancela con hora", {"resultado": "cliente_cancela", "motivo": "precio", "hora": "10:00"}),
        ("cliente_cancela con 'no_contesta' (C6)", {"resultado": "cliente_cancela", "motivo": "no_contesta"}),
        ("cliente_cancela con 'sin_decision' (D29.3)", {"resultado": "cliente_cancela", "motivo": "sin_decision"}),
        ("informe de espacios", {"resultado": "descartar", "motivo": "precio", "informe": "   "}),
        ("informe de 2001", {"resultado": "descartar", "motivo": "precio", "informe": "a" * 2001}),
        ("hora 10:00Z", {"resultado": "visita_acordada", "fecha": L, "hora": "10:00Z"}),
    ]
    # Cada una: 422 con la lista de errores de Pydantic.
    for nombre, cuerpo in CASOS_FORMA:
        r = registrar(op_auth, cuerpo)
        comprobar(f"422 {nombre}", r.status_code == 422 and isinstance(cuerpo_de(r).get("detail"), list), f"({r.status_code})")
    # oportunidad_id 0 también es de forma.
    r = pedir("POST", "/resultados-llamada", {"oportunidad_id": 0, "resultado": "descartar", "motivo": "precio", "informe": "x"})
    comprobar("422 oportunidad_id 0", r.status_code == 422)
    comprobar("ningún 422 ha escrito nada", foto_op(op_auth) == foto)

    # ------------------------------------------------------------------
    print("\nCASO 3 - 404")
    # Ids que no existen, también los que no caben en la columna.
    for op_no in (2147483647, 2147483648, 99999999999999999999):
        r = registrar(op_no, {"resultado": "descartar", "motivo": "precio"})
        comprobar(f"404 id {op_no}", r.status_code == 404 and motivo(r) == "oportunidad_no_encontrada", f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 4 - 409 de estado (sin escribir nada)")
    # (preparación, motivo esperado)
    _, op_sin_pres = lead(calcular=False)
    _, op_reciente = lead()
    # contacto_registrado: plazo cumplido pero con un contacto puesto a mano.
    _, op_contacto = lead()
    envejecer(op_contacto, 50)
    forzar(op_contacto, "fecha_ultimo_contacto = now()")
    # con_gate: un Gate puesto a mano en 'presupuesto_enviado' con plazo cumplido.
    _, op_gate_pe = lead(estructural=True)
    envejecer(op_gate_pe, 50)
    forzar(op_gate_pe, "estado = 'presupuesto_enviado'")
    # estado_no_permitido: un estado sin llamada ('ganada', a mano).
    _, op_ganada = lead()
    forzar(op_ganada, "estado = 'ganada'")
    # Cada uno: el 409 de su motivo y nada escrito.
    for op, esperado in ((op_sin_pres, "sin_presupuesto"), (op_reciente, "plazo_no_cumplido"),
                         (op_contacto, "contacto_registrado"), (op_gate_pe, "con_gate"), (op_ganada, "estado_no_permitido")):
        antes = foto_op(op)
        r = registrar(op, {"resultado": "descartar", "motivo": "precio"})
        comprobar(f"409 {esperado}", r.status_code == 409 and motivo(r) == esperado and foto_op(op) == antes,
                  f"({r.status_code} {r.text[:150]})")

    # ------------------------------------------------------------------
    print("\nCASO 5 - Gate")
    # visita_acordada: todos sus efectos.
    _, g1 = lead(estructural=True)
    # B5-0: en su día propio (D_G1; antes, el lunes).
    j = verificar_201("Gate visita_acordada", registrar(g1, {"resultado": "visita_acordada", "fecha": str(D_G1), "hora": "08:30"}),
                      g1, foto_op(g1), "gate", "visita_acordada", "visita_agendada", ["confirmada"], TEXTO_GATE)
    comprobar("Gate visita_acordada: fecha_visita en Madrid con su desfase", j.get("fecha_visita") == madrid(D_G1, 8, 30),
              f"({j.get('fecha_visita')})")
    # descartar con 'sin_decision' (D29.3, solo ruta nueva).
    _, g2 = lead(estructural=True)
    verificar_201("Gate descartar 'sin_decision'", registrar(g2, {"resultado": "descartar", "motivo": "sin_decision"}),
                  g2, foto_op(g2), "gate", "descartar", "perdida", [], motivo_esperado="sin_decision")
    # Las reglas de la fecha (422 de negocio), sin escribir; desde D32, cuatro.
    _, g3 = lead(estructural=True)
    antes = foto_op(g3)
    # Las tres, sobre la misma oportunidad (un 422 no escribe).
    for nombre, dia, hora in (("fecha_pasada", AYER, "10:00"), ("fin_de_semana", SABADO, "10:00"), ("fuera_de_franja", LUNES, "12:45"),
                              # D32: dentro de la franja, fuera de la rejilla.
                              ("fuera_de_rejilla", LUNES, "10:00")):
        r = registrar(g3, {"resultado": "visita_acordada", "fecha": str(dia), "hora": hora})
        comprobar(f"Gate 422 {nombre}", r.status_code == 422 and motivo(r) == nombre, f"({r.status_code} {r.text[:120]})")
    # Y la foto de la oportunidad, igual que antes.
    comprobar("los 422 de fecha no escriben nada", foto_op(g3) == antes)
    # visita_activa_existente: un Gate con una visita CONFIRMADA hecha a mano.
    _, g4 = lead(estructural=True)
    # SQL: la visita, en una oportunidad PROPIA.
    cur.execute(f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                f"SELECT %(op)s, %(f)s, 'confirmada', 'hecha a mano' WHERE %(op)s IN ({SQL_OPS});",
                {"op": g4, "f": madrid(LUNES, 17, 0), "p": PATRON})
    cn.commit()
    # D32: 09:45 (antes 10:00): tiene que pasar la rejilla para llegar a P1.
    r = registrar(g4, {"resultado": "visita_acordada", "fecha": L, "hora": "09:45"})
    comprobar("Gate con visita activa -> 409 visita_activa_existente", r.status_code == 409 and motivo(r) == "visita_activa_existente",
              f"({r.status_code} {r.text[:120]})")

    # ------------------------------------------------------------------
    print("\nCASO 6 - Seguimiento: abierto (c) y por abrir (b)")
    # c) visita_acordada y descartar.
    _, s1 = seguimiento_abierto()
    verificar_201("c) visita_acordada", registrar(s1, {"resultado": "visita_acordada", "fecha": str(D_S1), "hora": "11:00"}),
                  s1, foto_op(s1), "seguimiento", "visita_acordada", "visita_agendada", ["confirmada"], TEXTO_SEGUIMIENTO)
    _, s2 = seguimiento_abierto()
    verificar_201("c) descartar 'no_contesta'", registrar(s2, {"resultado": "descartar", "motivo": "no_contesta"}),
                  s2, foto_op(s2), "seguimiento", "descartar", "perdida", [], motivo_esperado="no_contesta")
    # b) visita_acordada y descartar (sin pasar por create-followup-task).
    _, s3 = lead()
    envejecer(s3, 50)
    verificar_201("b) visita_acordada", registrar(s3, {"resultado": "visita_acordada", "fecha": str(D_S3), "hora": "12:15"}),
                  s3, foto_op(s3), "seguimiento", "visita_acordada", "visita_agendada", ["confirmada"], TEXTO_SEGUIMIENTO)
    # El mismo b) con descarte y el motivo nuevo.
    _, s4 = lead()
    envejecer(s4, 50)
    verificar_201("b) descartar 'sin_decision'", registrar(s4, {"resultado": "descartar", "motivo": "sin_decision"}),
                  s4, foto_op(s4), "seguimiento", "descartar", "perdida", [], motivo_esperado="sin_decision")
    # create-followup-task sobre un b) ya registrado: 409 estado_no_permitido.
    r = pedir("POST", "/create-followup-task", {"oportunidad_id": s4, "motivo": "sin_respuesta_visita"})
    comprobar("create-followup-task tras el registro -> 409 estado_no_permitido",
              r.status_code == 409 and motivo(r) == "estado_no_permitido", f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 7 - Confirmación de visitas pedidas desde el chat")
    # confirmar_hora: con otra hora, 409 visita_cambiada; con la pedida, 201.
    # B5-0: k1 a k5, cada una en su día propio (antes, todas el lunes a las 10:00).
    # D32: 09:45 (antes 10:00).
    _, k1 = confirmacion(D_K1, "09:45")
    antes = foto_op(k1)
    # Primero con OTRA hora (la que no pidió el cliente).
    # D32: 11:00, otra hora de la rejilla (antes 10:30).
    r = registrar(k1, {"resultado": "confirmar_hora", "fecha": str(D_K1), "hora": "11:00"})
    comprobar("confirmar_hora con otra hora -> 409 visita_cambiada (C11), sin escribir",
              r.status_code == 409 and motivo(r) == "visita_cambiada" and foto_op(k1) == antes, f"({r.status_code})")
    # Y con la hora pedida.
    verificar_201("confirmar_hora", registrar(k1, {"resultado": "confirmar_hora", "fecha": str(D_K1), "hora": "09:45"}),
                  k1, antes, "confirmacion_visita", "confirmar_hora", "visita_agendada", ["confirmada"], TEXTO_CLIENTE)
    # otra_hora: igual a la pedida, 422; a otra hora, 201 con la cancelada.
    _, k2 = confirmacion(D_K2, "09:45")
    antes = foto_op(k2)
    # La hora pedida y un sábado: 422 los dos, sin escribir.
    r = registrar(k2, {"resultado": "otra_hora", "fecha": str(D_K2), "hora": "09:45"})
    comprobar("otra_hora con la hora pedida -> 422 misma_hora_pedida", r.status_code == 422 and motivo(r) == "misma_hora_pedida")
    r = registrar(k2, {"resultado": "otra_hora", "fecha": str(SABADO), "hora": "10:00"})
    comprobar("otra_hora en sábado -> 422 fin_de_semana (validar_fecha)", r.status_code == 422 and motivo(r) == "fin_de_semana")
    # Y otra hora válida (17:00, dentro de la franja de tarde).
    j = verificar_201("otra_hora", registrar(k2, {"resultado": "otra_hora", "fecha": str(D_K2), "hora": "17:00"}),
                      k2, antes, "confirmacion_visita", "otra_hora", "visita_agendada", ["cancelada", "confirmada"], TEXTO_OTRA_HORA)
    comprobar("otra_hora: visita_id (la nueva) distinta de visita_cancelada_id (la pedida)",
              j.get("visita_id") and j.get("visita_cancelada_id") and j["visita_id"] != j["visita_cancelada_id"])
    # cliente_cancela: 201, perdida, visita cancelada, sin visita_id.
    _, k3 = confirmacion(D_K3, "09:45")
    j = verificar_201("cliente_cancela", registrar(k3, {"resultado": "cliente_cancela", "motivo": "plazo"}),
                      k3, foto_op(k3), "confirmacion_visita", "cliente_cancela", "perdida", ["cancelada"], motivo_esperado="plazo")
    comprobar("cliente_cancela: visita_id null y visita_cancelada_id la pedida",
              j.get("visita_id") is None and j.get("visita_cancelada_id") is not None)
    # La pedida ya pasada (C12): confirmar_hora da 422 fecha_pasada.
    _, k4 = confirmacion(D_K4, "09:45")
    # SQL: la visita pedida de una oportunidad PROPIA, movida a ayer a mano.
    cur.execute(f"UPDATE visitas SET fecha_propuesta = %(f)s WHERE oportunidad_id = %(op)s AND oportunidad_id IN ({SQL_OPS});",
                {"f": madrid(AYER, 10, 0), "op": k4, "p": PATRON})
    cn.commit()
    r = registrar(k4, {"resultado": "confirmar_hora", "fecha": str(AYER), "hora": "10:00"})
    comprobar("confirmar_hora sobre una pedida ya pasada -> 422 fecha_pasada (C12)",
              r.status_code == 422 and motivo(r) == "fecha_pasada", f"({r.status_code} {r.text[:120]})")

    # ------------------------------------------------------------------
    print("\nCASO 8 - resultado_no_admitido en los dos sentidos")
    # Un resultado del Gate en una confirmación...
    _, k5 = confirmacion(D_K5, "09:45")
    r = registrar(k5, {"resultado": "visita_acordada", "fecha": str(D_K5), "hora": "09:45"})
    comprobar("visita_acordada en una confirmación -> 409", r.status_code == 409 and motivo(r) == "resultado_no_admitido")
    # ...uno de la confirmación en un seguimiento...
    _, s5 = seguimiento_abierto()
    r = registrar(s5, {"resultado": "cliente_cancela", "motivo": "precio"})
    comprobar("cliente_cancela en un seguimiento -> 409", r.status_code == 409 and motivo(r) == "resultado_no_admitido")
    # ...y uno de la confirmación en un Gate.
    r = registrar(g3, {"resultado": "otra_hora", "fecha": L, "hora": "09:45"})
    comprobar("otra_hora en un Gate -> 409", r.status_code == 409 and motivo(r) == "resultado_no_admitido")

    # ------------------------------------------------------------------
    print("\nCASO 9 - Repetición (200 sin escribir) y resultado_ya_registrado")
    # La misma petición, con otro informe, sobre cada uno de los cinco.
    # (oportunidad, la petición que la registró).
    # B5-0: con el día propio de cada una.
    REPETIR = [(g1, {"resultado": "visita_acordada", "fecha": str(D_G1), "hora": "08:30"}),
               (g2, {"resultado": "descartar", "motivo": "sin_decision"}),
               (k1, {"resultado": "confirmar_hora", "fecha": str(D_K1), "hora": "09:45"}),
               (k2, {"resultado": "otra_hora", "fecha": str(D_K2), "hora": "17:00"}),
               (k3, {"resultado": "cliente_cancela", "motivo": "plazo"})]
    for op, cuerpo in REPETIR:
        # La foto antes; la misma petición con otro informe.
        antes, fila_antes = foto_op(op), fila_resultado(op)
        r = registrar(op, {**cuerpo, "informe": "Otro informe."})
        # 200, nada escrito y el informe original intacto.
        comprobar(f"repetición {cuerpo['resultado']} -> 200, mismo llamada_id, nada escrito, informe original",
                  r.status_code == 200 and cuerpo_de(r).get("llamada_id") is not None
                  and foto_op(op) == antes and fila_resultado(op) == fila_antes, f"({r.status_code})")
        # D33: la repetición también trae su fin (o null sin visita).
        comprobar(f"repetición {cuerpo['resultado']}: fecha_fin_visita correcta",
                  fin_correcto(cuerpo_de(r), cuerpo["resultado"] in CON_VISITA),
                  f"({cuerpo_de(r).get('fecha_visita')} -> {cuerpo_de(r).get('fecha_fin_visita')})")
        # Otro resultado sobre la misma: 409 resultado_ya_registrado.
        otro = {"resultado": "descartar", "motivo": "otro"} if cuerpo["resultado"] != "descartar" else \
               {"resultado": "descartar", "motivo": "precio"}
        r = registrar(op, otro)
        comprobar(f"otro resultado tras {cuerpo['resultado']} -> 409 resultado_ya_registrado",
                  r.status_code == 409 and motivo(r) == "resultado_ya_registrado" and foto_op(op) == antes, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 10 - El alias POST /gate-decisions")
    # El mismo descarte de Gate por el alias: su contrato y la fila con tipo gate.
    _, g5 = lead(estructural=True)
    r = pedir("POST", "/gate-decisions", {"oportunidad_id": g5, "decision": "descartar", "motivo": "precio", "informe": "x"})
    # D33 (P2): las 8 de siempre más fecha_fin_visita, null en un descarte.
    comprobar("alias: 201 con las 8 claves de siempre más fecha_fin_visita (null) y la fila con tipo_llamada 'gate'",
              r.status_code == 201 and set(cuerpo_de(r)) == {"decision_id", "oportunidad_id", "decision", "motivo",
                                                             "estado_oportunidad", "visita_id", "fecha_propuesta",
                                                             "fecha_fin_visita", "creado"}
              and cuerpo_de(r).get("fecha_fin_visita") is None
              and (fila_resultado(g5) or ("",))[0] == "gate", f"({r.status_code} {r.text[:150]})")
    # Y su log lo escribió el servicio NUEVO: la acción de siempre con las
    # claves de B2 (tipo_llamada y estado). Una copia del servicio antiguo
    # escribiría la misma fila (el DEFAULT pone 'gate') pero no estas claves (N6).
    logs = logs_de(g5)
    comprobar("alias: el log lleva tipo_llamada 'gate' y estado 'perdida' (lo escribe el servicio nuevo)",
              len(logs) == 1 and logs[0][1].get("tipo_llamada") == "gate" and logs[0][1].get("estado") == "perdida", f"({logs})")
    # Cada ruta ve la fila de la otra: la nueva da 409 resultado_ya_registrado,
    # el alias su decision_ya_registrada de siempre.
    r = registrar(g5, {"resultado": "descartar", "motivo": "otro"})
    comprobar("ruta nueva sobre un registro del alias -> 409 resultado_ya_registrado", motivo(r) == "resultado_ya_registrado")
    r = pedir("POST", "/gate-decisions", {"oportunidad_id": g1, "decision": "descartar", "motivo": "otro", "informe": "x"})
    comprobar("alias sobre un registro de la ruta nueva -> 409 decision_ya_registrada", motivo(r) == "decision_ya_registrada")
    # Un seguimiento por el alias: 409 sin_gate (el alias es solo Gate).
    _, s6 = seguimiento_abierto()
    r = pedir("POST", "/gate-decisions", {"oportunidad_id": s6, "decision": "descartar", "motivo": "precio", "informe": "x"})
    comprobar("alias con un seguimiento -> 409 sin_gate", r.status_code == 409 and motivo(r) == "sin_gate")
    # 'sin_decision' por el alias: 422 (su Enum no cambia).
    r = pedir("POST", "/gate-decisions", {"oportunidad_id": g3, "decision": "descartar", "motivo": "sin_decision", "informe": "x"})
    comprobar("alias con 'sin_decision' -> 422", r.status_code == 422)

    # ------------------------------------------------------------------
    print("\nCASO 11 - La ficha")
    # (oportunidad, tipo, resultado, ¿con visita cancelada?, último estado)
    for op, tipo, resultado, con_cancelada, estado in (
            (g1, "gate", "visita_acordada", False, "visita_agendada"), (s1, "seguimiento", "visita_acordada", False, "visita_agendada"),
            (k2, "confirmacion_visita", "otra_hora", True, "visita_agendada"), (k3, "confirmacion_visita", "cliente_cancela", True, "perdida")):
        f = cuerpo_de(pedir("GET", f"/oportunidades/{op}/ficha"))
        ll = f.get("llamadas") or [{}]
        comprobar(f"ficha {resultado}: llamada {tipo} con su informe, visita cancelada {'sí' if con_cancelada else 'no'}, "
                  f"llamada_registrable null, cuadra en '{estado}'",
                  (ll[0].get("tipo_llamada"), ll[0].get("resultado"), ll[0].get("informe")) == (tipo, resultado, "Llamada de prueba.")
                  and (ll[0].get("fecha_visita_cancelada") is not None) == con_cancelada and f.get("llamada_registrable") is None
                  and f.get("historial_cuadra") is True and (f.get("historial") or [{}])[-1].get("estado") == estado,
                  f"({ll[0]} {f.get('historial')})")
    # otra_hora: la cancelada es la de las 09:45 (D32; antes 10:00) y la confirmada, la de las 17:00.
    # (Nota 2026-10-11, tanda 2c: con .get y [{}], una ficha en 500 o sin
    # llamadas da FALLO contado; antes, N11 y N12 paraban el script con una traza.)
    f = cuerpo_de(pedir("GET", f"/oportunidades/{k2}/ficha"))
    l0 = (f.get("llamadas") or [{}])[0]
    comprobar("ficha otra_hora: fecha_visita 17:00 y fecha_visita_cancelada 09:45",
              l0.get("fecha_visita") == madrid(D_K2, 17, 0) and l0.get("fecha_visita_cancelada") == madrid(D_K2, 9, 45),
              f"({l0})")
    # Antes de registrar: llamada_registrable con el tipo y sus resultados.
    for op, tipo in ((g3, "gate"), (s5, "seguimiento"), (k5, "confirmacion_visita")):
        f = cuerpo_de(pedir("GET", f"/oportunidades/{op}/ficha"))
        comprobar(f"ficha sin registro: llamada_registrable tipo {tipo}",
                  (f.get("llamada_registrable") or {}).get("tipo_llamada") == tipo, f"({f.get('llamada_registrable')})")

    # ------------------------------------------------------------------
    print("\nCASO 12 - La matriz lista <-> registro")
    # El próximo laborable lo dice la propia lista.
    lista = cuerpo_de(pedir("GET", "/llamadas-del-dia"))
    DIA = date.fromisoformat(lista["dia_visitas"])
    # H-C-2 (2026-10-20; plan docs/Plan_Resumen_Solicitud_D28.txt, sección
    # 17). Antes, las cinco horas eran FIJAS (DIA 08:30, 09:45 y 12:15; DIA+7
    # 08:30 y 09:45), y una visita real del próximo laborable las bloqueaba.
    # El DÍA no se puede mover: el caso prueba el apartado e) (próximo
    # laborable). Se eligen las HORAS: las primeras libres de la rejilla, en
    # orden, con los MISMOS papeles que antes:
    #   DIA:      1.ª -> me (pedida y confirmar_hora a la misma hora),
    #             2.ª -> me_conf (visita_acordada), 3.ª -> md2 (otra_hora).
    #   OTRO_DIA: 1.ª -> md (pedida y confirmar_hora), 2.ª -> md2 (pedida).
    # OTRO_DIA: el primer laborable desde DIA + 7 con dos horas libres, como
    # mucho LIMITE_LABORABLES (10, el horizonte que el negocio ofrece al
    # cliente, dias_oferta_visita). La guarda exigir_libres no cambia.
    LIMITE_LABORABLES = 10

    def horas_libres(dia):
        """Las horas de inicio libres de ese día, en orden ("HH:MM"): las que
        ofrece GET /huecos-libres y que, además, ninguna visita AJENA ocupa
        según la MISMA consulta de la guarda (fechas_prueba.tramos_sin_ajenas)."""
        # Lo que ofrece el backend ese día (modo un día).
        j = cuerpo_de(pedir("GET", f"/huecos-libres?fecha={dia}"))
        # Mañana y tarde juntas, en orden ("08:30" < "17:00" como texto).
        candidatas = sorted(j.get("manana", []) + j.get("tarde", []))
        # El tramo de un minuto de cada una, como en la guarda.
        tramos = [(datetime.fromisoformat(madrid(dia, int(h[:2]), int(h[3:]))),
                   datetime.fromisoformat(madrid(dia, int(h[:2]), int(h[3:]))) + timedelta(minutes=1))
                  for h in candidatas]
        # Si son libres para la guarda (True / False, en el mismo orden).
        libres = fechas_prueba.tramos_sin_ajenas("check_resultados_llamada_http", PATRON, tramos)
        # Solo las libres, en orden.
        return [h for h, libre in zip(candidatas, libres) if libre]

    # Las tres del próximo laborable; sin tres, no se puede probar e): FALLO
    # y se para sin crear nada (el finally limpia y comprueba lo ajeno).
    LIBRES_DIA = horas_libres(DIA)
    if len(LIBRES_DIA) < 3:
        # La explicación, como la de la guarda.
        print(f"  [FALLO] check_resultados_llamada_http: el próximo laborable ({DIA}) no tiene 3 horas libres "
              f"({LIBRES_DIA}); no se crea ni se borra nada.")
        # Código 1.
        sys.exit(1)
    # Las tres primeras, en orden (H1 < H2 < H3).
    H1, H2, H3 = LIBRES_DIA[:3]
    # OTRO_DIA: el primer laborable desde DIA + 7 con dos horas libres.
    OTRO_DIA, O1, O2 = None, None, None
    # El primer candidato.
    candidato = DIA + timedelta(days=7)
    # Como mucho LIMITE_LABORABLES días laborables.
    for _ in range(LIMITE_LABORABLES):
        # Sábado (5) y domingo (6) no son laborables: se saltan.
        while candidato.weekday() >= 5:
            candidato += timedelta(days=1)
        # Sus horas libres.
        libres = horas_libres(candidato)
        # Con dos, ese es el día.
        if len(libres) >= 2:
            OTRO_DIA, O1, O2 = candidato, libres[0], libres[1]
            break
        # Si no, el siguiente día.
        candidato += timedelta(days=1)
    # Ninguno en el límite: FALLO y se para sin crear nada.
    if OTRO_DIA is None:
        # La explicación.
        print(f"  [FALLO] check_resultados_llamada_http: ningún laborable de los {LIMITE_LABORABLES} desde "
              f"{DIA + timedelta(days=7)} tiene 2 horas libres; no se crea ni se borra nada.")
        # Código 1.
        sys.exit(1)
    # Constancia (no cuenta como comprobación: el recuento no cambia).
    print(f"  [INFO] horas elegidas: DIA {DIA} {H1}, {H2}, {H3}; OTRO_DIA {OTRO_DIA} {O1}, {O2}")
    # B5-0: antes de crearlas, sus cinco horas no pueden estar ocupadas por
    # una visita AJENA (una real, por ejemplo): si lo están, FALLO con la
    # explicación y se para. Cada tramo es el minuto de inicio de la visita.
    # La guarda vuelve a comprobar las horas ELEGIDAS, como antes las fijas.
    fechas_prueba.exigir_libres("check_resultados_llamada_http", PATRON, [
        (datetime.fromisoformat(madrid(d, int(h[:2]), int(h[3:]))),
         datetime.fromisoformat(madrid(d, int(h[:2]), int(h[3:]))) + timedelta(minutes=1))
        for d, h in ((OTRO_DIA, O1), (OTRO_DIA, O2), (DIA, H1), (DIA, H2), (DIA, H3))])
    # a) Gate de más de 24 h; y uno de menos (no sale, pero se registra, D25.14).
    _, ma = lead(estructural=True)
    envejecer(ma, 30)
    _, ma_reciente = lead(estructural=True)
    # b) seguimiento por abrir; c) abierto; d) visita pedida otro día; e)
    # pedida para el próximo laborable, y una confirmada por el Gate ese día.
    # b) por abrir y c) abierto.
    _, mb = lead()
    envejecer(mb, 50)
    _, mc = seguimiento_abierto()
    # d): dos pedidas otro día; e): una pedida para el próximo laborable.
    # D32: horas de la rejilla.
    # H-C-2: con las horas elegidas (mismos papeles que las fijas de antes).
    _, md = confirmacion(OTRO_DIA, O1)
    _, md2 = confirmacion(OTRO_DIA, O2)
    _, me = confirmacion(DIA, H1)
    # e) confirmada: un Gate con visita acordada el próximo laborable.
    _, me_conf = lead(estructural=True)
    r = registrar(me_conf, {"resultado": "visita_acordada", "fecha": str(DIA), "hora": H2})
    # Sin ella no hay caso: se para.
    assert r.status_code == 201, r.text

    def apartados(listado, op):
        """En qué apartados de la lista sale la oportunidad."""
        # Los cinco apartados, por nombre.
        return [n for n in ("gates_sin_decision", "seguimientos_por_abrir", "seguimientos_abiertos",
                            "visitas_sin_confirmar", "visitas_proximo_laborable")
                if any(e["oportunidad_id"] == op for e in listado[n])]

    # La lista de antes.
    antes = cuerpo_de(pedir("GET", "/llamadas-del-dia"))
    ESPERADO = {ma: ["gates_sin_decision"], ma_reciente: [], mb: ["seguimientos_por_abrir"], mc: ["seguimientos_abiertos"],
                md: ["visitas_sin_confirmar"], md2: ["visitas_sin_confirmar"], me: ["visitas_proximo_laborable"],
                me_conf: ["visitas_proximo_laborable"]}
    for op, esperado in ESPERADO.items():
        comprobar(f"antes: oportunidad {op} sale en {esperado or 'ningún apartado'}", apartados(antes, op) == esperado,
                  f"({apartados(antes, op)})")
    # El registro de cada una (lo que sale en la lista se puede registrar).
    REGISTRO = {ma: {"resultado": "descartar", "motivo": "precio"},
                ma_reciente: {"resultado": "descartar", "motivo": "precio"},
                mb: {"resultado": "descartar", "motivo": "plazo"},
                mc: {"resultado": "descartar", "motivo": "otro"},
                # H-C-2: confirmar_hora a SU hora; otra_hora a la 3.ª del DIA.
                md: {"resultado": "confirmar_hora", "fecha": str(OTRO_DIA), "hora": O1},
                md2: {"resultado": "otra_hora", "fecha": str(DIA), "hora": H3},
                me: {"resultado": "confirmar_hora", "fecha": str(DIA), "hora": H1}}
    for op, cuerpo in REGISTRO.items():
        r = registrar(op, cuerpo)
        comprobar(f"oportunidad {op} ({cuerpo['resultado']}) -> 201", r.status_code == 201, f"({r.status_code} {r.text[:150]})")
    # La e) ya confirmada: sale (recordatorio) y no se puede registrar otra vez.
    r = registrar(me_conf, {"resultado": "descartar", "motivo": "precio"})
    comprobar("e) confirmada: no registrable (409 resultado_ya_registrado)", r.status_code == 409 and motivo(r) == "resultado_ya_registrado")
    # La lista de después: cada una ha salido de su apartado, salvo las
    # confirmadas para el próximo laborable, que están (o pasan) a e).
    despues = cuerpo_de(pedir("GET", "/llamadas-del-dia"))
    DESPUES = {ma: [], ma_reciente: [], mb: [], mc: [], md: [], md2: ["visitas_proximo_laborable"],
               me: ["visitas_proximo_laborable"], me_conf: ["visitas_proximo_laborable"]}
    for op, esperado in DESPUES.items():
        comprobar(f"después: oportunidad {op} en {esperado or 'ningún apartado'}", apartados(despues, op) == esperado,
                  f"({apartados(despues, op)})")

    # ------------------------------------------------------------------
    print("\nCASO 13 - Concurrencia")
    # 5 peticiones iguales a la vez: un 201, cuatro 200, una fila, una visita.
    _, c1 = lead(estructural=True)
    with ThreadPoolExecutor(5) as ex:
        # Las cinco a la vez, y sus códigos ordenados.
        # B5-0: en su día propio (D_C1).
        rs = list(ex.map(lambda _: registrar(c1, {"resultado": "visita_acordada", "fecha": str(D_C1), "hora": "08:30"}), range(5)))
    cods = sorted(r.status_code for r in rs)
    # Un 201, cuatro 200, una sola fila y una sola visita confirmada.
    comprobar("5 iguales: un 201 y cuatro 200, una fila y una visita",
              cods == [200, 200, 200, 200, 201] and foto_op(c1)[0] == 1 and foto_op(c1)[1].count("confirmada") == 1, f"({cods})")
    # 5 distintas a la vez: un 201 y cuatro 409.
    # B5-0: c2, c3 y c4, cada una en su día propio (antes, el lunes).
    _, c2 = confirmacion(D_C2, "09:45")
    # El día de c2 como texto, para las cinco peticiones.
    C2 = str(D_C2)
    # Las cinco distintas: confirmar, dos otra_hora y dos cancelaciones.
    # D32: horas de la rejilla (antes 10:00, 17:00 y 18:00).
    DISTINTAS = [{"resultado": "confirmar_hora", "fecha": C2, "hora": "09:45"},
                 {"resultado": "otra_hora", "fecha": C2, "hora": "17:00"}, {"resultado": "otra_hora", "fecha": C2, "hora": "18:15"},
                 {"resultado": "cliente_cancela", "motivo": "precio"}, {"resultado": "cliente_cancela", "motivo": "otro"}]
    # Las cinco a la vez y sus códigos.
    with ThreadPoolExecutor(5) as ex:
        rs = list(ex.map(lambda c: registrar(c2, c), DISTINTAS))
    cods = sorted(r.status_code for r in rs)
    # SQL: las visitas activas que quedan.
    activas = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s AND estado IN ('solicitada', 'confirmada');", (c2,))[0][0]
    comprobar("5 distintas: un 201 y cuatro 409, una fila, como mucho una visita activa",
              cods == [201, 409, 409, 409, 409] and foto_op(c2)[0] == 1 and activas <= 1, f"({cods}, activas {activas})")
    # Registro de un seguimiento y POST /visits del mismo cliente a la vez.
    tok3, c3 = seguimiento_abierto()
    with ThreadPoolExecutor(2) as ex:
        # El registro y la visita pedida, a la vez.
        f_reg = ex.submit(registrar, c3, {"resultado": "descartar", "motivo": "precio"})
        f_vis = ex.submit(visita_pedida, tok3, D_C3, "09:45")
        r_reg, r_vis = f_reg.result(), f_vis.result()
    # El estado final, y si es coherente con quién ganó.
    final = consultar("SELECT estado FROM oportunidades WHERE id = %s;", (c3,))[0][0]
    coherente = ((r_reg.status_code == 201 and r_vis.status_code == 409 and final == "perdida")
                 or (r_vis.status_code == 201 and r_reg.status_code == 409 and motivo(r_reg) == "resultado_no_admitido"
                     and final == "visita_agendada"))
    # Nunca un 500 y el final coherente.
    comprobar("registro y POST /visits a la vez: nunca 500, estado final coherente", coherente,
              f"(registro {r_reg.status_code} {motivo(r_reg)}, visits {r_vis.status_code}, final {final})")
    # confirmar_hora y POST /visits cambiando la hora, a la vez.
    tok4, c4 = confirmacion(D_C4, "09:45")
    with ThreadPoolExecutor(2) as ex:
        # confirmar_hora y un cambio de hora del cliente, a la vez.
        f_reg = ex.submit(registrar, c4, {"resultado": "confirmar_hora", "fecha": str(D_C4), "hora": "09:45"})
        f_vis = ex.submit(visita_pedida, tok4, D_C4, "11:00")
        r_reg, r_vis = f_reg.result(), f_vis.result()
    # SQL: las visitas activas que quedan (como mucho una).
    activas = consultar("SELECT estado, fecha_propuesta FROM visitas WHERE oportunidad_id = %s "
                        "AND estado IN ('solicitada', 'confirmada');", (c4,))
    # Si ganó la confirmación: confirmada; si ganó el cambio: la nueva pedida.
    coherente = ((r_reg.status_code == 201 and r_vis.status_code == 409 and [a[0] for a in activas] == ["confirmada"])
                 or (r_vis.status_code == 201 and r_reg.status_code == 409 and motivo(r_reg) == "visita_cambiada"
                     and [a[0] for a in activas] == ["solicitada"]))
    # Nunca un 500 y una visita activa coherente.
    comprobar("confirmar_hora y cambio de hora a la vez: nunca 500, una visita activa coherente", coherente,
              f"(registro {r_reg.status_code} {motivo(r_reg)}, visits {r_vis.status_code}, activas {activas})")
    # Registro de un b) y POST /create-followup-task a la vez.
    _, c5 = lead()
    envejecer(c5, 50)
    with ThreadPoolExecutor(2) as ex:
        # El registro y la apertura del seguimiento, a la vez.
        f_reg = ex.submit(registrar, c5, {"resultado": "descartar", "motivo": "plazo"})
        f_seg = ex.submit(pedir, "POST", "/create-followup-task", {"oportunidad_id": c5, "motivo": "sin_respuesta_visita"})
        r_reg, r_seg = f_reg.result(), f_seg.result()
    # Gane quien gane, el registro sale (como b) o como c)) y queda 'perdida'.
    final = consultar("SELECT estado FROM oportunidades WHERE id = %s;", (c5,))[0][0]
    comprobar("registro de un b) y create-followup-task a la vez: registro 201, nunca 500, 'perdida'",
              r_reg.status_code == 201 and r_seg.status_code in (201, 409) and final == "perdida",
              f"(registro {r_reg.status_code}, seguimiento {r_seg.status_code}, final {final})")

    # ------------------------------------------------------------------
    print("\nCASO 13b - D31: hueco ocupado en /resultados-llamada y en el alias")
    # Las reglas reales, leídas aquí aparte, para calcular las horas libres
    # esperadas sin usar el código probado. SQL: clave y valor de las seis
    # (D32: el desplazamiento en lugar del paso).
    R31 = {c: int(v) for c, v in consultar(
        "SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);",
        (["visita_manana_inicio_min", "visita_manana_fin_min", "visita_tarde_inicio_min",
          "visita_tarde_fin_min", "duracion_visita_min", "desplazamiento_min"],))}

    # Ayuda: las horas libres esperadas, calculadas por la prueba.
    def libres_esperados(ocupadas_min):
        """Las horas de inicio libres de un día lejano con esas visitas
        ocupadas (en minutos desde medianoche): desde D32, la rejilla de cada
        franja cada duración + desplazamiento, con la visita entera dentro
        y sin chocar (con el desplazamiento) con ninguna."""
        # La duración de una visita.
        dur = R31["duracion_visita_min"]
        # Lo que ocupa con el desplazamiento.
        ocupa = dur + R31["desplazamiento_min"]
        # Cada inicio de cada franja que no choca con ninguna ocupada.
        return [f"{m // 60:02d}:{m % 60:02d}"
                for ini, fin in (("visita_manana_inicio_min", "visita_manana_fin_min"),
                                 ("visita_tarde_inicio_min", "visita_tarde_fin_min"))
                for m in range(R31[ini], R31[fin] - dur + 1, ocupa)
                if all(abs(m - o) >= ocupa for o in ocupadas_min)]

    # Ayuda: el "detail" de un 409.
    def detalle_409(r):
        """El "detail" de un 409 (o {} si no lo es)."""
        # Solo un 409 con un diccionario dentro.
        d = cuerpo_de(r).get("detail") if r.status_code == 409 else None
        # El diccionario o vacío.
        return d if isinstance(d, dict) else {}

    # Tres días propios más del bloque (B5-0).
    D_H1, D_H2, D_H3 = DIAS.siguiente(), DIAS.siguiente(), DIAS.siguiente()
    # 1) Al revés que en /visits: un Gate quiere acordar una visita encima de
    #    la que otro cliente pidió desde el chat (09:45; D32: antes 10:00).
    _, kh1 = confirmacion(D_H1, "09:45")
    # El Gate, con su foto antes.
    _, gh1 = lead(estructural=True)
    # Lo que tiene antes de los 409 y del 422.
    antes = foto_op(gh1)
    # C3 del plan D32: 10:00 (solaparía sin coincidir) ya no se puede pedir.
    r = registrar(gh1, {"resultado": "visita_acordada", "fecha": str(D_H1), "hora": "10:00"})
    # 422 fuera_de_rejilla, no 409.
    comprobar("ruta nueva 10:00 (solaparía sin coincidir) -> 422 fuera_de_rejilla (C3)",
              r.status_code == 422 and motivo(r) == "fuera_de_rejilla", f"({r.status_code} {motivo(r)})")
    # Ruta nueva, la misma hora que la pedida.
    r = registrar(gh1, {"resultado": "visita_acordada", "fecha": str(D_H1), "hora": "09:45"})
    # El detalle.
    d = detalle_409(r)
    # Se enseña.
    print(f"      ruta nueva 09:45 <- {r.status_code} {d}")
    # 409 hueco_ocupado, claves exactas y la lista esperada.
    comprobar("ruta nueva: visita_acordada sobre una pedida del chat -> 409 hueco_ocupado con huecos_libres",
              d.get("motivo") == "hueco_ocupado" and set(d) == {"motivo", "mensaje", "huecos_libres"}
              and d.get("huecos_libres") == libres_esperados([585]), f"({r.status_code} {d})")
    # El alias (formulario publicado, P17), a la misma hora que la pedida.
    r = pedir("POST", "/gate-decisions", {"oportunidad_id": gh1, "decision": "visita_acordada",
                                          "fecha": str(D_H1), "hora": "09:45", "informe": "x"})
    # El detalle.
    d = detalle_409(r)
    # Se enseña.
    print(f"      alias 09:45 <- {r.status_code} {d}")
    # El alias, igual: 409 hueco_ocupado con la lista.
    comprobar("alias /gate-decisions: el mismo 409 hueco_ocupado con huecos_libres",
              d.get("motivo") == "hueco_ocupado" and d.get("huecos_libres") == libres_esperados([585]),
              f"({r.status_code} {d})")
    # Ni los 409 ni el 422 han escrito nada.
    comprobar("los 409 y el 422 no escriben nada en el Gate", foto_op(gh1) == antes)
    # A las 11:00 (09:45 + 60 + 15: justo el desplazamiento): 201.
    verificar_201("Gate visita_acordada a las 11:00 (borde libre con el desplazamiento)",
                  registrar(gh1, {"resultado": "visita_acordada", "fecha": str(D_H1), "hora": "11:00"}),
                  gh1, antes, "gate", "visita_acordada", "visita_agendada", ["confirmada"], TEXTO_GATE)

    # 2) confirmar_hora cuando OTRA visita activa choca con la pedida (una
    #    ANTIGUA hecha a mano a las 10:30, fuera de la rejilla: R6 de D31 y R3
    #    de D32). La pedida es la propia y no cuenta (C8), pero la otra sí.
    _, kh2 = confirmacion(D_H2, "09:45")
    # Otra oportunidad propia, sin visita.
    _, gh2 = lead(estructural=True)
    # SQL: una visita 'confirmada' a las 10:30 de ese día, en esa oportunidad PROPIA.
    cur.execute(f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                f"SELECT %(op)s, %(f)s, 'confirmada', 'hecha a mano' WHERE %(op)s IN ({SQL_OPS});",
                {"op": gh2, "f": madrid(D_H2, 10, 30), "p": PATRON})
    # Se confirma (es una fila propia).
    cn.commit()
    # La foto antes.
    antes = foto_op(kh2)
    # confirmar_hora de la pedida (09:45, hasta las 10:45): choca con la de las 10:30.
    r = registrar(kh2, {"resultado": "confirmar_hora", "fecha": str(D_H2), "hora": "09:45"})
    # 409 hueco_ocupado.
    comprobar("confirmar_hora con OTRA visita que choca -> 409 hueco_ocupado, sin escribir",
              motivo(r) == "hueco_ocupado" and foto_op(kh2) == antes, f"({r.status_code} {cuerpo_de(r)})")
    # otra_hora a las 11:00: choca por el desplazamiento (la de las 10:30 deja
    # libre desde las 11:45).
    r = registrar(kh2, {"resultado": "otra_hora", "fecha": str(D_H2), "hora": "11:00"})
    # 409 hueco_ocupado.
    comprobar("otra_hora a las 11:00 (sin los 15 de desplazamiento tras la de las 10:30) -> 409, sin escribir",
              motivo(r) == "hueco_ocupado" and foto_op(kh2) == antes, f"({r.status_code} {cuerpo_de(r)})")
    # otra_hora a las 12:15 (la primera de la rejilla libre tras la antigua): 201.
    verificar_201("otra_hora a las 12:15 (la primera libre tras la antigua)",
                  registrar(kh2, {"resultado": "otra_hora", "fecha": str(D_H2), "hora": "12:15"}),
                  kh2, antes, "confirmacion_visita", "otra_hora", "visita_agendada", ["cancelada", "confirmada"],
                  TEXTO_OTRA_HORA)

    # 3) otra_hora que solo choca con la PROPIA pedida (que se cancela en la
    #    misma transacción): 201 (C8). Con la rejilla, dos horas válidas no
    #    chocan: la pedida se mueve por SQL a las 10:00 (una propia antigua).
    _, kh3 = confirmacion(D_H3, "09:45")
    # SQL: la visita pedida de una oportunidad PROPIA, movida a las 10:00.
    cur.execute(f"UPDATE visitas SET fecha_propuesta = %(f)s WHERE oportunidad_id = %(op)s "
                f"AND estado = 'solicitada' AND oportunidad_id IN ({SQL_OPS});",
                {"f": madrid(D_H3, 10, 0), "op": kh3, "p": PATRON})
    # Se confirma (es una fila propia).
    cn.commit()
    # La foto antes.
    antes = foto_op(kh3)
    # 09:45 solo choca con su propia pedida de las 10:00.
    verificar_201("otra_hora que solo choca con su PROPIA pedida -> 201 (C8)",
                  registrar(kh3, {"resultado": "otra_hora", "fecha": str(D_H3), "hora": "09:45"}),
                  kh3, antes, "confirmacion_visita", "otra_hora", "visita_agendada", ["cancelada", "confirmada"],
                  TEXTO_OTRA_HORA)

    # ------------------------------------------------------------------
    print("\nCASO 14 - Ningún 500, 201 <-> creado, ningún importe")
    comprobar(f"ninguna respuesta 500 ({len(codigos)} peticiones)", 500 not in codigos)
    comprobar(f"201 <-> creado=true y 200 <-> creado=false ({len(pares)} éxitos)",
              all((c == 201) == (cr is True) for c, cr in pares))
    comprobar("ninguna respuesta de /resultados-llamada lleva un importe",
              all("importe" not in json.dumps(c) for c in cuerpos))
finally:
    # Se apaga el servidor (el hilo termina solo).
    servidor.should_exit = True
    hilo.join(timeout=15)
    # Los logs del manejador global de los 500 de ESTA ejecución, de las
    # rutas que pide el script; se borran por id exacto si cuadran.
    rutas = ["/resultados-llamada", "/gate-decisions", "/leads", "/calculate-estimate", "/visits",
             "/create-followup-task", "/llamadas-del-dia"]
    # SQL: los ids de esos logs.
    ids_500 = [f[0] for f in consultar(
        "SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' AND (detalle->>'ruta' = ANY(%s) "
        "OR detalle->>'ruta' LIKE '/oportunidades/%%') ORDER BY id;", (FOTO["logs"][0], rutas))]
    print(f"\n  respuestas 500: {codigos.count(500)}; logs error_no_controlado de estas rutas: {ids_500}")
    comprobar("los logs de 500 son exactamente los esperados", len(ids_500) == codigos.count(500))
    # Borrado por id exacto, solo si cuadran.
    if ids_500 and len(ids_500) == codigos.count(500):
        # SQL: DELETE de esos logs por su id.
        cur.execute("DELETE FROM logs WHERE id = ANY(%s);", (ids_500,))
        cn.commit()
    # Lo propio, y lo ajeno con los MISMOS id_base.
    limpiar()
    cambiadas = {t: (FOTO[t][1], consultar(f"SELECT count(*) FROM {t} WHERE id <= %(b)s AND NOT ({PROPIO[t]});",
                                           {"p": PATRON, "b": FOTO[t][0]})[0][0]) for t in PROPIO}
    comprobar("no toca nada ajeno", all(a == b for a, b in cambiadas.values()), f"({cambiadas})")
    # No queda nada propio.
    restos = consultar("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON,))[0][0]
    comprobar("no quedan datos propios", restos == 0, f"({restos})")
    cn.close()

# Resumen y código de salida (1 si hubo fallos; lo mira la suite).
total = ok + len(fallos)
# El resumen, entre dos líneas de "=".
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con fallos, se listan y el código de salida es 1 (lo mira la suite).
if fallos:
    print("FALLOS:")
    # Uno por línea; después, código de salida 1.
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
