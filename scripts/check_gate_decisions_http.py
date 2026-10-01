"""
Verificación por HTTP REAL de POST /gate-decisions (plan:
docs/Plan_Endpoint_Gate_Decisions.txt, sección 4.1, y encargo del Bloque D).

Qué hace, en pocas palabras:
  1. Comprueba que el servidor se NIEGA a arrancar con un GATE_SECRET vacío
     o repetido (en procesos hijos, en el puerto 8020, que nunca llega a
     abrirse).
  2. Levanta el servidor de verdad (uvicorn.Server en un hilo de este
     proceso, SIN --reload, en el puerto 8019: nunca el 8000, que es el
     uvicorn de n8n de Gabi).
  3. Crea sus propios leads con POST /leads y sus presupuestos con POST
     /calculate-estimate, como lo hará n8n. Los que llevan cambios
     estructurales quedan con Gate ('pendiente_aprobacion').
  4. Prueba el contrato entero de POST /gate-decisions: autenticación,
     forma, estado, fecha, efectos, repeticiones, decisión ya registrada,
     concurrencia y regresión cruzada con /visits y /leads/session.
  5. Borra SOLO lo suyo y comprueba que no ha tocado nada ajeno.

Marca propia de los datos: emails http-gate-<8 caracteres>@example.com
(dominio reservado), con tokens uuid4 nuevos en cada ejecución.

"NO TOCA NADA AJENO": al empezar se cuentan, en cada tabla, las filas que
NO son de los clientes de prueba y que ya existían (id <= id_base). Al
terminar se vuelven a contar con el mismo id_base: si el script hubiera
borrado algo ajeno, el recuento bajaría -> FALLO. Lo que escriba otro
proceso durante la prueba tiene id > id_base y se enseña solo como
información.

Limpieza (solo lo propio, en el orden de las claves foráneas): logs ->
decisiones_gate -> visitas -> presupuestos -> oportunidades -> leads ->
clientes. Si alguna petición a /gate-decisions respondió 500 (solo debe
pasar en la prueba en negativo sin FOR UPDATE), sus logs del manejador
global no llevan marca propia: se borran por id exacto, y solo si son
exactamente los esperados (regla de CLAUDE.md).

El valor de los secretos NUNCA se imprime: solo se usan en las cabeceras.
"""

# ----------------------------------------------------------------------
# Imports: piezas de otras bibliotecas que este script necesita
# ----------------------------------------------------------------------
# json: para convertir el detalle de un log a texto y buscar dentro.
import json
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
# uuid: para generar tokens únicos en cada ejecución.
import uuid
# ThreadPoolExecutor: lanza varias peticiones a la vez (concurrencia).
from concurrent.futures import ThreadPoolExecutor
# date/datetime/time/timedelta: fechas y horas. "time" se renombra a
# hora_del_dia para no chocar con el módulo time de arriba.
from datetime import datetime, time as hora_del_dia, timedelta
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

# psycopg2: para leer y limpiar la base de datos directamente.
import psycopg2
# requests: para hacer peticiones HTTP reales al servidor.
import requests
# uvicorn: el servidor que ejecuta la aplicación FastAPI.
import uvicorn

# Los valores del .env (app.config los carga). Se usan solo en cabeceras y
# en el entorno de los procesos hijos; nunca se imprimen.
from app.config import DATABASE_URL, GATE_SECRET, MCP_SECRET, WEBHOOK_SECRET

# ----------------------------------------------------------------------
# Constantes de la prueba
# ----------------------------------------------------------------------
# Puerto del servidor de prueba (plan, 4.1). Nunca el 8000.
PUERTO = 8019
# Puerto para las pruebas de arranque: el servidor NO debe llegar a abrirlo.
PUERTO_ARRANQUE = 8020
# Dirección base de todas las peticiones.
BASE = f"http://127.0.0.1:{PUERTO}"
# Cabecera de n8n para /leads, /calculate-estimate, /visits y /leads/session.
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
# Cabecera del formulario de administración para /gate-decisions.
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# Patrón SQL (LIKE) que identifica a los clientes de ESTE script.
PATRON_EMAIL = "http-gate-%@example.com"
# Zona horaria de las visitas.
MADRID = ZoneInfo("Europe/Madrid")
# Texto fijo de la visita acordada (decisión P2), copiado del PLAN y no del
# código, para que la prueba no dependa de lo que se está probando.
TEXTO_P2 = "Visita acordada por teléfono por administración tras la revisión del Gate."
# Las claves EXACTAS de la respuesta (plan, 1.5). Si apareciera una de más
# (por ejemplo un importe), la comparación fallaría.
CLAVES_RESPUESTA = {
    "decision_id", "oportunidad_id", "decision", "motivo",
    "estado_oportunidad", "visita_id", "fecha_propuesta", "creado",
}

# ----------------------------------------------------------------------
# Contadores y registros globales
# ----------------------------------------------------------------------
# Número de comprobaciones correctas.
ok = 0
# Lista de textos de las comprobaciones que han fallado.
fallos = []
# Todos los cuerpos JSON recibidos de /gate-decisions (para buscar importes).
cuerpos_gate = []
# (código, creado, descripción) de cada 200/201 de /gate-decisions.
pares_exito = []
# Código HTTP de CADA respuesta de /gate-decisions (para contar los 500).
codigos_gate = []


def comprobar(titulo, condicion, detalle=""):
    """Anota una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # "global ok": la función modifica la variable ok de fuera, no una copia.
    global ok
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


# ----------------------------------------------------------------------
# Conexión propia a la base de datos (para mirar y para limpiar)
# ----------------------------------------------------------------------
# Una conexión directa, aparte del pool del servidor.
cn = psycopg2.connect(DATABASE_URL)
# El cursor es el objeto con el que se envían las consultas.
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una sola fila. El commit cierra la transacción de lectura,
    para que la siguiente consulta vea lo último que haya escrito el servidor."""
    cur.execute(sql, params)
    fila = cur.fetchone()
    cn.commit()
    return fila


def ejecutar(sql, params=()):
    """Escritura directa (SOLO sobre filas propias) y commit."""
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
# Orden de borrado: primero lo que apunta a otras tablas. decisiones_gate
# va antes que visitas porque su clave foránea doble apunta a visitas.
ORDEN_BORRADO = ["logs", "decisiones_gate", "visitas", "presupuestos", "oportunidades", "leads", "clientes"]


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas, en
    una sola transacción (el commit del final)."""
    for tabla in ORDEN_BORRADO:
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    cn.commit()


def foto_ajena():
    """
    Para cada tabla: (id_base, filas ajenas con id <= id_base). id_base es
    el id más alto en este momento; lo que se cree después tendrá id mayor.
    """
    foto = {}
    for tabla in ORDEN_BORRADO:
        # COALESCE(max(id), 0): si la tabla estuviera vacía, max da NULL y
        # se usa 0.
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
def cuerpo(r):
    """JSON de la respuesta, o {} si no es JSON (así un fallo no revienta el script)."""
    try:
        return r.json()
    except ValueError:
        return {}


def motivo(r):
    """El motivo de un rechazo de negocio: {"detail": {"motivo": ...}}."""
    detalle = cuerpo(r).get("detail")
    # isinstance: el 422 de Pydantic trae una LISTA en detail, no un objeto.
    return detalle.get("motivo") if isinstance(detalle, dict) else None


def lead_http(estructural):
    """Crea un lead por POST /leads. Devuelve (lead_token, oportunidad_id).
    estructural=True es lo que activa el Gate al calcular."""
    token = str(uuid.uuid4())
    r = requests.post(
        f"{BASE}/leads",
        headers=AUTH_WEBHOOK,
        json={
            "nombre": "Prueba Gate",
            "email": f"http-gate-{token[:8]}@example.com",
            "telefono": "600000000",
            "tipo_reforma": "bano",
            "m2": 6,
            "nivel_acabados": "medio",
            "incluye_cambios_estructurales": estructural,
            "lead_token": token,
        },
        timeout=20,
    )
    # assert: si no es 201, el script se detiene aquí con el texto del
    # error (sin datos de prueba no tiene sentido seguir).
    assert r.status_code == 201, r.text
    return token, r.json()["oportunidad_id"]


def calcular(oportunidad_id):
    """POST /calculate-estimate. Devuelve el JSON (status, motivo_gate...)."""
    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH_WEBHOOK,
                      json={"oportunidad_id": oportunidad_id}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()


def lead_con_gate():
    """Lead con cambios estructurales + cálculo: queda en pendiente_aprobacion.
    Devuelve (lead_token, oportunidad_id)."""
    token, op = lead_http(estructural=True)
    calc = calcular(op)
    assert calc["status"] == "pendiente_aprobacion", calc
    return token, op


def decidir(cuerpo_peticion, cabeceras=AUTH_GATE):
    """
    POST /gate-decisions con el cuerpo tal cual (un dict). Guarda el código,
    el JSON y, si es un éxito, el par (código, creado) para las
    comprobaciones globales del final.
    """
    r = requests.post(f"{BASE}/gate-decisions", headers=cabeceras, json=cuerpo_peticion, timeout=60)
    # list.append es seguro aunque lo llamen varios hilos a la vez.
    codigos_gate.append(r.status_code)
    j = cuerpo(r)
    if j:
        cuerpos_gate.append(j)
    if r.status_code in (200, 201):
        pares_exito.append((r.status_code, j.get("creado"), str(cuerpo_peticion.get("decision"))))
    return r


def visita_acordada(op, fecha, hora, informe="Cliente de acuerdo con la visita."):
    """Cuerpo de una decisión visita_acordada. fecha es un date; hora, "HH:MM"."""
    return {"oportunidad_id": op, "decision": "visita_acordada",
            "fecha": fecha.isoformat(), "hora": hora, "informe": informe}


def descartar(op, motivo_descarte, informe="El cliente no sigue adelante."):
    """Cuerpo de una decisión descartar."""
    return {"oportunidad_id": op, "decision": "descartar", "motivo": motivo_descarte, "informe": informe}


def esperado_madrid(fecha, hora):
    """El instante que debe devolver el endpoint, calculado aquí por separado."""
    # map(int, "08:30".split(":")) convierte ["08", "30"] en 8 y 30.
    h, m = map(int, hora.split(":"))
    return datetime.combine(fecha, hora_del_dia(h, m), tzinfo=MADRID)


def claves_con_importe(objeto, ruta=""):
    """Rutas de todas las claves que contienen "importe", a cualquier nivel
    (recorre diccionarios y listas llamándose a sí misma: recursión)."""
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


# ----------------------------------------------------------------------
# Lecturas de la base de datos sobre UNA oportunidad propia
# ----------------------------------------------------------------------
def foto_op(op):
    """
    Todo lo que una decisión puede cambiar en una oportunidad, en una tupla:
    (nº de decisiones, nº de visitas, nº de logs, estado,
     fecha_ultimo_contacto, updated_at, informe guardado).
    Si dos fotos son iguales, entre una y otra no se ha escrito NADA.
    """
    return consultar(
        """
        SELECT (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM visitas WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s),
               o.estado, o.fecha_ultimo_contacto, o.updated_at,
               (SELECT informe FROM decisiones_gate WHERE oportunidad_id = %(op)s)
        FROM oportunidades o WHERE o.id = %(op)s;
        """,
        {"op": op},
    )


def logs_decision(op):
    """Los detalles (JSON) de los logs gate_decision_registrada de una oportunidad."""
    cur.execute(
        "SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
        "AND accion = 'gate_decision_registrada' ORDER BY id;",
        (op,),
    )
    # f[0]: cada fila es una tupla de una columna; se saca el valor.
    filas = [f[0] for f in cur.fetchall()]
    cn.commit()
    return filas


def verificar_201(r, op, foto_antes, informe_enviado, decision, motivo_esperado=None, fecha=None, hora=None):
    """
    Todas las comprobaciones de un 201 (plan, 4.1 "Efectos", y punto 1 del
    encargo del Bloque D). Devuelve el decision_id (o None si no hubo 201).
    """
    j = cuerpo(r)
    # 1. Código, creado, y que la respuesta habla de ESTA oportunidad.
    comprobar("201, creado=true, misma decisión y oportunidad",
              r.status_code == 201 and j.get("creado") is True and j.get("decision") == decision
              and j.get("oportunidad_id") == op, f"({r.status_code}, {r.text[:160]})")
    # 2. Las claves EXACTAS del contrato, ni una más ni una menos.
    comprobar("la respuesta tiene exactamente las 8 claves del contrato", set(j) == CLAVES_RESPUESTA,
              f"(sobran {set(j) - CLAVES_RESPUESTA}, faltan {CLAVES_RESPUESTA - set(j)})")
    # 3. Ninguna clave de importes, a ningún nivel.
    comprobar("la respuesta no tiene ninguna clave de importes", not claves_con_importe(j), f"({claves_con_importe(j)})")
    decision_id = j.get("decision_id")

    # 4. La fila de decisiones_gate. informe_enviado.strip(): el informe se
    #    guarda RECORTADO (sin espacios en los extremos).
    fila = consultar("SELECT id, decision, motivo, visita_id, informe FROM decisiones_gate WHERE oportunidad_id = %s;", (op,))
    comprobar("fila en decisiones_gate con el mismo id, decisión, motivo e informe recortado",
              fila is not None and fila[0] == decision_id and fila[1] == decision and fila[2] == motivo_esperado
              and fila[4] == informe_enviado.strip(), f"({fila[:4] if fila else None})")

    # 5. El log: exactamente uno, con decision_id, y SIN el informe (P9).
    #    Se busca el texto del informe en el JSON entero, no solo la clave.
    logs = logs_decision(op)
    texto_logs = json.dumps(logs, ensure_ascii=False)
    comprobar("1 log gate_decision_registrada con el decision_id",
              len(logs) == 1 and logs[0].get("decision_id") == decision_id, f"({logs})")
    comprobar("el log NO contiene el informe (P9): ni la clave ni el texto",
              "informe" not in texto_logs and informe_enviado.strip() not in texto_logs)

    # 6. presupuestos no se toca: el Gate es permanente y no hay operador.
    pres = consultar("SELECT requiere_aprobacion, aprobado_por FROM presupuestos WHERE oportunidad_id = %s;", (op,))
    comprobar("presupuestos: requiere_aprobacion sigue true y aprobado_por NULL",
              pres == (True, None), f"({pres})")

    # 7. fecha_ultimo_contacto: era NULL antes y ahora está escrita y es
    #    reciente (menos de 2 minutos según el reloj de la base de datos).
    fuc = consultar("SELECT fecha_ultimo_contacto, now() - fecha_ultimo_contacto < interval '2 minutes' "
                    "FROM oportunidades WHERE id = %s;", (op,))
    comprobar("fecha_ultimo_contacto: antes NULL, ahora escrita y reciente (P5)",
              foto_antes[4] is None and fuc[0] is not None and fuc[1] is True, f"(antes {foto_antes[4]}, ahora {fuc[0]})")

    # 8. Estado de la oportunidad, en la base de datos y en la respuesta.
    estado_esperado = "visita_agendada" if decision == "visita_acordada" else "perdida"
    estado = consultar("SELECT estado FROM oportunidades WHERE id = %s;", (op,))[0]
    comprobar(f"oportunidad en '{estado_esperado}' (BD y respuesta)",
              estado == estado_esperado and j.get("estado_oportunidad") == estado_esperado, f"({estado})")

    if decision == "visita_acordada":
        # 9a. La visita: de ESTA oportunidad, 'confirmada', con la fecha
        #     pedida y el texto fijo de P2; y la respuesta, con su desfase.
        esperado = esperado_madrid(fecha, hora)
        visita = consultar("SELECT id, oportunidad_id, estado, fecha_propuesta, texto_cliente FROM visitas "
                           "WHERE id = %s;", (fila[3] if fila else None,))
        comprobar("visita de ESTA oportunidad, 'confirmada', fecha pedida y texto fijo P2",
                  visita is not None and visita[0] == j.get("visita_id") and visita[1] == op
                  and visita[2] == "confirmada" and visita[3] == esperado and visita[4] == TEXTO_P2,
                  f"({visita[:4] if visita else None})")
        # datetime.fromisoformat convierte el texto "2026-10-05T08:30:00+02:00"
        # en un datetime; utcoffset() es su desfase (+02:00 o +01:00).
        recibido = datetime.fromisoformat(j["fecha_propuesta"]) if j.get("fecha_propuesta") else None
        comprobar("fecha_propuesta de la respuesta = la pedida, en Madrid con su desfase",
                  recibido == esperado and recibido.utcoffset() == esperado.utcoffset(), f"({j.get('fecha_propuesta')})")
        comprobar("el log enlaza la visita y su fecha",
                  len(logs) == 1 and logs[0].get("visita_id") == j.get("visita_id")
                  and logs[0].get("fecha_propuesta") is not None
                  and datetime.fromisoformat(logs[0]["fecha_propuesta"]) == esperado)
    else:
        # 9b. Descartar: sin visita en la respuesta, ni en la BD, ni en el log.
        n_visitas = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s;", (op,))[0]
        comprobar("descartar: visita_id y fecha_propuesta null, motivo en la respuesta, 0 visitas",
                  j.get("visita_id") is None and j.get("fecha_propuesta") is None
                  and j.get("motivo") == motivo_esperado and n_visitas == 0, f"({n_visitas} visitas)")
        comprobar("el log lleva el motivo y visita_id null",
                  len(logs) == 1 and logs[0].get("motivo") == motivo_esperado and logs[0].get("visita_id") is None)
    return decision_id


def verificar_sin_escritura(titulo, op, foto_antes):
    """Compara la foto de antes con la de ahora: si son iguales, no se ha
    escrito NADA (ni decisiones, ni visitas, ni logs, ni estado, ni
    fecha_ultimo_contacto, ni updated_at, ni el informe)."""
    foto_despues = foto_op(op)
    comprobar(f"{titulo}: no escribe nada (decisiones, visitas, logs, estado, contacto, informe)",
              foto_despues == foto_antes, f"(antes {foto_antes[:4]}, después {foto_despues[:4]})")


def verificar_200(r, op, foto_antes, decision_id):
    """Repetición: 200, creado=false, mismo decision_id, y NADA escrito."""
    j = cuerpo(r)
    comprobar("200, creado=false, mismo decision_id",
              r.status_code == 200 and j.get("creado") is False and j.get("decision_id") == decision_id,
              f"({r.status_code}, {r.text[:160]})")
    verificar_sin_escritura("repetición", op, foto_antes)
    # El caso concreto que pide el plan (P5): fecha_ultimo_contacto, con su
    # valor exacto, no cambia en una repetición.
    comprobar("fecha_ultimo_contacto SIN CAMBIOS tras el 200",
              foto_op(op)[4] == foto_antes[4], f"({foto_antes[4]})")


def verificar_409(titulo, r, op, foto_antes, motivo_esperado):
    """Rechazo 409 con su motivo, y NADA escrito."""
    comprobar(f"409 {motivo_esperado}: {titulo}", r.status_code == 409 and motivo(r) == motivo_esperado,
              f"({r.status_code}, {motivo(r)})")
    verificar_sin_escritura(f"   {titulo}", op, foto_antes)


def puerto_ocupado(puerto):
    """True si algo acepta conexiones en 127.0.0.1:puerto."""
    # "with" cierra el socket al salir del bloque, pase lo que pase.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        # connect_ex devuelve 0 si la conexión se pudo abrir.
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ----------------------------------------------------------------------
# Fechas relativas a hoy (en Madrid), para que el script no caduque
# ----------------------------------------------------------------------
HOY = datetime.now(MADRID).date()
# Días hasta el próximo lunes (weekday: lunes = 0). "or 7": si hoy es lunes,
# el de la semana que viene, para que siempre sea futuro.
LUNES = HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)
SABADO = LUNES + timedelta(days=5)
AYER = HOY - timedelta(days=1)

# ======================================================================
# Antes de nada: nuestros dos puertos tienen que estar LIBRES. Si otro
# proceso los usa, se para y se avisa (CLAUDE.md): no se mata nada.
# ======================================================================
for p in (PUERTO, PUERTO_ARRANQUE):
    if puerto_ocupado(p):
        print(f"ABORTO: el puerto {p} está ocupado por otro proceso. No ejecuto nada ni mato nada.")
        sys.exit(2)

# Restos de una ejecución anterior cortada (solo lo propio).
limpiar()
FOTO = foto_ajena()
print("=" * 78)
print("FOTO DE LO AJENO AL EMPEZAR (tabla: id_base, filas ajenas con id <= id_base)")
print("=" * 78)
for tabla, (id_base, n) in FOTO.items():
    print(f"  {tabla:16} id_base={id_base:<6} ajenas={n}")
print(f"  Fechas de prueba: hoy {HOY} ({HOY:%A}), lunes {LUNES}, sábado {SABADO}, ayer {AYER}")

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
    ("GATE_SECRET solo con espacios", "   ", "GATE_SECRET no está definido"),
    ("GATE_SECRET igual a WEBHOOK_SECRET", WEBHOOK_SECRET, "GATE_SECRET es igual a WEBHOOK_SECRET"),
    ("GATE_SECRET igual a MCP_SECRET", MCP_SECRET, "GATE_SECRET es igual a MCP_SECRET"),
]
for nombre, valor, texto_esperado in CASOS_ARRANQUE:
    print(f"  -- {nombre}")
    # dict(os.environ, CLAVE=valor): una copia del entorno con esa clave cambiada.
    entorno = dict(os.environ, GATE_SECRET=valor, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO_ARRANQUE)],
            cwd=RAIZ_REPO, env=entorno, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        salida = proc.stdout + proc.stderr
        codigo = proc.returncode
    except subprocess.TimeoutExpired:
        # Si el servidor SÍ arrancó, no termina solo: a los 60 s
        # subprocess.run mata a ESE hijo (el nuestro) y llega aquí.
        salida, codigo = "", None
    # Última línea del error: el RuntimeError con su mensaje (sin valores).
    ultima = salida.strip().splitlines()[-1] if salida.strip() else "(sin salida)"
    print(f"      código de salida {codigo}; última línea: {ultima[:150]}")
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
hilo.start()
inicio = time.time()
# Se pregunta a /health hasta que conteste, como mucho 30 s. El "else" de
# un while se ejecuta solo si el bucle termina SIN break (no contestó).
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

# try/finally: pase lo que pase dentro (incluso un error), el bloque
# finally apaga el servidor y limpia.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación (P4)")
    # Un lead con Gate y una petición VÁLIDA: si la puerta dejara pasar la
    # credencial equivocada, esta petición registraría la decisión.
    _, op_auth = lead_con_gate()
    foto_auth = foto_op(op_auth)
    valida = descartar(op_auth, "precio")
    r = decidir(valida, cabeceras={})
    comprobar("401 sin cabecera", r.status_code == 401, f"({r.status_code})")
    comprobar("   mismo 401 que /leads: detail 'No autorizado' y WWW-Authenticate: APIKey",
              cuerpo(r) == {"detail": "No autorizado"} and r.headers.get("WWW-Authenticate") == "APIKey")
    r = decidir({"basura": 1}, cabeceras={})
    comprobar("401 sin cabecera y con cuerpo inválido (la cabecera va antes)", r.status_code == 401, f"({r.status_code})")
    # Las dos pruebas de que la credencial del Agente 2 NO abre esta puerta.
    r = decidir(valida, cabeceras={"X-Webhook-Secret": WEBHOOK_SECRET})
    comprobar("401 con el valor de WEBHOOK_SECRET en X-Webhook-Secret", r.status_code == 401, f"({r.status_code})")
    r = decidir(valida, cabeceras={"X-Gate-Secret": WEBHOOK_SECRET})
    comprobar("401 con el valor de WEBHOOK_SECRET en X-Gate-Secret", r.status_code == 401, f"({r.status_code})")
    verificar_sin_escritura("los 401", op_auth, foto_auth)

    # ------------------------------------------------------------------
    print("\nCASO 2 - Forma (422 de Pydantic)")
    _, op_forma = lead_con_gate()
    foto_forma = foto_op(op_forma)
    # Cada caso: (nombre, cuerpo). Todos son válidos salvo por UNA cosa.
    base_va = visita_acordada(op_forma, LUNES, "10:00")
    base_de = descartar(op_forma, "precio")
    CASOS_FORMA = [
        ("campo de más (presupuesto_id)", {**base_va, "presupuesto_id": 1}),
        ("decision 'continuar'", {**base_va, "decision": "continuar"}),
        ("motivo 'caro'", {**base_de, "motivo": "caro"}),
        # {k: v for ... if k != "fecha"}: una copia del cuerpo SIN esa clave.
        ("visita_acordada sin fecha", {k: v for k, v in base_va.items() if k != "fecha"}),
        ("visita_acordada sin hora", {k: v for k, v in base_va.items() if k != "hora"}),
        ("visita_acordada con motivo", {**base_va, "motivo": "precio"}),
        ("descartar sin motivo", {k: v for k, v in base_de.items() if k != "motivo"}),
        ("descartar con fecha", {**base_de, "fecha": LUNES.isoformat()}),
        ("informe vacío", {**base_de, "informe": ""}),
        ("informe solo de espacios", {**base_de, "informe": "     "}),
        ("informe de 2001 caracteres", {**base_de, "informe": "x" * 2001}),
        ("hora '10:00Z'", {**base_va, "hora": "10:00Z"}),
    ]
    for nombre, peticion in CASOS_FORMA:
        r = decidir(peticion)
        # El 422 de Pydantic trae en "detail" una LISTA de errores; el de
        # negocio, un objeto con motivo. Se exige la forma de Pydantic.
        comprobar(f"422 (Pydantic) con {nombre}", r.status_code == 422 and isinstance(cuerpo(r).get("detail"), list),
                  f"({r.status_code})")
    verificar_sin_escritura("los 12 rechazos de forma", op_forma, foto_forma)

    # ------------------------------------------------------------------
    print("\nCASO 3 - Estado (404 y 409)")
    # 2147483647: el mayor entero de una columna INTEGER; no existe.
    r = decidir(descartar(2147483647, "precio"))
    comprobar("404 oportunidad_no_encontrada", r.status_code == 404 and motivo(r) == "oportunidad_no_encontrada",
              f"({r.status_code}, {motivo(r)})")

    # Lead sin calcular: no tiene presupuesto.
    _, op_sin = lead_http(estructural=False)
    foto = foto_op(op_sin)
    verificar_409("lead sin calcular", decidir(descartar(op_sin, "precio")), op_sin, foto, "sin_presupuesto")

    # Presupuesto normal (sin Gate): queda en presupuesto_enviado.
    _, op_normal = lead_http(estructural=False)
    calcular(op_normal)
    foto = foto_op(op_normal)
    verificar_409("presupuesto sin Gate", decidir(descartar(op_normal, "precio")), op_normal, foto, "sin_gate")

    # Gate con el estado forzado desde el script (fila propia), sin decisión.
    _, op_estado = lead_con_gate()
    ejecutar("UPDATE oportunidades SET estado = 'presupuesto_enviado' WHERE id = %s;", (op_estado,))
    foto = foto_op(op_estado)
    verificar_409("Gate en 'presupuesto_enviado' sin decisión previa", decidir(descartar(op_estado, "precio")),
                  op_estado, foto, "estado_no_permitido")

    # Gate con una visita activa creada a mano (fila propia), P1.
    _, op_activa = lead_con_gate()
    ejecutar("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
             "VALUES (%s, %s, 'solicitada', 'visita de prueba creada a mano');",
             (op_activa, esperado_madrid(LUNES, "17:00")))
    foto = foto_op(op_activa)
    verificar_409("visita activa ya existente (P1)", decidir(visita_acordada(op_activa, LUNES, "10:00")),
                  op_activa, foto, "visita_activa_existente")

    # ------------------------------------------------------------------
    print("\nCASO 4 - Reglas de la fecha (422 de negocio, solo visita_acordada)")
    # Se usa op_forma: sigue intacta en pendiente_aprobacion.
    for nombre, fecha, hora, esperado in [
        ("ayer a las 10:00", AYER, "10:00", "fecha_pasada"),
        ("el próximo sábado a las 10:00", SABADO, "10:00", "fin_de_semana"),
        ("08:29 (antes de la franja)", LUNES, "08:29", "fuera_de_franja"),
        ("12:45 (terminaría a las 13:45)", LUNES, "12:45", "fuera_de_franja"),
        ("19:30 (terminaría a las 20:30)", LUNES, "19:30", "fuera_de_franja"),
    ]:
        r = decidir(visita_acordada(op_forma, fecha, hora))
        # El 422 de NEGOCIO trae un objeto con motivo, no la lista de Pydantic.
        comprobar(f"422 {esperado}: {nombre}", r.status_code == 422 and motivo(r) == esperado,
                  f"({r.status_code}, {motivo(r)})")
    verificar_sin_escritura("los 5 rechazos de fecha", op_forma, foto_forma)

    # ------------------------------------------------------------------
    print("\nCASO 5 - 201 visita_acordada (lunes 08:30, límite inferior; informe con espacios)")
    token_va, op_va = lead_con_gate()
    foto_antes = foto_op(op_va)
    # Un informe reconocible (con un trozo de uuid) y con espacios en los
    # extremos, para comprobar que se guarda recortado.
    informe_va = f"  Llamada {uuid.uuid4().hex[:10]}: acepta la visita del lunes.  "
    r = decidir(visita_acordada(op_va, LUNES, "08:30", informe=informe_va))
    print(f"      <- {r.status_code} {r.text}")
    decision_va = verificar_201(r, op_va, foto_antes, informe_va, "visita_acordada", fecha=LUNES, hora="08:30")
    comprobar("el informe se guarda recortado (sin los espacios de los extremos)",
              foto_op(op_va)[6] == informe_va.strip())

    print("\n  5b - 200 repetición (misma fecha y hora, OTRO informe)")
    foto_tras_201 = foto_op(op_va)
    r = decidir(visita_acordada(op_va, LUNES, "08:30", informe="Otro informe distinto, que debe ignorarse."))
    verificar_200(r, op_va, foto_tras_201, decision_va)
    comprobar("el informe ORIGINAL sigue intacto", foto_op(op_va)[6] == informe_va.strip())

    print("\n  5c - 409 decision_ya_registrada sobre la misma oportunidad")
    # La foto se toma JUSTO ANTES de cada 409 (no se reutiliza la de antes
    # de la repetición): así, si algo escribe, falla la comprobación de
    # quien lo escribió y no la de la petición que llega después.
    foto = foto_op(op_va)
    verificar_409("visita_acordada a otra hora", decidir(visita_acordada(op_va, LUNES, "10:00")),
                  op_va, foto, "decision_ya_registrada")
    foto = foto_op(op_va)
    verificar_409("descartar", decidir(descartar(op_va, "precio")), op_va, foto, "decision_ya_registrada")

    # ------------------------------------------------------------------
    print("\nCASO 6 - 201 visita_acordada a las 19:00 (límite superior), informe de 2000 'á'")
    _, op_1900 = lead_con_gate()
    foto_antes = foto_op(op_1900)
    # 2000 'á': el límite cuenta CARACTERES, no bytes (cada 'á' son 2 bytes).
    r = decidir(visita_acordada(op_1900, LUNES, "19:00", informe="á" * 2000))
    verificar_201(r, op_1900, foto_antes, "á" * 2000, "visita_acordada", fecha=LUNES, hora="19:00")

    # ------------------------------------------------------------------
    print("\nCASO 7 - 201 descartar")
    token_de, op_de = lead_con_gate()
    foto_antes = foto_op(op_de)
    informe_de = f"Llamada {uuid.uuid4().hex[:10]}: le parece caro y no sigue."
    r = decidir(descartar(op_de, "precio", informe=informe_de))
    print(f"      <- {r.status_code} {r.text}")
    decision_de = verificar_201(r, op_de, foto_antes, informe_de, "descartar", motivo_esperado="precio")

    print("\n  7b - 200 repetición (mismo motivo, otro informe)")
    foto_tras_201 = foto_op(op_de)
    verificar_200(decidir(descartar(op_de, "precio", informe="Otro informe.")), op_de, foto_tras_201, decision_de)
    comprobar("el informe ORIGINAL sigue intacto", foto_op(op_de)[6] == informe_de)

    print("\n  7c - 409 decision_ya_registrada")
    # Foto justo antes de cada 409, por el mismo motivo que en 5c.
    foto = foto_op(op_de)
    verificar_409("descartar con otro motivo", decidir(descartar(op_de, "plazo")), op_de, foto,
                  "decision_ya_registrada")
    foto = foto_op(op_de)
    verificar_409("visita_acordada", decidir(visita_acordada(op_de, LUNES, "10:00")), op_de, foto,
                  "decision_ya_registrada")

    # ------------------------------------------------------------------
    print("\nCASO 8 - Concurrencia")
    _, op_c1 = lead_con_gate()
    misma = visita_acordada(op_c1, LUNES, "10:00")
    # ThreadPoolExecutor lanza las 5 peticiones a la vez, cada una en su
    # hilo. map() devuelve las respuestas en el orden en que se LANZARON.
    with ThreadPoolExecutor(max_workers=5) as ejecutor:
        respuestas = list(ejecutor.map(lambda _: decidir(misma), range(5)))
    pares = [(r.status_code, cuerpo(r).get("creado")) for r in respuestas]
    ids = {cuerpo(r).get("decision_id") for r in respuestas}
    print(f"      misma decisión x5 -> (código, creado) por petición: {pares}; decision_id {ids}")
    comprobar("misma decisión x5: un 201 y cuatro 200", sorted(p[0] for p in pares) == [200, 200, 200, 200, 201])
    comprobar("misma decisión x5: un solo creado=true y el mismo decision_id en las 5",
              [p[1] for p in pares].count(True) == 1 and len(ids) == 1)
    f = foto_op(op_c1)
    comprobar("misma decisión x5: 1 decisión, 1 visita y 1 log de decisión en la BD",
              f[0] == 1 and f[1] == 1 and len(logs_decision(op_c1)) == 1, f"({f[:3]})")

    _, op_c2 = lead_con_gate()
    distintas = [visita_acordada(op_c2, LUNES, "08:30"), visita_acordada(op_c2, LUNES, "09:30"),
                 visita_acordada(op_c2, LUNES, "10:30"), descartar(op_c2, "precio"), descartar(op_c2, "plazo")]
    with ThreadPoolExecutor(max_workers=5) as ejecutor:
        respuestas = list(ejecutor.map(decidir, distintas))
    resumen = [(r.status_code, motivo(r) or cuerpo(r).get("decision")) for r in respuestas]
    print(f"      decisiones distintas x5 -> {resumen}")
    codigos = sorted(r.status_code for r in respuestas)
    comprobar("distintas x5: exactamente un 201 y cuatro 409", codigos == [201, 409, 409, 409, 409])
    comprobar("distintas x5: los cuatro 409 son decision_ya_registrada",
              [motivo(r) for r in respuestas if r.status_code == 409].count("decision_ya_registrada") == 4)
    # Quién ganó: la decisión de la única respuesta 201 (si la hubo).
    ganadora = next((cuerpo(r).get("decision") for r in respuestas if r.status_code == 201), None)
    f = foto_op(op_c2)
    visitas_esperadas = 1 if ganadora == "visita_acordada" else 0
    comprobar(f"distintas x5: 1 decisión y {visitas_esperadas} visita(s) en la BD (ganó {ganadora})",
              f[0] == 1 and f[1] == visitas_esperadas and len(logs_decision(op_c2)) == 1, f"({f[:3]})")

    # ------------------------------------------------------------------
    print("\nCASO 9 - Regresión cruzada")
    for nombre, token, op, estado in [("visita acordada", token_va, op_va, "visita_agendada"),
                                       ("descartada", token_de, op_de, "perdida")]:
        # POST /visits sobre un lead con Gate ya decidido: el Gate es
        # permanente (P5 de /visits), así que sigue bloqueado.
        foto = foto_op(op)
        r = requests.post(f"{BASE}/visits", headers=AUTH_WEBHOOK, timeout=30,
                          json={"lead_token": token, "fecha": LUNES.isoformat(), "hora": "17:00",
                                "texto_cliente": "Quiero otra visita"})
        comprobar(f"POST /visits ({nombre}): 409 presupuesto_con_gate", r.status_code == 409
                  and motivo(r) == "presupuesto_con_gate", f"({r.status_code}, {motivo(r)})")
        verificar_sin_escritura(f"   POST /visits ({nombre})", op, foto)
        # GET /leads/session: lo que verá el router de n8n (P6).
        r = requests.get(f"{BASE}/leads/session/{token}", headers=AUTH_WEBHOOK, timeout=30)
        j = cuerpo(r)
        comprobar(f"GET /leads/session ({nombre}): estado '{estado}' y requiere_aprobacion true",
                  r.status_code == 200 and j.get("estado_oportunidad") == estado
                  and (j.get("presupuesto") or {}).get("requiere_aprobacion") is True,
                  f"({r.status_code}, {j.get('estado_oportunidad')}, {(j.get('presupuesto') or {}).get('requiere_aprobacion')})")

    # ------------------------------------------------------------------
    print("\nCASO 10 - Ningún importe en ninguna respuesta de POST /gate-decisions")
    con_importe = [c for c in cuerpos_gate if claves_con_importe(c)]
    comprobar(f"0 claves 'importe' en {len(cuerpos_gate)} respuestas (éxitos y errores)", not con_importe,
              f"({con_importe[:2]})")

    # ------------------------------------------------------------------
    print("\nCASO 11 - Coherencia entre el código HTTP y creado")
    incoherentes = [p for p in pares_exito if not ((p[0] == 201 and p[1] is True) or (p[0] == 200 and p[1] is False))]
    print(f"      respuestas de éxito revisadas: {len(pares_exito)} "
          f"({sum(1 for p in pares_exito if p[0] == 201)} con 201, {sum(1 for p in pares_exito if p[0] == 200)} con 200)")
    comprobar("201 <-> creado=true y 200 <-> creado=false en TODAS las respuestas de éxito",
              not incoherentes, f"(incoherentes: {incoherentes})")

finally:
    # Apagado ordenado del servidor propio (el hilo de este proceso).
    servidor.should_exit = True
    hilo.join(timeout=15)

    # Logs de errores 500 de /gate-decisions escritos DURANTE esta prueba.
    # No llevan marca propia (entity_type 'sistema', entity_id 0): se
    # buscan por id > id_base y por la ruta, y se borran por id EXACTO solo
    # si son tantos como 500 ha recibido este script.
    n_500 = codigos_gate.count(500)
    cur.execute(
        "SELECT id, detalle->>'tipo' FROM logs WHERE id > %s AND accion = 'error_no_controlado' "
        "AND detalle->>'ruta' = '/gate-decisions' ORDER BY id;",
        (FOTO["logs"][0],),
    )
    logs_500 = cur.fetchall()
    cn.commit()
    print(f"\n  Respuestas 500 de /gate-decisions: {n_500}; logs error_no_controlado de esa ruta: {logs_500}")
    comprobar("los logs de 500 de /gate-decisions son exactamente los esperados", len(logs_500) == n_500,
              f"({len(logs_500)} logs, {n_500} respuestas 500)")
    if logs_500 and len(logs_500) == n_500:
        ejecutar("DELETE FROM logs WHERE id = ANY(%s);", ([f[0] for f in logs_500],))
        print(f"  Borrados por id exacto: {[f[0] for f in logs_500]}")

    # Limpieza de lo propio y comprobación de que no queda nada.
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
