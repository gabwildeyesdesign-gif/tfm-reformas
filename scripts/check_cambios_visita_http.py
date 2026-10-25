"""
Verificación por HTTP REAL de POST /cambios-visita y de lo que cambia en la
ficha (D46; plan docs/Plan_Cambio_Visita_D46.txt, sección 8, verdes V1-V12,
con el veredicto del tutor al PASO 0).

Levanta el servidor en un hilo de este proceso (sin --reload, puerto 8036,
nunca el 8000), crea sus propios clientes por POST /leads y
/calculate-estimate, y sus visitas confirmadas por POST /resultados-llamada
(visita_acordada de un Gate), como lo hará n8n; por SQL solo lo que no tiene
otro camino (una confirmada ya pasada y una fuera de la rejilla). Prueba:
  CASO 1  V9: 401 sin X-Gate-Secret, con la llave del Agente 2 y con el
          valor de WEBHOOK_SECRET; 401 antes que el 422
  CASO 2  V6 (forma): 422 de Pydantic sin escribir nada
  CASO 3  V9: 404
  CASO 4  V1: cambiar, con todos sus efectos y las claves EXACTAS
  CASO 5  V4 y V2: la repetición (200 sin escribir) y un segundo cambio
  CASO 6  V3 y V4: cancelar, su repetición y 409 visita_ya_cancelada
  CASO 7  V5: 409 sin_visita_confirmada (Gate sin visita y visita solo
          'solicitada'), visita_cambiada y visita_pasada
  CASO 8  V6 (negocio): misma_hora_actual, fin_de_semana, fuera_de_rejilla
          con horas_validas, y hoy en dos ramas deterministas (R5 de D45)
  CASO 9  V7: la propia visita no cuenta en el hueco; otra sí (409 con
          huecos_libres)
  CASO 10 V8: 5 cambios distintos a la vez sobre la misma oportunidad (un
          201 y cuatro 409 visita_cambiada) y 2 cambios a la misma hora en
          oportunidades distintas (un 201 y un 409 hueco_ocupado)
  CASO 11 V10: la ficha antes y después (visita_modificable, cambios_visita
          con tipo_cambio igual a la accion de la tabla, historial_cuadra y
          el evento de la cancelación; sin ids de visita, H-D46-3)
  CASO 12 V12: ningún importe ni la dirección en las respuestas de la ruta;
          la marca de la dirección 0 veces en sus respuestas y en los logs;
          ningún 500; 201 <-> creado

Recuentos: previstos en el plan ANTES de la fase 2 (precisión 5) y reales
después; en el commit, calculados en una orden anterior (D37.5).

Las 4 preguntas: (1) escribe en tablas reales SOLO filas propias (emails
http-cambio-<8>@example.com, tokens uuid4), con commit; por SQL, sobre
oportunidades PROPIAS (guarda SQL_OPS): cambia el estado de una a
'visita_agendada' y le pone una confirmada ya pasada, y pone una confirmada
fuera de la rejilla en otra; (2) no pasa ninguna conexión al código (todo
por HTTP); (3) si se corta, la siguiente ejecución borra lo propio al
empezar (cambios_visita y direcciones_obra antes que visitas y
oportunidades), y "no toca nada ajeno" (id_base) lo comprueba; los logs de
un 500 se borran por id exacto tras comprobarlos; (4) puerto 8036, ningún
dato real; sus visitas van en su bloque de fechas_prueba.py, con
exigir_libres antes de crear nada (la pasada, de hace dos horas, no ocupa
ningún hueco futuro).
"""

# json: buscar claves en el texto. socket, threading, time, uuid: puerto,
# servidor, esperas y tokens. ThreadPoolExecutor: peticiones a la vez.
import json
# socket: comprobar que el puerto está libre. sys: consola y salida.
import socket
import sys
# threading: el servidor en un hilo; time: esperas; uuid: tokens y marca.
import threading
import time
import uuid
# ThreadPoolExecutor: varias peticiones a la vez (CASO 10).
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

# Las fechas propias de este script (bloque de semanas lejanas).
import fechas_prueba

# Puerto propio (8036, comprobado libre en el PASO 1) y dirección base.
PUERTO = 8036
BASE = f"http://127.0.0.1:{PUERTO}"
# Llave de n8n (leads, cálculo, visitas) y de administración (el resto).
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# La marca propia de los clientes.
PATRON = "http-cambio-%@example.com"
# La marca de ESTA ejecución, dentro de la dirección de la obra (CASO 12).
MARCA = f"MARCA-D46H-{uuid.uuid4().hex[:10]}"
# Las claves EXACTAS de la respuesta (plan, sección 3), copiadas del PLAN.
CLAVES_RESPUESTA = {"cambio_id", "oportunidad_id", "accion", "motivo", "estado_oportunidad", "visita_id",
                    "fecha_visita", "fecha_fin_visita", "visita_cancelada_id", "creado"}
# Las de la ficha (plan, sección 5, con la corrección del tutor: tipo_cambio).
CLAVES_MODIFICABLE = {"fecha_visita", "acciones"}
CLAVES_CAMBIO = {"tipo_cambio", "motivo", "fecha_registro", "fecha_visita_anterior", "fecha_visita_nueva", "informe"}
# El texto fijo de la visita nueva (P6), copiado del veredicto del tutor.
TEXTO_CAMBIADA = "Visita cambiada por teléfono por administración."
# Informe por defecto de los cambios.
INFORME = "Cambio de prueba D46."

# Contadores, y registros de todas las respuestas de la ruta.
ok, fallos, codigos, cuerpos, pares = 0, [], [], [], []


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


# C1 (veredicto al PASO 1): la hora de referencia de las pruebas.
def ahora_postgres():
    """
    El now() de Postgres, leído en una transacción de SOLO LECTURA (C1 del
    veredicto al PASO 1: la referencia de tiempo es el reloj de la base de
    datos, precisión 2, nunca el del PC). Cada llamada abre una transacción
    nueva, así que da la hora de ESE momento.
    """
    # SQL: la primera orden de la transacción la hace READ ONLY.
    cur.execute("SET TRANSACTION READ ONLY;")
    # SQL: la hora de la base de datos.
    cur.execute("SELECT now();")
    ahora = cur.fetchone()[0]
    # Se cierra la transacción de lectura.
    cn.commit()
    return ahora


# Qué es propio: lo que cuelga de los clientes con la marca.
SQL_OPS = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
# Por tabla, la condición de sus filas propias, en el orden de borrado
# (cambios_visita, decisiones_gate y direcciones_obra antes que visitas y
# oportunidades: sus claves foráneas).
PROPIO = {
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})",
    "cambios_visita": f"oportunidad_id IN ({SQL_OPS})",
    "direcciones_obra": f"oportunidad_id IN ({SQL_OPS})",
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
    cn.commit()


def foto_ajena():
    """Por tabla: (id_base, filas NO propias con id <= id_base). Solo recuentos."""
    # Diccionario tabla -> (id_base, ajenas).
    foto = {}
    for tabla, condicion in PROPIO.items():
        # SQL: el id más alto ahora, y cuántas filas NO propias hay hasta él.
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        # El id más alto (la base de esta tabla).
        base = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(b)s AND NOT ({condicion});", {"p": PATRON, "b": base})
        # El par (base, ajenas) de la tabla.
        foto[tabla] = (base, cur.fetchone()[0])
    cn.commit()
    return foto


def pedir(metodo, ruta, cuerpo=None, cabeceras=AUTH_GATE):
    """Una petición HTTP real; apunta el código. Devuelve la respuesta."""
    # requests.request: GET o POST, con el cuerpo JSON si lo hay.
    r = requests.request(metodo, f"{BASE}{ruta}", headers=cabeceras, json=cuerpo, timeout=60)
    codigos.append(r.status_code)
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


def madrid(dia: date, hh: int, mm: int) -> datetime:
    """Ese instante en Madrid."""
    # combine con la zona: el desfase de ese día.
    return datetime.combine(dia, datetime.min.time().replace(hour=hh, minute=mm), tzinfo=MADRID)


def cambio(op, accion, actual, nueva=None, motivo_c=None, informe=INFORME, cabeceras=AUTH_GATE, extra=None):
    """
    POST /cambios-visita. actual y nueva: instantes en Madrid. Guarda el
    cuerpo de la respuesta y, en los éxitos, el par (código, creado).
    """
    # El cuerpo: la visita que "ve" el operador y el informe.
    cuerpo = {"oportunidad_id": op, "accion": accion, "fecha_actual": f"{actual:%Y-%m-%d}",
              "hora_actual": f"{actual:%H:%M}", "informe": informe}
    # Al cambiar, la nueva.
    if nueva is not None:
        cuerpo |= {"fecha": f"{nueva:%Y-%m-%d}", "hora": f"{nueva:%H:%M}"}
    # Al cancelar, el motivo.
    if motivo_c is not None:
        cuerpo["motivo"] = motivo_c
    # Campos de más o cambiados (CASO 2).
    cuerpo |= extra or {}
    r = pedir("POST", "/cambios-visita", cuerpo, cabeceras)
    # Para el CASO 12: cada cuerpo y su texto; en los éxitos, el par código/creado.
    cuerpos.append((r.status_code, r.text))
    # Solo en los éxitos: el par (código, creado).
    if r.status_code in (200, 201):
        pares.append((r.status_code, cuerpo_de(r).get("creado")))
    return r


def lead(estructural=True):
    """Lead propio por POST /leads y su cálculo. Devuelve (token, oportunidad_id)."""
    # Token único; sus 8 primeros caracteres van en el email (la marca).
    token = str(uuid.uuid4())
    r = pedir("POST", "/leads", {
        "nombre": "Prueba cambio", "email": f"http-cambio-{token[:8]}@example.com", "telefono": "600000000",
        "tipo_reforma": "bano", "m2": 6, "nivel_acabados": "medio",
        "incluye_cambios_estructurales": estructural, "lead_token": token}, AUTH_WEBHOOK)
    # Sin datos de prueba no tiene sentido seguir: se para con el código.
    assert r.status_code == 201, r.status_code
    op = r.json()["oportunidad_id"]
    # El cálculo (con cambios estructurales queda con Gate).
    c = pedir("POST", "/calculate-estimate", {"oportunidad_id": op}, AUTH_WEBHOOK)
    assert c.status_code == 200, c.status_code
    return token, op


def con_visita(dia, hh, mm, direccion=None):
    """Un Gate propio con su visita CONFIRMADA por POST /resultados-llamada. Devuelve op."""
    # El Gate.
    _, op = lead()
    # El cuerpo de la llamada: visita_acordada a esa hora (y la dirección, si se da).
    cuerpo = {"oportunidad_id": op, "resultado": "visita_acordada", "fecha": str(dia), "hora": f"{hh:02d}:{mm:02d}",
              "informe": "Llamada de prueba D46."}
    if direccion is not None:
        cuerpo["direccion_obra"] = direccion
    r = pedir("POST", "/resultados-llamada", cuerpo)
    # Sin la visita no hay caso: se para con el código.
    assert r.status_code == 201, r.status_code
    return op


def por_sql_confirmada(op, cuando):
    """Pone a mano una visita 'confirmada' en una oportunidad PROPIA sin visita
    (y la oportunidad en 'visita_agendada'): solo para lo que no tiene otro
    camino (una pasada, una fuera de la rejilla)."""
    # SQL: el estado, solo si la oportunidad es propia.
    cur.execute(f"UPDATE oportunidades SET estado = 'visita_agendada' WHERE id = %(op)s AND id IN ({SQL_OPS});",
                {"op": op, "p": PATRON})
    # SQL: la visita, solo si la oportunidad es propia.
    cur.execute(f"INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                f"SELECT %(op)s, %(f)s, 'confirmada', 'puesta a mano (D46)' WHERE %(op)s IN ({SQL_OPS});",
                {"op": op, "f": cuando, "p": PATRON})
    # Exactamente una fila (si no, el caso no existe: se para).
    assert cur.rowcount == 1, cur.rowcount
    cn.commit()


def foto_op(op):
    """Todo lo que un cambio puede escribir en una oportunidad (dos fotos iguales: nada escrito)."""
    # SQL: cambios, visitas (estado y fecha), logs, estado y fechas.
    return consultar(
        """
        SELECT (SELECT count(*) FROM cambios_visita WHERE oportunidad_id = %(op)s),
               (SELECT string_agg(estado || '@' || fecha_propuesta::text, ',' ORDER BY id) FROM visitas
                WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s),
               o.estado, o.fecha_ultimo_contacto, o.updated_at
        FROM oportunidades o WHERE o.id = %(op)s;
        """, {"op": op})[0]


def ficha(op):
    """GET de la ficha. Devuelve su JSON ({} si no es 200)."""
    # La ficha, con la llave de administración.
    r = pedir("GET", f"/oportunidades/{op}/ficha")
    return cuerpo_de(r) if r.status_code == 200 else {}


def claves_anidadas(objeto):
    """Todas las claves de un JSON, en cualquier nivel."""
    # Un diccionario: sus claves y las de sus valores; una lista, las de cada
    # elemento; un valor simple, ninguna.
    if isinstance(objeto, dict):
        return set(objeto).union(*(claves_anidadas(v) for v in objeto.values()))
    # Una lista: las de cada elemento.
    if isinstance(objeto, list):
        return set().union(*(claves_anidadas(e) for e in objeto))
    # Un valor simple (texto, número, null): ninguna.
    return set()


def puerto_ocupado(puerto):
    """True si algo escucha en 127.0.0.1:puerto."""
    # "with" cierra el socket; connect_ex da 0 si alguien contesta.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# Fechas: los días laborables del bloque propio, uno por oportunidad con visita.
MADRID = ZoneInfo("Europe/Madrid")
# C1 (veredicto al PASO 1): "hoy" en Madrid sale del now() de Postgres.
HOY = ahora_postgres().astimezone(MADRID).date()
DIAS = fechas_prueba.Dias("check_cambios_visita_http", HOY)
(D_FORMA, D_CAMBIO, D_CANCEL, D_409, D_SOL, D_HUECO, D_CONC1, D_CONC2) = (DIAS.siguiente() for _ in range(8))
# El sábado de la semana de D_409 (fin_de_semana).
SABADO = D_409 + timedelta(days=5 - D_409.weekday())
# Antes de crear nada: ninguna visita activa AJENA en el bloque.
fechas_prueba.exigir_libres("check_cambios_visita_http", PATRON,
                            [fechas_prueba.tramo_del_bloque("check_cambios_visita_http", HOY)])
# La duración REAL de la visita, leída aquí aparte (no con el código probado).
DURACION = int(consultar("SELECT valor FROM reglas_negocio WHERE clave = 'duracion_visita_min';")[0][0])

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

# try/finally: pase lo que pase, se apaga el servidor y se limpia.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación (V9)")
    # Una oportunidad con visita para todos los intentos sin llave (ninguno escribe).
    op_forma = con_visita(D_FORMA, 8, 30)
    ACTUAL_FORMA = madrid(D_FORMA, 8, 30)
    antes = foto_op(op_forma)
    # (título, cabeceras)
    for titulo, cabeceras in (("sin X-Gate-Secret", {}), ("con la llave del Agente 2 (X-Webhook-Secret)", AUTH_WEBHOOK),
                              ("X-Gate-Secret con el valor de WEBHOOK_SECRET", {"X-Gate-Secret": WEBHOOK_SECRET})):
        r = cambio(op_forma, "cancelar", ACTUAL_FORMA, motivo_c="precio", cabeceras=cabeceras)
        comprobar(f"{titulo} -> 401", r.status_code == 401, f"({r.status_code})")
    # Sin llave y con el cuerpo mal: 401 antes que el 422.
    r = pedir("POST", "/cambios-visita", {"oportunidad_id": "no es un número"}, {})
    comprobar("sin llave y con el cuerpo mal -> 401 (antes que el 422)", r.status_code == 401, f"({r.status_code})")
    comprobar("ningún 401 ha escrito nada", foto_op(op_forma) == antes)

    # ------------------------------------------------------------------
    print("\nCASO 2 - Forma: 422 de Pydantic sin escribir nada (V6)")
    # (título, acción, nueva, motivo, campos de más o cambiados)
    MALAS = [("sin informe", "cancelar", None, "precio", {"informe": None}),
             ("informe solo espacios", "cancelar", None, "precio", {"informe": "   "}),
             ("informe de 2001", "cancelar", None, "precio", {"informe": "x" * 2001}),
             ("informe con U+0000", "cancelar", None, "precio", {"informe": "Informe\u0000"}),
             ("motivo no_contesta", "cancelar", None, "no_contesta", {}),
             ("motivo sin_decision", "cancelar", None, "sin_decision", {}),
             ("cancelar sin motivo", "cancelar", None, None, {}),
             ("cancelar con fecha y hora", "cancelar", madrid(D_FORMA, 11, 0), "precio", {}),
             ("cambiar con motivo", "cambiar", madrid(D_FORMA, 11, 0), "precio", {}),
             ("cambiar sin hora", "cambiar", None, None, {"fecha": str(D_FORMA)}),
             ("direccion_obra (no es de la visita)", "cancelar", None, "precio",
              {"direccion_obra": {"calle_numero": "Calle 1", "codigo_postal": "28013", "municipio": "Madrid"}}),
             ("visita_id (la ruta no lo admite)", "cancelar", None, "precio", {"visita_id": 1}),
             ("sin hora_actual", "cancelar", None, "precio", {"hora_actual": None}),
             ("hora_actual sin el cero (8:30)", "cancelar", None, "precio", {"hora_actual": "8:30"}),
             ("acción desconocida", "mover", None, None, {})]
    # Cada una sobre la misma oportunidad (un 422 no escribe).
    for titulo, accion, nueva, motivo_c, extra in MALAS:
        r = cambio(op_forma, accion, ACTUAL_FORMA, nueva, motivo_c, extra=extra)
        comprobar(f"{titulo} -> 422", r.status_code == 422, f"({r.status_code})")
    comprobar("ningún 422 de forma ha escrito nada", foto_op(op_forma) == antes)

    # ------------------------------------------------------------------
    print("\nCASO 3 - 404 (V9)")
    r = cambio(2147483647, "cancelar", ACTUAL_FORMA, motivo_c="precio")
    comprobar("oportunidad inexistente -> 404 oportunidad_no_encontrada",
              r.status_code == 404 and motivo(r) == "oportunidad_no_encontrada", f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 4 - Cambiar (V1)")
    # La visita confirmada CON dirección de la obra (con la marca, CASO 12).
    DIR = {"calle_numero": f"Calle {MARCA} 1", "codigo_postal": "28013", "municipio": "Municipio de Prueba"}
    op_c = con_visita(D_CAMBIO, 8, 30, DIR)
    # La visita vieja (su id), leída aquí.
    vieja_c = consultar("SELECT id FROM visitas WHERE oportunidad_id = %s AND estado = 'confirmada';", (op_c,))[0][0]
    # La ficha ANTES (CASO 11 la mira).
    FICHA_ANTES = ficha(op_c)
    r = cambio(op_c, "cambiar", madrid(D_CAMBIO, 8, 30), madrid(D_CAMBIO, 11, 0), informe="  Informe del cambio.  ")
    j = cuerpo_de(r)
    comprobar("201 con las claves EXACTAS, creado, accion cambiar, 'visita_agendada', sin motivo",
              r.status_code == 201 and set(j) == CLAVES_RESPUESTA and j.get("creado") is True
              and j.get("accion") == "cambiar" and j.get("estado_oportunidad") == "visita_agendada"
              and j.get("motivo") is None, f"({r.status_code} {r.text[:200]})")
    # Las fechas: la nueva en Madrid y su fin con la duración real.
    fin = (madrid(D_CAMBIO, 11, 0).astimezone(timezone.utc) + timedelta(minutes=DURACION)).astimezone(MADRID)
    comprobar(f"fecha_visita 11:00 y fecha_fin_visita + {DURACION} min, escritas como la API",
              j.get("fecha_visita") == madrid(D_CAMBIO, 11, 0).isoformat() and j.get("fecha_fin_visita") == fin.isoformat(),
              f"({j.get('fecha_visita')} -> {j.get('fecha_fin_visita')})")
    # SQL: las visitas, la fila y la oportunidad, tal como quedaron.
    visitas_c = consultar("SELECT id, estado, texto_cliente FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (op_c,))
    comprobar("los ids de la respuesta son los de la tabla: la vieja 'cancelada' y la nueva 'confirmada' con el texto fijo",
              [(e, t) for _, e, t in visitas_c][1:] == [("confirmada", TEXTO_CAMBIADA)] and visitas_c[0][1] == "cancelada"
              and j.get("visita_cancelada_id") == vieja_c == visitas_c[0][0] and j.get("visita_id") == visitas_c[1][0],
              f"({visitas_c})")
    # SQL: la fila de cambios_visita de la oportunidad.
    filas_c = consultar("SELECT id, accion, visita_anterior_id, visita_nueva_id, motivo, informe FROM cambios_visita "
                        "WHERE oportunidad_id = %s;", (op_c,))
    comprobar("una fila en cambios_visita (cambiar, vieja, nueva, sin motivo, informe recortado) con el cambio_id",
              filas_c == [(j.get("cambio_id"), "cambiar", vieja_c, j.get("visita_id"), None, "Informe del cambio.")],
              f"({filas_c})")
    # SQL: el estado de la oportunidad y si se escribió el contacto.
    o = consultar("SELECT estado, fecha_ultimo_contacto IS NOT NULL FROM oportunidades WHERE id = %s;", (op_c,))[0]
    comprobar("oportunidad sigue en 'visita_agendada' y fecha_ultimo_contacto escrita (P8)", o == ("visita_agendada", True))
    # SQL: el log del cambio, con su texto.
    logs = consultar("SELECT detalle, detalle::text FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                     "AND accion = 'visita_confirmada_cambiada';", (op_c,))
    comprobar("un log visita_confirmada_cambiada con 'estado' y sin el informe (N12)",
              len(logs) == 1 and logs[0][0].get("estado") == "visita_agendada"
              and "Informe del cambio" not in logs[0][1] and "informe" not in logs[0][0], f"({logs})")

    # ------------------------------------------------------------------
    print("\nCASO 5 - Repetición y segundo cambio (V4, V2)")
    antes = foto_op(op_c)
    r = cambio(op_c, "cambiar", madrid(D_CAMBIO, 8, 30), madrid(D_CAMBIO, 11, 0), informe="Otro informe.")
    comprobar("la MISMA petición -> 200, creado false, el mismo cambio_id y visita_id, nada escrito",
              r.status_code == 200 and cuerpo_de(r).get("creado") is False
              and cuerpo_de(r).get("cambio_id") == j.get("cambio_id") and cuerpo_de(r).get("visita_id") == j.get("visita_id")
              and foto_op(op_c) == antes, f"({r.status_code})")
    # Segundo cambio, sin límite (D46.2): de las 11:00 a las 17:00.
    r = cambio(op_c, "cambiar", madrid(D_CAMBIO, 11, 0), madrid(D_CAMBIO, 17, 0), informe="Segundo cambio.")
    estados = [f[0] for f in consultar("SELECT estado FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (op_c,))]
    comprobar("segundo cambio -> 201; dos filas y solo la última visita confirmada",
              r.status_code == 201 and foto_op(op_c)[0] == 2 and estados == ["cancelada", "cancelada", "confirmada"],
              f"({r.status_code} {estados})")

    # ------------------------------------------------------------------
    print("\nCASO 6 - Cancelar (V3, V4)")
    op_x = con_visita(D_CANCEL, 9, 45)
    # La confirmada que se va a cancelar (su id).
    vieja_x = consultar("SELECT id FROM visitas WHERE oportunidad_id = %s AND estado = 'confirmada';", (op_x,))[0][0]
    # La cancelación, con motivo e informe.
    r = cambio(op_x, "cancelar", madrid(D_CANCEL, 9, 45), motivo_c="plazo", informe="Cancela por plazo.")
    # Su JSON.
    jx = cuerpo_de(r)
    comprobar("201 con las claves EXACTAS: cancelar, plazo, 'perdida', visita_id y fechas null, la cancelada",
              r.status_code == 201 and set(jx) == CLAVES_RESPUESTA and jx.get("accion") == "cancelar"
              and jx.get("motivo") == "plazo" and jx.get("estado_oportunidad") == "perdida" and jx.get("visita_id") is None
              and jx.get("fecha_visita") is None and jx.get("fecha_fin_visita") is None
              and jx.get("visita_cancelada_id") == vieja_x, f"({r.status_code} {r.text[:200]})")
    # SQL: la visita, la oportunidad, la fila y el log.
    fila = consultar("SELECT (SELECT estado FROM visitas WHERE id = %s), o.estado, "
                     "(SELECT count(*) FROM cambios_visita WHERE oportunidad_id = o.id AND accion = 'cancelar' "
                     " AND motivo = 'plazo' AND visita_nueva_id IS NULL AND informe = 'Cancela por plazo.'), "
                     "(SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = o.id "
                     " AND accion = 'visita_confirmada_cancelada' AND detalle->>'estado' = 'perdida' "
                     " AND NOT detalle ? 'informe') FROM oportunidades o WHERE o.id = %s;", (vieja_x, op_x))[0]
    comprobar("visita 'cancelada', oportunidad 'perdida', una fila con motivo e informe y un log sin informe",
              fila == ("cancelada", "perdida", 1, 1), f"({fila})")
    # La foto, para "nada escrito" en lo que sigue.
    antes = foto_op(op_x)
    # La misma cancelación, con otro informe.
    r = cambio(op_x, "cancelar", madrid(D_CANCEL, 9, 45), motivo_c="plazo", informe="Otro informe.")
    comprobar("la MISMA cancelación -> 200, creado false, el mismo cambio_id, nada escrito (N10: no 409)",
              r.status_code == 200 and cuerpo_de(r).get("creado") is False
              and cuerpo_de(r).get("cambio_id") == jx.get("cambio_id") and foto_op(op_x) == antes, f"({r.status_code})")
    # Otro motivo y cambiar: ya no se puede.
    for titulo, args in (("otro motivo", ("cancelar", madrid(D_CANCEL, 9, 45), None, "precio")),
                         ("cambiar", ("cambiar", madrid(D_CANCEL, 9, 45), madrid(D_CANCEL, 11, 0), None))):
        r = cambio(op_x, *args)
        comprobar(f"tras cancelar, {titulo} -> 409 visita_ya_cancelada, nada escrito",
                  r.status_code == 409 and motivo(r) == "visita_ya_cancelada" and foto_op(op_x) == antes,
                  f"({r.status_code} {motivo(r)})")

    # ------------------------------------------------------------------
    print("\nCASO 7 - 409 de la visita (V5)")
    # Un Gate sin decisión (sin visita).
    _, op_sin = lead()
    # Una visita solo 'solicitada', pedida desde el chat (sin Gate).
    tok_sol, op_sol = lead(estructural=False)
    # Su visita pedida desde el chat (sin ella no hay caso: se para).
    r = pedir("POST", "/visits", {"lead_token": tok_sol, "fecha": str(D_SOL), "hora": "09:45",
                                  "texto_cliente": "Prefiero por la mañana."}, AUTH_WEBHOOK)
    assert r.status_code == 201, r.status_code
    # Las dos, con la foto antes y después.
    for titulo, op, actual in (("Gate sin visita", op_sin, madrid(D_SOL, 9, 45)),
                               ("visita solo 'solicitada'", op_sol, madrid(D_SOL, 9, 45))):
        antes = foto_op(op)
        r = cambio(op, "cancelar", actual, motivo_c="precio")
        comprobar(f"{titulo} -> 409 sin_visita_confirmada, nada escrito",
                  r.status_code == 409 and motivo(r) == "sin_visita_confirmada" and foto_op(op) == antes,
                  f"({r.status_code} {motivo(r)})")
    # visita_cambiada: la pantalla cree que es a las 09:45 y es a las 08:30.
    op_409 = con_visita(D_409, 8, 30)
    antes_409 = foto_op(op_409)
    r = cambio(op_409, "cambiar", madrid(D_409, 9, 45), madrid(D_409, 11, 0))
    comprobar("fecha_actual que no es la confirmada -> 409 visita_cambiada, nada escrito",
              r.status_code == 409 and motivo(r) == "visita_cambiada" and foto_op(op_409) == antes_409,
              f"({r.status_code} {motivo(r)})")
    # visita_pasada: una confirmada de hace dos horas, puesta por SQL (a minuto entero).
    _, op_pas = lead()
    # Hace dos horas, a minuto entero (si no, daría visita_cambiada antes).
    pasada = (ahora_postgres().astimezone(MADRID) - timedelta(hours=2)).replace(second=0, microsecond=0)
    por_sql_confirmada(op_pas, pasada)
    # La foto antes, y la cancelación de la pasada.
    antes = foto_op(op_pas)
    r = cambio(op_pas, "cancelar", pasada, motivo_c="precio")
    comprobar("visita ya pasada -> 409 visita_pasada, nada escrito",
              r.status_code == 409 and motivo(r) == "visita_pasada" and foto_op(op_pas) == antes,
              f"({r.status_code} {motivo(r)})")

    # ------------------------------------------------------------------
    print("\nCASO 8 - 422 de negocio de la hora nueva (V6)")
    # (título, hora nueva, motivo esperado), sobre op_409 (08:30 de D_409).
    for titulo, nueva, esperado in (("la misma hora", madrid(D_409, 8, 30), "misma_hora_actual"),
                                    ("un sábado", madrid(SABADO, 9, 45), "fin_de_semana"),
                                    ("fuera de la rejilla", madrid(D_409, 10, 0), "fuera_de_rejilla")):
        r = cambio(op_409, "cambiar", madrid(D_409, 8, 30), nueva)
        comprobar(f"{titulo} -> 422 {esperado}, nada escrito",
                  r.status_code == 422 and motivo(r) == esperado and foto_op(op_409) == antes_409,
                  f"({r.status_code} {motivo(r)})")
    # fuera_de_rejilla (la última del bucle) trae horas_validas (D32). Un 422
    # de Pydantic trae una lista en "detail": FALLO contado, nunca una traza.
    detalle = cuerpo_de(r).get("detail")
    comprobar("fuera_de_rejilla trae horas_validas por franja",
              isinstance(detalle, dict) and isinstance(detalle.get("horas_validas"), dict))
    # Hoy (R5 de D45): las 18:15 de hoy. Si aún no han llegado, antelación
    # insuficiente (administración empieza el 1.er laborable después de
    # hoy); si ya pasaron, fecha_pasada. La regla la prueba el servicio.
    hoy_tarde = madrid(HOY, 18, 15)
    # C1: con el now() de Postgres de este momento (un minuto de margen por
    # el tiempo que tarda la petición en llegar).
    esperado = "antelacion_insuficiente" if hoy_tarde > ahora_postgres() + timedelta(minutes=1) else "fecha_pasada"
    print(f"  [INFO] hoy a las 18:15: se espera {esperado}")
    # El cambio a hoy, que no escribe nada.
    r = cambio(op_409, "cambiar", madrid(D_409, 8, 30), hoy_tarde)
    comprobar(f"hoy a las 18:15 -> 422 {esperado}, nada escrito",
              r.status_code == 422 and motivo(r) == esperado and foto_op(op_409) == antes_409,
              f"({r.status_code} {motivo(r)})")

    # ------------------------------------------------------------------
    print("\nCASO 9 - El hueco libre (V7)")
    # Una confirmada antigua fuera de la rejilla (09:00, por SQL), que choca
    # con las 09:45; y otra oportunidad propia a las 12:15 del mismo día.
    _, op_h1 = lead()
    por_sql_confirmada(op_h1, madrid(D_HUECO, 9, 0))
    op_h2 = con_visita(D_HUECO, 12, 15)
    # A las 12:15: la ocupa op_h2.
    antes = foto_op(op_h1)
    r = cambio(op_h1, "cambiar", madrid(D_HUECO, 9, 0), madrid(D_HUECO, 12, 15))
    # huecos_libres, solo si "detail" es un objeto (si no, None: FALLO contado).
    detalle = cuerpo_de(r).get("detail")
    libres = detalle.get("huecos_libres") if isinstance(detalle, dict) else None
    comprobar("a la hora de OTRA visita -> 409 hueco_ocupado con huecos_libres (sin 12:15, con 09:45), nada escrito",
              r.status_code == 409 and motivo(r) == "hueco_ocupado" and isinstance(libres, list) and "12:15" not in libres
              and "09:45" in libres and foto_op(op_h1) == antes, f"({r.status_code} {motivo(r)} {libres})")
    # A las 09:45: choca con la PROPIA (09:00), que no cuenta (N3).
    r = cambio(op_h1, "cambiar", madrid(D_HUECO, 9, 0), madrid(D_HUECO, 9, 45))
    comprobar("a una hora que choca con la PROPIA visita vieja -> 201 (no cuenta: se cancela)",
              r.status_code == 201, f"({r.status_code} {motivo(r)})")

    # ------------------------------------------------------------------
    print("\nCASO 10 - Concurrencia (V8)")
    # 5 cambios DISTINTOS a la vez sobre la misma oportunidad (08:30 de D_CONC1).
    op_k = con_visita(D_CONC1, 8, 30)
    horas = [(9, 45), (11, 0), (12, 15), (17, 0), (18, 15)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        # Las cinco a la vez; list() espera a todas.
        rs = list(pool.map(lambda hm: cambio(op_k, "cambiar", madrid(D_CONC1, 8, 30), madrid(D_CONC1, *hm)), horas))
    cods = sorted(r.status_code for r in rs)
    # Los motivos de los 409.
    motivos_409 = {motivo(r) for r in rs if r.status_code == 409}
    # SQL: las filas de cambios y las confirmadas que quedan.
    filas_k = consultar("SELECT count(*) FROM cambios_visita WHERE oportunidad_id = %s;", (op_k,))[0][0]
    activas_k = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s AND estado = 'confirmada';", (op_k,))[0][0]
    comprobar("5 a la vez sobre la misma: un 201, cuatro 409 visita_cambiada, una fila y una confirmada",
              cods == [201, 409, 409, 409, 409] and motivos_409 == {"visita_cambiada"} and filas_k == 1 and activas_k == 1,
              f"({cods} {motivos_409} filas {filas_k} confirmadas {activas_k})")
    # 2 cambios a la MISMA hora en oportunidades distintas (08:30 y 11:00 de D_CONC2 -> 17:00).
    op_y1 = con_visita(D_CONC2, 8, 30)
    op_y2 = con_visita(D_CONC2, 11, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Las dos a la vez.
        rs = list(pool.map(lambda par: cambio(par[0], "cambiar", par[1], madrid(D_CONC2, 17, 0)),
                           ((op_y1, madrid(D_CONC2, 8, 30)), (op_y2, madrid(D_CONC2, 11, 0)))))
    cods = sorted(r.status_code for r in rs)
    # El motivo del que pierde.
    perdedor = [motivo(r) for r in rs if r.status_code == 409]
    # SQL: cuántas confirmadas quedan a las 17:00 de las dos.
    a_las_17 = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id IN (%s, %s) AND estado = 'confirmada' "
                         "AND fecha_propuesta = %s;", (op_y1, op_y2, madrid(D_CONC2, 17, 0)))[0][0]
    comprobar("2 a la misma hora en oportunidades distintas: un 201 y un 409 hueco_ocupado; una sola a las 17:00",
              cods == [201, 409] and perdedor == ["hueco_ocupado"] and a_las_17 == 1, f"({cods} {perdedor} {a_las_17})")

    # ------------------------------------------------------------------
    print("\nCASO 11 - La ficha (V10)")
    # ANTES del cambio: visita_modificable con la de las 08:30 y ningún cambio.
    vm = FICHA_ANTES.get("visita_modificable") or {}
    comprobar("antes: visita_modificable con la de las 08:30, acciones [cambiar, cancelar], claves exactas",
              set(vm) == CLAVES_MODIFICABLE and vm.get("fecha_visita") == madrid(D_CAMBIO, 8, 30).isoformat()
              and vm.get("acciones") == ["cambiar", "cancelar"] and FICHA_ANTES.get("cambios_visita") == [], f"({vm})")
    # DESPUÉS de dos cambios.
    f = ficha(op_c)
    # Sus cambios ([] si la ficha falla: FALLO contado).
    cambios_f = f.get("cambios_visita") or []
    # SQL: la accion guardada de cada fila, en el orden de la ficha.
    acciones_tabla = [a[0] for a in consultar("SELECT accion FROM cambios_visita WHERE oportunidad_id = %s "
                                              "ORDER BY created_at, id;", (op_c,))]
    comprobar("después: dos cambios con claves exactas, tipo_cambio = la accion de la tabla, fechas e informes",
              all(set(x) == CLAVES_CAMBIO for x in cambios_f) and [x.get("tipo_cambio") for x in cambios_f] == acciones_tabla
              == ["cambiar", "cambiar"]
              and [(x.get("fecha_visita_anterior"), x.get("fecha_visita_nueva")) for x in cambios_f]
              == [(madrid(D_CAMBIO, 8, 30).isoformat(), madrid(D_CAMBIO, 11, 0).isoformat()),
                  (madrid(D_CAMBIO, 11, 0).isoformat(), madrid(D_CAMBIO, 17, 0).isoformat())]
              and [x.get("informe") for x in cambios_f] == ["Informe del cambio.", "Segundo cambio."], f"({cambios_f})")
    comprobar("después: visita_modificable con la de las 17:00 e historial_cuadra true",
              (f.get("visita_modificable") or {}).get("fecha_visita") == madrid(D_CAMBIO, 17, 0).isoformat()
              and f.get("historial_cuadra") is True, f"({f.get('visita_modificable')} {f.get('historial_cuadra')})")
    # La cancelada: null, su cambio y el último evento.
    f = ficha(op_x)
    ultimo = (f.get("historial") or [{}])[-1]
    comprobar("cancelada: visita_modificable null, un cambio (cancelar, plazo), último evento "
              "visita_confirmada_cancelada en 'perdida' y historial_cuadra true (N13)",
              f.get("visita_modificable", "falta") is None
              and [(x.get("tipo_cambio"), x.get("motivo")) for x in f.get("cambios_visita") or []] == [("cancelar", "plazo")]
              and ultimo.get("evento") == "visita_confirmada_cancelada" and ultimo.get("estado") == "perdida"
              and f.get("historial_cuadra") is True, f"({f.get('historial')})")
    # La pasada y la solicitada: null.
    comprobar("visita pasada y visita solo 'solicitada': visita_modificable null",
              ficha(op_pas).get("visita_modificable", "falta") is None
              and ficha(op_sol).get("visita_modificable", "falta") is None)
    # H-D46-3: ninguna ficha de esta prueba lleva ids de visita (ni accion, prohibida).
    prohibidas = set().union(*(claves_anidadas(ficha(op)) for op in (op_c, op_x, op_k))) & {
        "visita_id", "visita_cancelada_id", "visita_anterior_id", "visita_nueva_id", "cambio_id", "accion"}
    comprobar("ninguna ficha lleva ids de visita ni la clave accion (H-D46-3)", not prohibidas, f"({prohibidas})")

    # ------------------------------------------------------------------
    print("\nCASO 12 - Ningún importe ni dirección; ningún 500 (V12)")
    comprobar(f"ninguna respuesta de /cambios-visita lleva un importe ni la dirección ({len(cuerpos)} respuestas)",
              not any("importe" in texto or "direccion_obra" in texto or "calle_numero" in texto
                      for codigo, texto in cuerpos if codigo != 422))
    comprobar("la marca de la dirección aparece 0 veces en las respuestas de /cambios-visita",
              not any(MARCA in texto for _, texto in cuerpos))
    # SQL: logs escritos desde el id base con la marca en su detalle.
    n = consultar("SELECT count(*) FROM logs WHERE id > %s AND detalle::text LIKE %s;", (FOTO["logs"][0], f"%{MARCA}%"))[0][0]
    comprobar("la marca aparece 0 veces en logs.detalle", n == 0, f"({n})")
    comprobar(f"ninguna respuesta 500 ({len(codigos)} peticiones)", 500 not in codigos)
    comprobar(f"201 <-> creado=true y 200 <-> creado=false ({len(pares)} éxitos)",
              bool(pares) and all((c == 201) == (cr is True) for c, cr in pares))
finally:
    # Se apaga el servidor (el hilo termina solo).
    servidor.should_exit = True
    hilo.join(timeout=15)
    # Los logs del manejador global de los 500 de ESTA ejecución, de las
    # rutas que pide el script; se borran por id exacto si cuadran.
    rutas = ["/cambios-visita", "/resultados-llamada", "/leads", "/calculate-estimate", "/visits"]
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
