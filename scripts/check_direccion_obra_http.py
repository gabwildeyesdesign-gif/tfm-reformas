"""
Verificación por HTTP REAL de la dirección de la obra (D42; rama
feat/n0-direccion-obra; veredicto del tutor al PASO 0, 2026-10-22).

Levanta el servidor en un hilo de este proceso (sin --reload, puerto 8035,
nunca el 8000), crea sus propios clientes por POST /leads y
/calculate-estimate (y POST /visits para las confirmaciones) y prueba:
  CASO 1  forma (422 de Pydantic, sin escribir nada): cada regla de D42.3,
          el código postal como NÚMERO (N15), una clave de más, y un objeto
          con un resultado SIN visita (descartar y cliente_cancela); null =
          no enviarla (201)
  CASO 2  201 con dirección (Gate visita_acordada, confirmar_hora y
          otra_hora): la respuesta con sus claves EXACTAS y sin la
          dirección; la ficha la devuelve exacta; el log dice
          direccion_obra_registrada = true
  CASO 3  201 sin dirección: la ficha da null y el log dice false
  CASO 4  repetición (H1): 200 con OTRA dirección, y la ficha sigue igual
  CASO 5  D42.5, el barrido: la marca de las direcciones 0 veces en TODAS
          las demás lecturas (GET /leads/session, GET /gate-avisos, GET
          /llamadas-del-dia, GET /huecos-libres en sus dos modos, POST
          /calculate-estimate, POST /visits, POST /create-followup-task, las
          respuestas 200 y 409 de POST /resultados-llamada y las tres tools
          MCP). Para que la lista diaria pruebe algo, dos oportunidades
          propias que SALEN en ella (Gate sin decisión y seguimiento por
          abrir) llevan una dirección escrita a mano.
  CASO 6  logs: la marca 0 veces en logs.detalle (desde el id base, también
          en error_no_controlado); ningún 500. H3 (limitación aceptada): un
          422 repite lo enviado ("input"); su recuento sale como [INFO].

H4 del veredicto: NUNCA imprime una dirección ni un cuerpo de respuesta que
pueda llevarla: solo códigos, recuentos y verdadero/falso.

Recuentos: se calculan en la fase 2 y se escriben en el commit (D37.5).

Las 4 preguntas: (1) escribe en tablas reales SOLO filas propias (emails
http-direccion-<8>@example.com, tokens uuid4), con commit: envejece a mano el
presupuesto de oportunidades PROPIAS y escribe a mano la dirección de dos
oportunidades PROPIAS; (2) no pasa ninguna conexión al código (todo por
HTTP); (3) si se corta, la siguiente ejecución borra lo propio al empezar, y
"no toca nada ajeno" (id_base, también en direcciones_obra) lo comprueba;
los logs de un 500 se borran por id exacto tras comprobarlos; (4) puerto
8035, ningún dato real; sus visitas van en su bloque de fechas_prueba.py,
con exigir_libres antes de crear nada.
"""

# asyncio: el cliente MCP es asíncrono. json: el texto de las respuestas.
import asyncio
import json
import socket
import sys
# threading: el servidor corre en un hilo; time: esperas; uuid: tokens y marca.
import threading
import time
import uuid
# Fechas en Madrid; Path para las rutas.
from datetime import datetime
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
# El cliente MCP de fastmcp, con su transporte HTTP y el token Bearer.
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

# Los secretos del .env: solo en cabeceras, nunca se imprimen.
from app.config import DATABASE_URL, GATE_SECRET, MCP_SECRET, WEBHOOK_SECRET

# Las fechas propias de este script (bloque de semanas lejanas).
import fechas_prueba

# Puerto propio (8035, comprobado libre en el PASO 0) y direcciones base.
PUERTO = 8035
BASE = f"http://127.0.0.1:{PUERTO}"
URL_MCP = f"{BASE}/mcp/"
# Llave de n8n (leads, cálculo, visitas, sesión) y de administración.
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# La marca propia de los clientes.
PATRON = "http-direccion-%@example.com"
# La marca de ESTA ejecución, dentro de los textos de las direcciones.
MARCA = f"MARCA-D42H-{uuid.uuid4().hex[:10]}"
# Direcciones válidas de prueba (inventadas; nunca se imprimen).
DIR_A = {"calle_numero": f"Calle {MARCA} 1", "codigo_postal": "28013", "municipio": "Municipio de Prueba"}
DIR_B = {"calle_numero": f"Avenida {MARCA} 2", "codigo_postal": "08001", "municipio": "Otro Municipio"}
# Las claves EXACTAS de la respuesta de POST /resultados-llamada (D42.5: no
# cambian; copiadas del Contrato, sección 4).
CLAVES_RESPUESTA = {"llamada_id", "oportunidad_id", "tipo_llamada", "resultado", "motivo", "estado_oportunidad",
                    "visita_id", "fecha_visita", "fecha_fin_visita", "visita_cancelada_id", "creado"}

# Contadores; los códigos de todas las respuestas; los textos de las
# respuestas que NO son 422 ni de la ficha (el barrido de D42.5), y el
# número de 422 que repiten la marca (H3, solo [INFO]).
ok, fallos, codigos, textos_barrido = 0, [], [], []
eco_422 = 0


def comprobar(titulo, condicion, detalle=""):
    """[OK] si la condición es verdadera; [FALLO] (y se apunta) si no."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta: se suma y se imprime.
    if condicion:
        ok += 1
        print(f"  [OK] {titulo}")
    # Incorrecta: se apunta y se imprime con el detalle (nunca una dirección).
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
    filas = cur.fetchall()
    cn.commit()
    return filas


# Qué es propio: lo que cuelga de los clientes con la marca.
SQL_OPS = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
# Por tabla, la condición de sus filas propias, en el orden de borrado
# (direcciones_obra antes que oportunidades: su clave foránea).
PROPIO = {
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})",
    "direcciones_obra": f"oportunidad_id IN ({SQL_OPS})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPS})",
    "visitas": f"oportunidad_id IN ({SQL_OPS})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPS})",
    "oportunidades": f"id IN ({SQL_OPS})",
    "leads": "cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s)",
    "clientes": "email LIKE %(p)s",
}


# Borrado de lo propio (al empezar y al terminar).
def limpiar():
    """Borra SOLO lo propio, en el orden de las claves foráneas, y commit."""
    # Una tabla tras otra.
    for tabla, condicion in PROPIO.items():
        # SQL: DELETE de las filas propias de esa tabla.
        cur.execute(f"DELETE FROM {tabla} WHERE {condicion};", {"p": PATRON})
    cn.commit()


# La foto de lo ajeno, para "no toca nada ajeno".
def foto_ajena():
    """Por tabla: (id_base, filas NO propias con id <= id_base). Solo recuentos (H4)."""
    # Diccionario tabla -> (id_base, ajenas).
    foto = {}
    for tabla, condicion in PROPIO.items():
        # SQL: el id más alto ahora, y cuántas filas NO propias hay hasta él.
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        base = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(b)s AND NOT ({condicion});", {"p": PATRON, "b": base})
        foto[tabla] = (base, cur.fetchone()[0])
    cn.commit()
    return foto


# Todas las peticiones pasan por aquí (códigos y barrido).
def pedir(metodo, ruta, cuerpo=None, cabeceras=AUTH_GATE, barrido=True):
    """
    Una petición HTTP real; apunta el código y, salvo en un 422 o si
    barrido=False (la ficha), guarda su texto para el barrido de D42.5.
    """
    # global: se cambia el contador de fuera.
    global eco_422
    # requests.request: GET o POST, con el cuerpo JSON si lo hay.
    r = requests.request(metodo, f"{BASE}{ruta}", headers=cabeceras, json=cuerpo, timeout=60)
    codigos.append(r.status_code)
    # H3: un 422 repite lo enviado; se cuenta aparte y no entra en el barrido.
    if r.status_code == 422:
        eco_422 += MARCA in r.text
    # Las demás, al barrido (con su ruta, nunca con su texto en la salida).
    elif barrido:
        textos_barrido.append((f"{metodo} {ruta.split('?')[0]} -> {r.status_code}", r.text))
    return r


# La ruta probada.
def registrar(op, cuerpo):
    """POST /resultados-llamada con un informe de prueba."""
    # El cuerpo con el oportunidad_id y el informe.
    return pedir("POST", "/resultados-llamada", {"oportunidad_id": op, "informe": "Llamada de prueba D42.", **cuerpo})


# Los datos de partida, por las rutas de verdad.
def lead(estructural=False):
    """Lead propio por POST /leads y su cálculo. Devuelve (token, oportunidad_id)."""
    # Token único; sus 8 primeros caracteres van en el email (la marca).
    token = str(uuid.uuid4())
    r = pedir("POST", "/leads", {
        "nombre": "Prueba direccion", "email": f"http-direccion-{token[:8]}@example.com", "telefono": "600000000",
        "tipo_reforma": "bano", "m2": 6, "nivel_acabados": "medio",
        "incluye_cambios_estructurales": estructural, "lead_token": token}, AUTH_WEBHOOK)
    # Sin datos de prueba no tiene sentido seguir: se para con el código.
    assert r.status_code == 201, r.status_code
    op = r.json()["oportunidad_id"]
    # El cálculo (con cambios estructurales queda con Gate).
    c = pedir("POST", "/calculate-estimate", {"oportunidad_id": op}, AUTH_WEBHOOK)
    assert c.status_code == 200, c.status_code
    return token, op


# Para que una oportunidad propia salga en la lista diaria.
def envejecer(op, horas):
    """El presupuesto de una oportunidad PROPIA, creado hace esas horas."""
    # SQL: UPDATE del presupuesto, solo si la oportunidad es propia.
    cur.execute(f"UPDATE presupuestos SET created_at = now() - make_interval(hours => %(h)s) "
                f"WHERE oportunidad_id = %(op)s AND oportunidad_id IN ({SQL_OPS});", {"h": horas, "op": op, "p": PATRON})
    cn.commit()


# La dirección de las dos de la lista (CASO 5).
def direccion_a_mano(op, direccion):
    """Escribe la dirección de una oportunidad PROPIA directamente en la tabla (CASO 5)."""
    # SQL: la fila, solo si la oportunidad es propia.
    cur.execute(f"INSERT INTO direcciones_obra (oportunidad_id, calle_numero, codigo_postal, municipio) "
                f"SELECT %(op)s, %(c)s, %(cp)s, %(m)s WHERE %(op)s IN ({SQL_OPS});",
                {"op": op, "c": direccion["calle_numero"], "cp": direccion["codigo_postal"],
                 "m": direccion["municipio"], "p": PATRON})
    cn.commit()


# Una visita pedida desde el chat, para confirmar_hora, otra_hora y cliente_cancela.
def confirmacion(dia, hora="09:45"):
    """Un cliente sin Gate con una visita 'solicitada' ese día. Devuelve (token, op)."""
    # El lead y su visita pedida desde el chat (como el Agente 2).
    token, op = lead()
    r = pedir("POST", "/visits", {"lead_token": token, "fecha": str(dia), "hora": hora,
                                  "texto_cliente": "Prefiero por la mañana."}, AUTH_WEBHOOK)
    assert r.status_code == 201, r.status_code
    return token, op


# "No ha escrito nada": dos fotos iguales.
def foto_op(op):
    """Todo lo que un registro puede escribir en una oportunidad (solo recuentos y estado)."""
    # SQL: dirección, resultados, visitas, logs y estado.
    return consultar(
        """
        SELECT (SELECT count(*) FROM direcciones_obra WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM visitas WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s),
               o.estado
        FROM oportunidades o WHERE o.id = %(op)s;
        """, {"op": op})[0]


# La única lectura que puede devolverla.
def ficha(op):
    """GET de la ficha (fuera del barrido: es la ÚNICA que puede llevarla). Devuelve su JSON."""
    # La ficha, con la llave de administración.
    r = pedir("GET", f"/oportunidades/{op}/ficha", barrido=False)
    return r.json() if r.status_code == 200 else {}


# La clave del log de D42 (P1 = A).
def clave_log(op):
    """El valor de direccion_obra_registrada en el log de resultado (o "ausente"), y si el log lleva la marca."""
    # SQL: los logs de resultado de la oportunidad, con su texto.
    filas = consultar("SELECT detalle, detalle::text FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                      "AND accion IN ('gate_decision_registrada', 'llamada_registrada') ORDER BY id;", (op,))
    # Sin log: nada que mirar.
    if len(filas) != 1:
        return f"{len(filas)} logs", True
    return filas[0][0].get("direccion_obra_registrada", "ausente"), MARCA in filas[0][1]


# Nunca se usa un puerto ocupado (ni se mata nada).
def puerto_ocupado(puerto):
    """True si algo escucha en 127.0.0.1:puerto."""
    # "with" cierra el socket; connect_ex da 0 si alguien contesta.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# Fechas: los días laborables del bloque propio, uno por oportunidad con visita.
MADRID = ZoneInfo("Europe/Madrid")
HOY = datetime.now(MADRID).date()
DIAS = fechas_prueba.Dias("check_direccion_obra_http", HOY)
D_FORMA, D_GATE, D_CONF, D_OTRA, D_SIN, D_NULL = (DIAS.siguiente() for _ in range(6))
# Antes de crear nada: ninguna visita activa AJENA en el bloque.
fechas_prueba.exigir_libres("check_direccion_obra_http", PATRON,
                            [fechas_prueba.tramo_del_bloque("check_direccion_obra_http", HOY)])

# El puerto tiene que estar libre: si no, se para y se avisa.
if puerto_ocupado(PUERTO):
    print(f"ABORTO: el puerto {PUERTO} está ocupado. No ejecuto nada ni mato nada.")
    sys.exit(2)
# Restos propios de una ejecución anterior, y la foto de lo ajeno.
limpiar()
FOTO = foto_ajena()
print(f"Fechas: hoy {HOY}, primer día de prueba {D_FORMA}")

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


async def tools_mcp(op, dia):
    """Las tres tools MCP; devuelve [(nombre, texto de su salida)]."""
    # El transporte con el token Bearer.
    transporte = StreamableHttpTransport(URL_MCP, auth=MCP_SECRET)
    salidas = []
    # El cliente se conecta y se desconecta solo.
    async with Client(transporte) as client:
        # Cada tool con sus argumentos (consultar_huecos_libres en sus dos modos).
        for nombre, argumentos in (("calculate_estimate", {"oportunidad_id": op}), ("get_business_rules", {}),
                                   ("consultar_huecos_libres", {}),
                                   ("consultar_huecos_libres", {"fecha": dia.isoformat()})):
            r = await client.call_tool(nombre, argumentos)
            # El texto entero: la salida estructurada y los bloques de texto.
            texto = json.dumps(r.structured_content, ensure_ascii=False, default=str) + "".join(
                getattr(c, "text", "") for c in r.content)
            salidas.append((nombre, texto))
    return salidas


# try/finally: pase lo que pase, se apaga el servidor y se limpia.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Forma: 422 sin escribir nada")
    # Un Gate para todos los 422 de la dirección (ninguno escribe).
    tok_forma, op_forma = lead(estructural=True)
    antes = foto_op(op_forma)
    # Cada regla de D42.3, con la marca en la calle (si algo se guardara o
    # fuera a logs, el barrido lo vería).
    MALAS = [("calle vacía", {"calle_numero": ""}), ("calle solo espacios", {"calle_numero": "   "}),
             ("calle de 151", {"calle_numero": MARCA + "x" * 151}), ("municipio vacío", {"municipio": ""}),
             ("municipio de 101", {"municipio": "x" * 101}), ("CP 00123", {"codigo_postal": "00123"}),
             ("CP 53000", {"codigo_postal": "53000"}), ("CP de 4 cifras", {"codigo_postal": "2801"}),
             ("CP con letra", {"codigo_postal": "2801a"}), ("CP como NÚMERO 28013 (N15)", {"codigo_postal": 28013}),
             ("clave de más", {"piso": "3B"})]
    for titulo, cambios in MALAS:
        r = registrar(op_forma, {"resultado": "visita_acordada", "fecha": str(D_FORMA), "hora": "11:00",
                                 "direccion_obra": {**DIR_A, **cambios}})
        comprobar(f"{titulo} -> 422", r.status_code == 422, f"({r.status_code})")
    # Un objeto con un resultado SIN visita: descartar (Gate) y cliente_cancela.
    r = registrar(op_forma, {"resultado": "descartar", "motivo": "precio", "direccion_obra": DIR_A})
    comprobar("descartar con direccion_obra -> 422", r.status_code == 422, f"({r.status_code})")
    # cliente_cancela: una confirmación propia, con su foto antes.
    tok_canc, op_canc = confirmacion(D_FORMA, "17:00")
    antes_canc = foto_op(op_canc)
    r = registrar(op_canc, {"resultado": "cliente_cancela", "motivo": "plazo", "direccion_obra": DIR_A})
    comprobar("cliente_cancela con direccion_obra -> 422", r.status_code == 422, f"({r.status_code})")
    comprobar("ningún 422 ha escrito nada (dirección, resultado, visitas, logs, estado)",
              foto_op(op_forma) == antes and foto_op(op_canc) == antes_canc,
              f"({foto_op(op_forma)} vs {antes}; {foto_op(op_canc)} vs {antes_canc})")
    # null = no enviarla: descartar con null se registra (201) sin dirección.
    r = registrar(op_forma, {"resultado": "descartar", "motivo": "precio", "direccion_obra": None})
    comprobar("descartar con direccion_obra null -> 201 y ninguna dirección",
              r.status_code == 201 and foto_op(op_forma)[0] == 0, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 2 - 201 con dirección: respuesta, ficha y log")
    # Un Gate y dos confirmaciones (cada una con su día propio).
    tok_gate, op_gate = lead(estructural=True)
    tok_conf, op_conf = confirmacion(D_CONF)
    tok_otra, op_otra = confirmacion(D_OTRA)
    # (título, oportunidad, cuerpo, dirección enviada).
    CON = [("Gate visita_acordada", op_gate, {"resultado": "visita_acordada", "fecha": str(D_GATE), "hora": "11:00"},
            DIR_A),
           ("confirmar_hora", op_conf, {"resultado": "confirmar_hora", "fecha": str(D_CONF), "hora": "09:45"}, DIR_A),
           ("otra_hora", op_otra, {"resultado": "otra_hora", "fecha": str(D_OTRA), "hora": "17:00"}, DIR_B)]
    for titulo, op, cuerpo_r, direccion in CON:
        r = registrar(op, {**cuerpo_r, "direccion_obra": direccion})
        # La respuesta: claves exactas y sin la marca.
        j = r.json() if r.status_code in (200, 201) else {}
        comprobar(f"{titulo}: 201 con las claves EXACTAS de siempre y sin la dirección",
                  r.status_code == 201 and set(j) == CLAVES_RESPUESTA and MARCA not in r.text, f"({r.status_code})")
        comprobar(f"{titulo}: la ficha devuelve la dirección exacta (tres claves)",
                  ficha(op).get("direccion_obra") == direccion)
        # El log: true y sin la marca.
        valor, con_marca = clave_log(op)
        comprobar(f"{titulo}: log con direccion_obra_registrada = true y sin la dirección",
                  valor is True and not con_marca, f"({valor}, marca {con_marca})")

    # ------------------------------------------------------------------
    print("\nCASO 3 - 201 sin dirección: ficha null y log false")
    tok_sin, op_sin = lead(estructural=True)
    r = registrar(op_sin, {"resultado": "visita_acordada", "fecha": str(D_SIN), "hora": "11:00"})
    comprobar("visita_acordada sin direccion_obra -> 201", r.status_code == 201, f"({r.status_code})")
    comprobar("la ficha da direccion_obra null (clave presente)", "direccion_obra" in ficha(op_sin)
              and ficha(op_sin)["direccion_obra"] is None)
    # El log: false (rastro del olvido del formulario).
    valor, _ = clave_log(op_sin)
    comprobar("log con direccion_obra_registrada = false", valor is False, f"({valor})")
    # Con "direccion_obra": null, lo mismo que sin la clave.
    tok_null, op_null = lead(estructural=True)
    r = registrar(op_null, {"resultado": "visita_acordada", "fecha": str(D_NULL), "hora": "11:00",
                            "direccion_obra": None})
    comprobar("visita_acordada con direccion_obra null -> 201, ficha null y log false",
              r.status_code == 201 and ficha(op_null).get("direccion_obra", "x") is None and clave_log(op_null)[0] is False)

    # ------------------------------------------------------------------
    print("\nCASO 4 - Repetición (H1): no mira ni escribe la dirección")
    antes = foto_op(op_gate)
    r = registrar(op_gate, {**CON[0][2], "direccion_obra": DIR_B})
    comprobar("la MISMA petición con OTRA dirección -> 200 (creado false)",
              r.status_code == 200 and r.json().get("creado") is False, f"({r.status_code})")
    comprobar("la ficha sigue con la dirección primera y nada más escrito",
              ficha(op_gate).get("direccion_obra") == DIR_A and foto_op(op_gate) == antes)
    # Otro resultado: 409 resultado_ya_registrado (su cuerpo entra en el barrido).
    r = registrar(op_gate, {"resultado": "descartar", "motivo": "precio"})
    comprobar("otro resultado -> 409", r.status_code == 409, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 5 - D42.5: la dirección no sale en ninguna otra lectura")
    # Dos oportunidades propias que SALEN en la lista diaria, con la
    # dirección escrita a mano: un Gate sin decisión de hace 30 h (a) y un
    # seguimiento por abrir de hace 50 h (b).
    tok_lista_a, op_lista_a = lead(estructural=True)
    envejecer(op_lista_a, 30)
    direccion_a_mano(op_lista_a, DIR_A)
    # El seguimiento por abrir (sin Gate), con otra dirección.
    tok_lista_b, op_lista_b = lead()
    envejecer(op_lista_b, 50)
    direccion_a_mano(op_lista_b, DIR_B)
    # Control positivo: la ficha SÍ las devuelve (la marca está en la base).
    comprobar("control: la ficha de las dos de la lista devuelve su dirección",
              ficha(op_lista_a).get("direccion_obra") == DIR_A and ficha(op_lista_b).get("direccion_obra") == DIR_B)
    # La lista diaria: tienen que estar las dos (si no, el barrido no prueba nada).
    r = pedir("GET", "/llamadas-del-dia")
    lista = r.json() if r.status_code == 200 else {}
    # Los ids de los apartados a) y b).
    en_a = [e.get("oportunidad_id") for e in lista.get("gates_sin_decision", [])]
    en_b = [e.get("oportunidad_id") for e in lista.get("seguimientos_por_abrir", [])]
    comprobar("las dos propias salen en la lista diaria (a y b)", op_lista_a in en_a and op_lista_b in en_b,
              f"({r.status_code}, a {op_lista_a in en_a}, b {op_lista_b in en_b})")
    # Las demás lecturas de esas oportunidades y de las del CASO 2.
    for token in (tok_lista_a, tok_lista_b, tok_gate, tok_conf, tok_otra):
        pedir("GET", f"/leads/session/{token}", cabeceras=AUTH_WEBHOOK)
    # El aviso del Gate (las dos con Gate).
    for op in (op_lista_a, op_gate):
        pedir("GET", f"/gate-avisos/{op}")
    # El cálculo (repetición) de cuatro con dirección.
    for op in (op_lista_a, op_lista_b, op_gate, op_conf):
        pedir("POST", "/calculate-estimate", {"oportunidad_id": op}, AUTH_WEBHOOK)
    # create-followup-task: un 201 (la de b) y un 409.
    for op in (op_lista_b, op_conf):
        pedir("POST", "/create-followup-task", {"oportunidad_id": op, "motivo": "sin_respuesta_visita"})
    pedir("GET", "/huecos-libres")
    pedir("GET", f"/huecos-libres?fecha={D_CONF}")
    # POST /visits sobre una que ya tiene visita confirmada y dirección (409).
    pedir("POST", "/visits", {"lead_token": tok_conf, "fecha": str(D_CONF), "hora": "11:00",
                              "texto_cliente": "Otra hora, por favor."}, AUTH_WEBHOOK)
    # Las tres tools MCP (calculate_estimate sobre una con dirección).
    for nombre, texto in asyncio.run(tools_mcp(op_lista_a, D_CONF)):
        textos_barrido.append((f"MCP {nombre}", texto))
    # El barrido: la marca en NINGUNA de esas respuestas.
    con_marca = [ruta for ruta, texto in textos_barrido if MARCA in texto]
    print(f"  [INFO] respuestas barridas: {len(textos_barrido)} (ficha y 422 aparte)")
    comprobar("la marca aparece 0 veces fuera de la ficha (rutas REST, tools MCP, 200/201/409)", con_marca == [],
              f"({con_marca})")
    comprobar("el barrido incluye gate-avisos, la lista, la sesión, huecos, visits, followup y las 3 tools",
              all(any(ruta.startswith(p) for ruta, _ in textos_barrido) for p in
                  ("GET /gate-avisos", "GET /llamadas-del-dia", "GET /leads/session", "GET /huecos-libres",
                   "POST /visits", "POST /create-followup-task", "POST /calculate-estimate",
                   "MCP calculate_estimate", "MCP get_business_rules", "MCP consultar_huecos_libres")))

    # ------------------------------------------------------------------
    print("\nCASO 6 - Logs y 500")
    # SQL: logs escritos desde el id base con la marca en su detalle (todas
    # las acciones, también error_no_controlado).
    n = consultar("SELECT count(*) FROM logs WHERE id > %s AND detalle::text LIKE %s;",
                  (FOTO["logs"][0], f"%{MARCA}%"))[0][0]
    comprobar("la marca aparece 0 veces en logs.detalle", n == 0, f"({n})")
    comprobar(f"ninguna respuesta 500 ({len(codigos)} peticiones)", 500 not in codigos)
    print(f"  [INFO] H3: respuestas 422 que repiten la marca (limitación aceptada): {eco_422}")
finally:
    # Se apaga el servidor (el hilo termina solo).
    servidor.should_exit = True
    hilo.join(timeout=15)
    # Los logs del manejador global de los 500 de ESTA ejecución, de las
    # rutas que pide el script; se borran por id exacto si cuadran.
    rutas = ["/resultados-llamada", "/leads", "/calculate-estimate", "/visits", "/create-followup-task",
             "/llamadas-del-dia", "/huecos-libres"]
    # SQL: los ids de esos logs.
    ids_500 = [f[0] for f in consultar(
        "SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' AND (detalle->>'ruta' = ANY(%s) "
        "OR detalle->>'ruta' LIKE '/oportunidades/%%' OR detalle->>'ruta' LIKE '/leads/session/%%' "
        "OR detalle->>'ruta' LIKE '/gate-avisos/%%') ORDER BY id;", (FOTO["logs"][0], rutas))]
    print(f"\n  respuestas 500: {codigos.count(500)}; logs error_no_controlado de estas rutas: {ids_500}")
    comprobar("los logs de 500 son exactamente los esperados", len(ids_500) == codigos.count(500))
    # Borrado por id exacto, solo si cuadran.
    if ids_500 and len(ids_500) == codigos.count(500):
        # SQL: DELETE de esos logs por su id.
        cur.execute("DELETE FROM logs WHERE id = ANY(%s);", (ids_500,))
        cn.commit()
    # Lo propio, y lo ajeno con los MISMOS id_base (solo recuentos).
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
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con fallos, se listan y el código de salida es 1 (lo mira la suite).
if fallos:
    print("FALLOS:")
    # Uno por línea (los títulos nunca llevan una dirección).
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
