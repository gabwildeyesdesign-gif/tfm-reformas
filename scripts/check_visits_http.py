"""
Verificación por HTTP REAL de POST /visits (plan: docs/Plan_Endpoint_Visits.txt,
secciones 7 y 8.3).

Levanta el servidor de verdad (uvicorn.Server en un hilo de este proceso,
SIN --reload, en el puerto 8018: nunca el 8000, que es el uvicorn de n8n).
Los leads de prueba se crean con POST /leads y sus presupuestos con POST
/calculate-estimate, por HTTP, como lo hará n8n. Tokens uuid4 en cada
ejecución; emails http-visits-<8 caracteres>@example.com.

Las fechas son RELATIVAS a hoy (el próximo lunes, etc.) para que el
script no caduque. El cambio de hora con fechas fijas se prueba aparte, en
scripts/check_visits_service.py.

"NO TOCA NADA AJENO": al empezar se cuentan, en cada tabla, las filas que
NO son de los clientes de prueba y que ya existían (id <= id_base). Al
terminar, tras la limpieza, se vuelven a contar con el mismo id_base: si
el script hubiera borrado algo ajeno, el recuento bajaría -> FALLO. Lo que
escriba otro proceso durante la prueba (el uvicorn de n8n) tiene id >
id_base, no entra en la comparación y se enseña aparte, como información.

Limpieza (solo lo propio, en el orden de las claves foráneas): logs ->
visitas -> presupuestos -> oportunidades -> leads -> clientes, todo a
partir de los ids de los clientes de prueba.
"""

import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as hora_del_dia, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn

from app.config import DATABASE_URL, WEBHOOK_SECRET

PUERTO = 8018
BASE = f"http://127.0.0.1:{PUERTO}"
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
PATRON_EMAIL = "http-visits-%@example.com"
MADRID = ZoneInfo("Europe/Madrid")

ok = 0
fallos = []
# Todos los cuerpos JSON recibidos de POST /visits, para el caso de los
# importes.
cuerpos_visits = []
# (código HTTP, creado, descripción) de cada respuesta de ÉXITO (200 o 201)
# de POST /visits, para comprobar al final que 201 <-> creado=true y
# 200 <-> creado=false en todas.
pares_exito = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una sola fila, cerrando la transacción de lectura."""
    cur.execute(sql, params)
    fila = cur.fetchone()
    cn.commit()
    return fila


def ejecutar(sql, params=()):
    """Escritura directa en la base de datos (solo sobre filas propias)."""
    cur.execute(sql, params)
    cn.commit()


# ----------------------------------------------------------------------
# Qué es "propio": todo lo que cuelga de los clientes de prueba
# ----------------------------------------------------------------------
# Subconsultas reutilizables. %(patron)s se rellena con PATRON_EMAIL.
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
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPORTUNIDADES})",
}
# Orden de borrado: primero lo que apunta a otras tablas.
ORDEN_BORRADO = ["logs", "visitas", "presupuestos", "oportunidades", "leads", "clientes"]


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas."""
    for tabla in ORDEN_BORRADO:
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    cn.commit()


def foto_ajena():
    """
    Para cada tabla: (id_base, filas ajenas con id <= id_base).
    id_base es el id más alto en este momento.
    """
    foto = {}
    for tabla in ORDEN_BORRADO:
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        id_base = cur.fetchone()[0]
        cur.execute(
            f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
            {"patron": PATRON_EMAIL, "base": id_base},
        )
        foto[tabla] = (id_base, cur.fetchone()[0])
    cn.commit()
    return foto


def recontar_ajeno(foto):
    """Vuelve a contar lo ajeno con los MISMOS id_base, y lo ajeno nuevo."""
    resultado = {}
    for tabla, (id_base, _) in foto.items():
        params = {"patron": PATRON_EMAIL, "base": id_base}
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});", params)
        antiguas = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id > %(base)s AND NOT ({PROPIO[tabla]});", params)
        nuevas = cur.fetchone()[0]
        resultado[tabla] = (antiguas, nuevas)
    cn.commit()
    return resultado


# ----------------------------------------------------------------------
# Ayudas HTTP
# ----------------------------------------------------------------------
def lead_http(estructural=False):
    """Crea un lead por POST /leads. Devuelve (lead_token, oportunidad_id)."""
    token = str(uuid.uuid4())
    r = requests.post(
        f"{BASE}/leads",
        headers=AUTH,
        json={
            "nombre": "Prueba Visitas",
            "email": f"http-visits-{token[:8]}@example.com",
            "telefono": "600000000",
            "tipo_reforma": "bano",
            "m2": 6,
            "nivel_acabados": "medio",
            "incluye_cambios_estructurales": estructural,
            "lead_token": token,
        },
        timeout=20,
    )
    assert r.status_code == 201, r.text
    return token, r.json()["oportunidad_id"]


def calcular(oportunidad_id):
    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH, json={"oportunidad_id": oportunidad_id}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()


def visita(token, fecha, hora, texto="Me viene bien a esa hora", cabeceras=AUTH, extra=None):
    """POST /visits. fecha es un date; hora, un texto "HH:MM"."""
    peticion = {"lead_token": token, "fecha": fecha.isoformat(), "hora": hora, "texto_cliente": texto}
    if extra:
        peticion.update(extra)
    r = requests.post(f"{BASE}/visits", headers=cabeceras, json=peticion, timeout=40)
    try:
        cuerpos_visits.append(r.json())
    except ValueError:
        pass
    if r.status_code in (200, 201):
        pares_exito.append((r.status_code, cuerpo(r).get("creado"), f"{fecha} {hora}"))
    return r


def cuerpo(r):
    """JSON de la respuesta, o {} si no es JSON (así un fallo no revienta el script)."""
    try:
        return r.json()
    except ValueError:
        return {}


def motivo(r):
    """El motivo de un rechazo de negocio: {"detail": {"motivo": ...}}."""
    detalle = cuerpo(r).get("detail")
    return detalle.get("motivo") if isinstance(detalle, dict) else None


def esperado_madrid(fecha, hora):
    """El instante que debe devolver el endpoint, calculado aquí por separado."""
    h, m = map(int, hora.split(":"))
    return datetime.combine(fecha, hora_del_dia(h, m), tzinfo=MADRID)


def claves_con_importe(objeto, ruta=""):
    """Rutas de todas las claves que contienen "importe", a cualquier nivel."""
    encontradas = []
    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            ruta_clave = f"{ruta}.{clave}" if ruta else clave
            if "importe" in clave.lower():
                encontradas.append(ruta_clave)
            encontradas.extend(claves_con_importe(valor, ruta_clave))
    elif isinstance(objeto, list):
        for i, valor in enumerate(objeto):
            encontradas.extend(claves_con_importe(valor, f"{ruta}[{i}]"))
    return encontradas


def visitas_de(oportunidad_id):
    cur.execute("SELECT id, estado FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (oportunidad_id,))
    filas = cur.fetchall()
    cn.commit()
    return filas


def logs_de(oportunidad_id, accion):
    cur.execute(
        "SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s AND accion = %s ORDER BY id;",
        (oportunidad_id, accion),
    )
    filas = [f[0] for f in cur.fetchall()]
    cn.commit()
    return filas


# ----------------------------------------------------------------------
# Fechas relativas a hoy (en Madrid)
# ----------------------------------------------------------------------
HOY = datetime.now(MADRID).date()
# Días hasta el próximo lunes (weekday: lunes = 0). Si hoy es lunes, el
# de la semana que viene, para que siempre sea futuro.
LUNES = HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)
MARTES = LUNES + timedelta(days=1)
SABADO = LUNES + timedelta(days=5)
AYER = HOY - timedelta(days=1)

# ======================================================================
limpiar()
FOTO = foto_ajena()
print("=" * 78)
print("FOTO DE LO AJENO AL EMPEZAR (tabla: id_base, filas ajenas con id <= id_base)")
print("=" * 78)
for tabla, (id_base, n) in FOTO.items():
    print(f"  {tabla:14} id_base={id_base:<6} ajenas={n}")
print(f"  Fechas de prueba: hoy {HOY} ({HOY:%A}), lunes {LUNES}, martes {MARTES}, sábado {SABADO}, ayer {AYER}")

servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
hilo = threading.Thread(target=servidor.run, daemon=True)
hilo.start()
inicio = time.time()
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

try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación")
    token_x = str(uuid.uuid4())
    r = visita(token_x, LUNES, "08:30", cabeceras={})
    comprobar("401 sin cabecera", r.status_code == 401, f"({r.status_code})")
    r = requests.post(f"{BASE}/visits", json={"basura": 1}, timeout=10)
    comprobar("401 sin cabecera y con cuerpo inválido (la cabecera va antes)", r.status_code == 401, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 2 - Formato (422 de Pydantic)")
    for nombre, args, campo in [
        ("hora '10:00:00'", dict(hora="10:00:00"), "hora"),
        ("texto de 1001 caracteres", dict(hora="10:00", texto="x" * 1001), "texto_cliente"),
        ("oportunidad_id de más", dict(hora="10:00", extra={"oportunidad_id": 5}), "oportunidad_id"),
    ]:
        r = visita(token_x, LUNES, **args)
        loc = cuerpo(r).get("detail", [{}])[0].get("loc") if r.status_code == 422 else None
        comprobar(f"422 con {nombre}, señalando {campo}", r.status_code == 422 and loc[-1] == campo, f"({r.status_code}, loc={loc})")

    # ------------------------------------------------------------------
    print("\nCASO 3 - 404")
    r = visita(token_x, LUNES, "08:30")
    comprobar("404 lead_no_encontrado", r.status_code == 404 and motivo(r) == "lead_no_encontrado", f"({r.status_code}, {motivo(r)})")

    # ------------------------------------------------------------------
    print("\nCASO 4-6 - Estado (409)")
    token_sin, op_sin = lead_http()
    r = visita(token_sin, LUNES, "08:30")
    comprobar("409 sin_presupuesto", r.status_code == 409 and motivo(r) == "sin_presupuesto", f"({r.status_code}, {motivo(r)})")

    token_gate, op_gate = lead_http(estructural=True)
    calc = calcular(op_gate)
    print(f"      lead con Gate: estado {calc['status']}, motivo_gate {calc['motivo_gate']}")
    r = visita(token_gate, LUNES, "08:30")
    comprobar("409 presupuesto_con_gate (Gate pendiente)", r.status_code == 409 and motivo(r) == "presupuesto_con_gate", f"({r.status_code}, {motivo(r)})")
    # 5b: el Gate con un estado que SÍ permite visita (como quedará tras
    # aprobarse, P5). Aquí la ÚNICA barrera es la comprobación del Gate.
    ejecutar("UPDATE oportunidades SET estado = 'presupuesto_enviado' WHERE id = %s;", (op_gate,))
    r = visita(token_gate, LUNES, "08:30")
    comprobar("409 presupuesto_con_gate aunque el estado sea presupuesto_enviado", r.status_code == 409 and motivo(r) == "presupuesto_con_gate", f"({r.status_code}, {motivo(r)})")
    comprobar("el lead con Gate no tiene ninguna visita", visitas_de(op_gate) == [])

    token_ganada, op_ganada = lead_http()
    calcular(op_ganada)
    ejecutar("UPDATE oportunidades SET estado = 'ganada' WHERE id = %s;", (op_ganada,))
    r = visita(token_ganada, LUNES, "08:30")
    comprobar("409 estado_no_permitido (estado 'ganada')", r.status_code == 409 and motivo(r) == "estado_no_permitido", f"({r.status_code}, {motivo(r)})")

    # ------------------------------------------------------------------
    print("\nCASO 7 - Reglas de la fecha (422 de negocio)")
    token, op = lead_http()
    calcular(op)
    for nombre, fecha, hora, esperado in [
        ("ayer a las 10:00", AYER, "10:00", "fecha_pasada"),
        ("el próximo sábado a las 10:00", SABADO, "10:00", "fin_de_semana"),
        ("08:29 (antes de la franja)", LUNES, "08:29", "fuera_de_franja"),
        ("12:45 (terminaría a las 13:45)", LUNES, "12:45", "fuera_de_franja"),
        ("16:59 (antes de la franja de tarde)", LUNES, "16:59", "fuera_de_franja"),
        ("19:30 (terminaría a las 20:30)", LUNES, "19:30", "fuera_de_franja"),
    ]:
        r = visita(token, fecha, hora)
        mensaje = (cuerpo(r).get("detail") or {}).get("mensaje", "") if r.status_code == 422 else ""
        comprobar(f"422 {esperado}: {nombre}", r.status_code == 422 and motivo(r) == esperado, f"({r.status_code}, {motivo(r)})")
        if esperado == "fuera_de_franja":
            comprobar("   el mensaje nombra las franjas reales", "08:30" in mensaje and "13:30" in mensaje and "17:00" in mensaje and "20:00" in mensaje, f"({mensaje[:60]}...)")
    comprobar("ningún rechazo de fecha ha creado visitas", visitas_de(op) == [])
    comprobar("la oportunidad sigue en presupuesto_enviado", consultar("SELECT estado FROM oportunidades WHERE id = %s;", (op,))[0] == "presupuesto_enviado")

    # ------------------------------------------------------------------
    print("\nCASO 8 - 201 solicitud nueva (lunes 08:30, límite inferior)")
    texto_1 = "El lunes a primera hora, sobre las 8:30"
    r = visita(token, LUNES, "08:30", texto=texto_1)
    j = cuerpo(r)
    print(f"      <- {r.status_code} {r.text}")
    esperado_1 = esperado_madrid(LUNES, "08:30")
    comprobar("201, estado solicitada, sustituye_a null, creado true",
              r.status_code == 201 and j.get("estado") == "solicitada" and j.get("sustituye_a") is None and j.get("creado") is True)
    recibido = datetime.fromisoformat(j["fecha_propuesta"]) if "fecha_propuesta" in j else None
    comprobar("fecha_propuesta = lunes 08:30 en Madrid, con su desfase",
              recibido == esperado_1 and recibido.utcoffset() == esperado_1.utcoffset(), f"({j.get('fecha_propuesta')})")
    visita_1 = j.get("visita_id")
    fila = consultar("SELECT estado, texto_cliente, fecha_propuesta FROM visitas WHERE id = %s;", (visita_1,))
    comprobar("en la BD: 'solicitada', con texto_cliente y la misma fecha", fila is not None and fila[0] == "solicitada" and fila[1] == texto_1 and fila[2] == esperado_1)
    comprobar("oportunidad en visita_agendada", consultar("SELECT estado FROM oportunidades WHERE id = %s;", (op,))[0] == "visita_agendada")
    comprobar("1 log visita_solicitada con visita_id", [d.get("visita_id") for d in logs_de(op, "visita_solicitada")] == [visita_1])

    # ------------------------------------------------------------------
    print("\nCASO 9 - 200 repetición (misma fecha)")
    n_visitas = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s;", (op,))[0]
    n_logs = consultar("SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s;", (op,))[0]
    r = visita(token, LUNES, "08:30", texto="Otro texto distinto")
    j = cuerpo(r)
    comprobar("200, mismo visita_id, creado false", r.status_code == 200 and j.get("visita_id") == visita_1 and j.get("creado") is False, f"({r.status_code}, {j})")
    comprobar("0 visitas nuevas", consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s;", (op,))[0] == n_visitas)
    comprobar("0 logs nuevos", consultar("SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s;", (op,))[0] == n_logs)
    comprobar("el texto_cliente original no ha cambiado", consultar("SELECT texto_cliente FROM visitas WHERE id = %s;", (visita_1,))[0] == texto_1)

    # ------------------------------------------------------------------
    print("\nCASO 10 - 201 sustitución (lunes 12:30, límite superior de la mañana)")
    r = visita(token, LUNES, "12:30")
    j = cuerpo(r)
    print(f"      <- {r.status_code} {r.text}")
    visita_2 = j.get("visita_id")
    comprobar("201, sustituye_a = la anterior, creado true", r.status_code == 201 and j.get("sustituye_a") == visita_1 and j.get("creado") is True)
    comprobar("la anterior queda 'cancelada' y solo hay una activa",
              visitas_de(op) == [(visita_1, "cancelada"), (visita_2, "solicitada")], f"({visitas_de(op)})")
    sustituida = logs_de(op, "visita_sustituida")
    comprobar("1 log visita_sustituida con la visita anterior en el detalle",
              len(sustituida) == 1 and sustituida[0].get("sustituye_a") == visita_1
              and datetime.fromisoformat(sustituida[0].get("fecha_anterior", "1970-01-01T00:00:00+00:00")) == esperado_1,
              f"({sustituida})")

    print("\nCASO 11 - Límites de la tarde (17:00 y 19:00, por sustitución)")
    for hora in ("17:00", "19:00"):
        r = visita(token, MARTES, hora)
        comprobar(f"201 a las {hora} del martes", r.status_code == 201 and cuerpo(r).get("creado") is True, f"({r.status_code}, {motivo(r)})")
    activas = [v for v in visitas_de(op) if v[1] == "solicitada"]
    comprobar("sigue habiendo exactamente una visita activa", len(activas) == 1, f"({visitas_de(op)})")

    # ------------------------------------------------------------------
    print("\nCASO 12 - Concurrencia")
    token_c1, op_c1 = lead_http()
    calcular(op_c1)
    # ThreadPoolExecutor lanza las 5 peticiones a la vez, cada una en su
    # hilo; map() devuelve las respuestas en el mismo orden.
    with ThreadPoolExecutor(max_workers=5) as ejecutor:
        respuestas = list(ejecutor.map(lambda _: visita(token_c1, LUNES, "10:00"), range(5)))
    codigos = sorted(r.status_code for r in respuestas)
    creados = [cuerpo(r).get("creado") for r in respuestas]
    ids = {cuerpo(r).get("visita_id") for r in respuestas}
    print(f"      misma fecha x5 -> códigos {codigos}, creado {creados}, visita_id {ids}")
    # Cada par sale de UNA misma respuesta. map() devuelve las respuestas en
    # el orden en que se lanzaron las peticiones (no en el que terminaron).
    print(f"      misma fecha x5, pares (código, creado) por petición: {[(r.status_code, cuerpo(r).get('creado')) for r in respuestas]}")
    comprobar("misma fecha x5: un 201 y cuatro 200", codigos == [200, 200, 200, 200, 201])
    comprobar("misma fecha x5: exactamente un creado=true y todas con el mismo visita_id", creados.count(True) == 1 and len(ids) == 1)
    comprobar("misma fecha x5: una sola visita en la BD", len(visitas_de(op_c1)) == 1, f"({visitas_de(op_c1)})")

    token_c2, op_c2 = lead_http()
    calcular(op_c2)
    horas = ["08:30", "09:00", "10:00", "11:00", "12:00"]
    with ThreadPoolExecutor(max_workers=5) as ejecutor:
        respuestas = list(ejecutor.map(lambda h: visita(token_c2, LUNES, h), horas))
    codigos = sorted(r.status_code for r in respuestas)
    print(f"      fechas distintas x5 -> códigos {codigos}, visitas {visitas_de(op_c2)}")
    comprobar("fechas distintas x5: todas 201 (se serializan como sustituciones)", codigos == [201] * 5)
    estados = [v[1] for v in visitas_de(op_c2)]
    comprobar("fechas distintas x5: exactamente una activa y cuatro canceladas",
              estados.count("solicitada") == 1 and estados.count("cancelada") == 4)

    # ------------------------------------------------------------------
    print("\nCASO 13 - Visita confirmada (P4)")
    visita_conf = [v for v in visitas_de(op_c1)][0][0]
    ejecutar("UPDATE visitas SET estado = 'confirmada' WHERE id = %s;", (visita_conf,))
    r = visita(token_c1, MARTES, "10:00")
    comprobar("409 visita_confirmada al pedir otra fecha", r.status_code == 409 and motivo(r) == "visita_confirmada", f"({r.status_code}, {motivo(r)})")
    r = visita(token_c1, LUNES, "10:00")
    comprobar("con la MISMA fecha: 200, creado false, estado confirmada",
              r.status_code == 200 and cuerpo(r).get("estado") == "confirmada" and cuerpo(r).get("creado") is False, f"({r.status_code}, {cuerpo(r)})")
    comprobar("la confirmada sigue intacta", visitas_de(op_c1) == [(visita_conf, "confirmada")])

    # ------------------------------------------------------------------
    print("\nCASO 14 - Ningún importe en ninguna respuesta de POST /visits")
    con_importe = [(i, claves_con_importe(c)) for i, c in enumerate(cuerpos_visits) if claves_con_importe(c)]
    comprobar(f"0 claves 'importe' en {len(cuerpos_visits)} respuestas (éxitos y errores)", not con_importe, f"({con_importe[:3]})")

    # ------------------------------------------------------------------
    print("\nCASO 15 - Coherencia entre el código HTTP y creado")
    incoherentes = [p for p in pares_exito if not ((p[0] == 201 and p[1] is True) or (p[0] == 200 and p[1] is False))]
    print(f"      respuestas de éxito revisadas: {len(pares_exito)} "
          f"({sum(1 for p in pares_exito if p[0] == 201)} con 201, {sum(1 for p in pares_exito if p[0] == 200)} con 200)")
    comprobar("201 <-> creado=true y 200 <-> creado=false en TODAS las respuestas de éxito",
              not incoherentes, f"(incoherentes: {incoherentes})")

finally:
    servidor.should_exit = True
    hilo.join(timeout=15)
    limpiar()
    restos = consultar(f"SELECT count(*) FROM clientes WHERE {PROPIO['clientes']};", {"patron": PATRON_EMAIL})[0]
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")
    comprobar("no queda ningún dato de prueba", restos == 0)

    print("\nNO TOCA NADA AJENO (mismo id_base que al empezar)")
    despues = recontar_ajeno(FOTO)
    for tabla, (id_base, antes) in FOTO.items():
        antiguas, nuevas = despues[tabla]
        comprobar(f"{tabla}: ajenas con id <= {id_base}: {antes} -> {antiguas}", antiguas == antes,
                  f"(ajenas NUEVAS durante la prueba, solo información: {nuevas})")
    cn.close()

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
