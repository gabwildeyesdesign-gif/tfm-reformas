"""
Verificación por HTTP REAL del resumen de la solicitud (D28.1; plan
docs/Plan_Resumen_Solicitud_D28.txt, sección 7.2, CASOS 1-16, y CASO 17
"no toca nada ajeno").

Qué hace, en pocas palabras:
  1. Levanta el servidor de verdad (uvicorn.Server en un hilo de este
     proceso, SIN --reload, en el puerto 8034: nunca el 8000, que es el
     uvicorn de n8n de Gabi). Si el 8034 está ocupado, se para sin tocar
     nada.
  2. Crea sus propios leads por POST /leads, como n8n, con y sin resumen, y
     casos con Gate por POST /calculate-estimate.
  3. Prueba el contrato: el 401 antes que el 422; el 422 SOLO por el tipo;
     guardado, sin_resumen y los tres descartes (el lead se guarda SIEMPRE,
     P1 = b), con el [AVISO] sin el texto; repetición que no sobrescribe
     (P3); el resumen en la raíz de GET /gate-avisos y de la ficha; y NUNCA
     en GET /llamadas-del-dia ni en GET /leads/session.
  4. Borra SOLO lo suyo y comprueba que no ha tocado nada ajeno.

Marca propia: emails http-resumen-<8 caracteres>@example.com (dominio
reservado), tokens uuid4, y un texto aleatorio de esta ejecución (MARCA)
dentro de cada resumen, para buscarlo en las respuestas. Los textos con
"email" y "teléfono" son inventados. El valor de los secretos NUNCA se
imprime.

Las 4 preguntas (CLAUDE.md):
  (1) Escribe en clientes, leads, oportunidades, presupuestos y logs
      PROPIOS (POST /leads y POST /calculate-estimate los confirman), y
      retrasa SU presupuesto del CASO 14 con un UPDATE limitado a lo propio.
  (2) No pasa ninguna conexión a app/: el código corre en su uvicorn, con
      su pool.
  (3) Si se corta, quedan filas propias, identificadas por el patrón del
      email: la siguiente ejecución las borra al empezar (limpiar()). Una
      oportunidad propia con Gate retrasada podría salir mientras tanto en
      la lista diaria real, como en los scripts del listado.
  (4) No toca el 8000 (usa el 8034 y se para si está ocupado). Lee datos
      reales en la lista diaria, pero solo comprueba sus propios elementos
      (regla del 2026-10-12).

Recuento previsto: 62/62 (CASOS 1-16: 54; logs de 500 y limpieza: 2;
CASO 17 sobre 6 tablas: 6).
"""

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
# contextlib.redirect_stderr: capturar el [AVISO] que escribe el servidor
# (corre en este mismo proceso). io.StringIO: donde se captura.
import contextlib
import io
# json: el texto de los elementos propios de la lista diaria.
import json
# socket: comprobar que nadie escucha en el puerto.
import socket
# sys: sys.path, la salida estándar y el código de salida.
import sys
# threading: el servidor corre en un hilo de este proceso.
import threading
# time: esperar a que el servidor arranque.
import time
# uuid: marcas, emails y tokens únicos de cada ejecución.
import uuid
# timedelta: cuánto se retrasa el presupuesto propio del CASO 14.
from datetime import timedelta
# Path: rutas independientes del sistema operativo.
from pathlib import Path

# La raíz del repositorio, para que "import app..." funcione. En una copia de
# prueba en negativo, es la de la copia, y se arranca SU app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda de módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes bien en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: preparar, mirar y limpiar la base de datos.
import psycopg2
# requests: peticiones HTTP reales.
import requests
# uvicorn: el servidor.
import uvicorn

# Los valores del .env (solo en cabeceras; nunca se imprimen).
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET

# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------
# Puerto propio (nunca el 8000) y dirección base.
PUERTO = 8034
BASE = f"http://127.0.0.1:{PUERTO}"
# Cabeceras: la de n8n (POST /leads, /calculate-estimate y /leads/session)
# y la de administración (gate-avisos, ficha y lista diaria).
AUTH_WEBHOOK = {"X-Webhook-Secret": WEBHOOK_SECRET}
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# Patrón SQL (LIKE) de los clientes de ESTE script.
PATRON_EMAIL = "http-resumen-%@example.com"
# Un texto aleatorio de esta ejecución, dentro de cada resumen propio.
MARCA = f"marca{uuid.uuid4().hex[:10]}"
# Otra, distinta, para el NOMBRE del cliente: la lista diaria enseña el
# nombre, y con la misma marca el CASO 14 la encontraría sin que el resumen
# hubiera salido.
RUN = uuid.uuid4().hex[:6]
# Las claves EXACTAS de la respuesta de POST /leads, copiadas del PLAN (5.1).
CLAVES_LEADS = {"lead_id", "cliente_id", "oportunidad_id", "status", "creado", "resumen_solicitud_estado"}
# Las de GET /leads/session, copiadas de check_lead_session_http (SIN cambios
# en este bloque: el resumen NO va ahí).
CLAVES_SESION = {"existe", "lead_id", "oportunidad_id", "estado_oportunidad", "presupuesto", "reforma",
                 "plazo_orientativo"}
# Las de "reforma", la misma en sesión, gate-avisos y ficha (sin resumen).
CLAVES_REFORMA = {"tipo_reforma", "m2", "nivel_acabados", "incluye_cambios_estructurales"}
# El primer texto del [AVISO] de POST /leads (plan, 5.1).
AVISO = "[AVISO] POST /leads: resumen_solicitud no guardado en el lead"
# Un marcador para "no enviar la clave" (distinto de enviar null).
AUSENTE = object()

# ----------------------------------------------------------------------
# Contadores
# ----------------------------------------------------------------------
# Correctas, fallos y el código de CADA respuesta (para contar los 500).
ok = 0
fallos = []
codigos = []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si condicion es verdadera, [FALLO] si no; se cuenta."""
    # global: se modifica la variable ok del archivo, no una copia local.
    global ok
    # Verdadera: una correcta más.
    if condicion:
        # Se suma y se enseña.
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta y se enseña.
    else:
        # A la lista de fallos.
        fallos.append(titulo)
        print(f"    [FALLO] {titulo}  {detalle}")


# ----------------------------------------------------------------------
# Conexión propia (preparar, mirar y limpiar)
# ----------------------------------------------------------------------
# Conexión directa (no la del pool del servidor) y su cursor.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una fila; el commit cierra la transacción de lectura para
    que la siguiente consulta vea lo último que escribió el servidor."""
    # SQL: el SELECT que llega en sql (cada llamada lleva su "# SQL" encima).
    cur.execute(sql, params)
    # La primera fila (o None); el commit cierra la transacción.
    fila = cur.fetchone()
    cn.commit()
    return fila


def consultar_todas(sql, params=()):
    """SELECT de varias filas, con el mismo commit de cierre."""
    # SQL: el SELECT que llega en sql (cada llamada lleva su "# SQL" encima).
    cur.execute(sql, params)
    # Todas las filas; el commit cierra la transacción.
    filas = cur.fetchall()
    cn.commit()
    return filas


# ----------------------------------------------------------------------
# Qué es "propio": todo lo que cuelga de los clientes de prueba
# ----------------------------------------------------------------------
# Subconsultas encadenadas: clientes propios -> sus leads -> sus oportunidades.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPORTUNIDADES = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# La condición de filas PROPIAS de cada tabla que este script puede tocar.
PROPIO = {
    "clientes": f"id IN ({SQL_CLIENTES})",
    "leads": f"id IN ({SQL_LEADS})",
    "oportunidades": f"id IN ({SQL_OPORTUNIDADES})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPORTUNIDADES})",
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPORTUNIDADES})",
}
# Orden de borrado: primero lo que apunta a otras tablas.
ORDEN_BORRADO = ["logs", "presupuestos", "oportunidades", "leads", "clientes"]
# Las tablas que se fotografían para el CASO 17 (las que toca, y visitas,
# que no debe tocar nunca).
TABLAS_FOTO = ORDEN_BORRADO + ["visitas"]
# Ninguna visita es propia: la condición es "falso" (todas son ajenas).
PROPIO["visitas"] = "false"


def limpiar():
    """Borra SOLO las filas propias, en el orden de las claves foráneas."""
    # Una tabla cada vez.
    for tabla in ORDEN_BORRADO:
        # SQL: borra las filas de esa tabla que cumplen la condición de PROPIO.
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    # Un solo commit para todos los borrados.
    cn.commit()


def foto_ajena():
    """Para cada tabla: (id_base, filas ajenas con id <= id_base)."""
    # Diccionario tabla -> (id_base, ajenas).
    foto = {}
    # Una tabla cada vez.
    for tabla in TABLAS_FOTO:
        # SQL: el id más alto ahora (0 si la tabla está vacía).
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        id_base = cur.fetchone()[0]
        # SQL: cuántas filas NO propias hay con id <= id_base.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                    {"patron": PATRON_EMAIL, "base": id_base})
        foto[tabla] = (id_base, cur.fetchone()[0])
    # Se cierra la transacción de lectura.
    cn.commit()
    return foto


def recontar_ajeno(foto):
    """Vuelve a contar lo ajeno con los MISMOS id_base: (antiguas, nuevas)."""
    # Diccionario tabla -> (antiguas, nuevas).
    resultado = {}
    # Una tabla cada vez, con su id_base de la foto.
    for tabla, (id_base, _) in foto.items():
        # Los valores de los %(...)s.
        params = {"patron": PATRON_EMAIL, "base": id_base}
        # SQL: ajenas antiguas (deben ser las mismas).
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});", params)
        antiguas = cur.fetchone()[0]
        # SQL: ajenas NUEVAS (las escribió otro proceso; solo se informa).
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id > %(base)s AND NOT ({PROPIO[tabla]});", params)
        resultado[tabla] = (antiguas, cur.fetchone()[0])
    # Se cierra la transacción de lectura.
    cn.commit()
    return resultado


def resumen_en_bd(token):
    """(existe el lead, su resumen_solicitud) del lead PROPIO con ese token."""
    # SQL: el resumen del lead con ese token, solo si es propio.
    fila = consultar(f"SELECT resumen_solicitud FROM leads WHERE lead_token = %(token)s AND {PROPIO['leads']};",
                     {"token": token, "patron": PATRON_EMAIL})
    # Sin fila: el lead no existe (o no es propio).
    return (False, None) if fila is None else (True, fila[0])


def puerto_ocupado(puerto):
    """True si algo acepta conexiones en 127.0.0.1:puerto."""
    # connect_ex devuelve 0 si alguien escucha. El with cierra el socket.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Un segundo como mucho.
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def claves_anidadas(objeto):
    """Todas las claves de un JSON, en cualquier nivel."""
    # Un diccionario: sus claves y, recursivamente, las de sus valores.
    if isinstance(objeto, dict):
        # Las de este nivel y las de dentro.
        return set(objeto).union(*(claves_anidadas(v) for v in objeto.values()))
    # Una lista: las claves de cada elemento.
    if isinstance(objeto, list):
        # La unión de todas.
        return set().union(*(claves_anidadas(e) for e in objeto))
    # Un valor simple no tiene claves.
    return set()


# ----------------------------------------------------------------------
# Ayudas HTTP (todas anotan el código, para el CASO 16)
# ----------------------------------------------------------------------
def pedir(metodo, ruta, cabeceras, cuerpo=None):
    """Una petición real; se anota su código. Devuelve la respuesta."""
    # requests.request: GET o POST con las cabeceras y el cuerpo JSON pedidos.
    r = requests.request(metodo, f"{BASE}{ruta}", headers=cabeceras, json=cuerpo, timeout=60)
    # Se anota el código.
    codigos.append(r.status_code)
    return r


def cuerpo_de(r):
    """JSON de la respuesta, o {} si no es JSON (un fallo no revienta el script)."""
    # r.json() convierte el cuerpo; si no es JSON, ValueError y {}.
    try:
        return r.json()
    # No era JSON.
    except ValueError:
        return {}


def lead(resumen=AUSENTE, estructural=False, token=None, cabeceras=AUTH_WEBHOOK, capturar=False):
    """POST /leads con un lead propio. resumen=AUSENTE no envía la clave.
    Devuelve (respuesta, token, stderr capturado durante la petición)."""
    # Token nuevo (o el que se repite) y email propio.
    token = token or str(uuid.uuid4())
    email = f"http-resumen-{uuid.uuid4().hex[:8]}@example.com"
    # El cuerpo, como lo envía n8n.
    cuerpo = {"nombre": f"Resumen Prueba {RUN}", "email": email, "telefono": "6" + str(uuid.uuid4().int)[:8],
              "tipo_reforma": "bano", "m2": 5.5, "nivel_acabados": "medio",
              "incluye_cambios_estructurales": estructural, "fotos": [], "lead_token": token}
    # La clave del resumen solo si se pide (AUSENTE = el chat publicado de hoy).
    if resumen is not AUSENTE:
        cuerpo["resumen_solicitud"] = resumen
    # stderr del proceso (y del servidor, que corre aquí), capturado si se pide.
    buffer = io.StringIO()
    # redirect_stderr cambia sys.stderr mientras dura el with.
    with contextlib.redirect_stderr(buffer) if capturar else contextlib.nullcontext():
        # La petición real.
        r = pedir("POST", "/leads", cabeceras, cuerpo)
    # Respuesta, token y lo capturado.
    return r, token, buffer.getvalue()


# ======================================================================
# Antes de nada: el puerto tiene que estar LIBRE
# ======================================================================
# Si está ocupado, se para sin tocar nada (CLAUDE.md).
if puerto_ocupado(PUERTO):
    print(f"ABORTO: el puerto {PUERTO} está ocupado por otro proceso. No ejecuto nada ni mato nada.")
    sys.exit(2)

# Restos de una ejecución anterior cortada (solo lo propio) y foto de lo ajeno.
limpiar()
FOTO = foto_ajena()
# Se enseña la foto.
print("=" * 78)
print("FOTO DE LO AJENO AL EMPEZAR (tabla: id_base, filas ajenas con id <= id_base)")
print("=" * 78)
# Una línea por tabla.
for tabla, (id_base, n) in FOTO.items():
    print(f"  {tabla:14} id_base={id_base:<6} ajenas={n}")

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
    # 200: el servidor está arriba.
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    # Todavía no escucha: se reintenta en un cuarto de segundo.
    except requests.exceptions.RequestException:
        time.sleep(0.25)
# else del while: solo si pasaron los 30 s sin break.
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio_espera:.2f} s")

# try/finally: pase lo que pase, el finally apaga el servidor y limpia.
try:
    # ==================================================================
    print("\nCASO 1 - Sin cabecera: 401 antes que el 422")
    # ==================================================================
    # Cuerpo con un resumen de tipo inválido Y sin la cabecera.
    r, _, _ = lead(resumen=123, cabeceras={})
    comprobar("sin X-Webhook-Secret y con resumen_solicitud = 123 -> 401", r.status_code == 401, f"({r.status_code})")

    # ==================================================================
    print("\nCASO 2 - El 422 es SOLO por el tipo")
    # ==================================================================
    # Un número en vez de texto: error de la integración, no de contenido.
    r, token, _ = lead(resumen=123)
    comprobar("resumen_solicitud = 123 -> 422", r.status_code == 422, f"({r.status_code})")
    # El error nombra el campo.
    locs = [e.get("loc") for e in cuerpo_de(r).get("detail", [])] if r.status_code == 422 else []
    comprobar("el 422 nombra body.resumen_solicitud", ["body", "resumen_solicitud"] in locs, f"({locs})")
    # Y no se ha escrito nada.
    comprobar("con el 422 no se crea ningún lead", resumen_en_bd(token) == (False, None))

    # ==================================================================
    print("\nCASO 3 - Resumen válido: guardado, recortado solo en los extremos")
    # ==================================================================
    # Con comillas dobles y un salto de línea en medio, y espacios alrededor.
    TEXTO_3 = f'Cambiar la bañera por un plato "extraplano".\nAlicatar hasta el techo. {MARCA}'
    r3, TOKEN_3, _ = lead(resumen=f"   {TEXTO_3}  \n")
    c3 = cuerpo_de(r3)
    comprobar("201 Created", r3.status_code == 201, f"({r3.status_code})")
    # Las 6 claves exactas del contrato.
    comprobar("6 claves exactas en la respuesta", set(c3) == CLAVES_LEADS, f"({sorted(c3)})")
    comprobar("resumen_solicitud_estado = guardado", c3.get("resumen_solicitud_estado") == "guardado",
              f"({c3.get('resumen_solicitud_estado')})")
    # La respuesta no repite el texto.
    comprobar("la respuesta no repite el texto del resumen", MARCA not in r3.text)
    # En la base de datos: el texto recortado, con las comillas y el salto.
    comprobar("en leads.resumen_solicitud, el texto recortado tal cual (comillas y salto incluidos)",
              resumen_en_bd(TOKEN_3) == (True, TEXTO_3))
    OP_3 = c3.get("oportunidad_id")

    # ==================================================================
    print("\nCASO 4 - Sin la clave (el chat publicado de hoy)")
    # ==================================================================
    # El cuerpo sin la clave resumen_solicitud.
    r4, TOKEN_4, _ = lead()
    c4 = cuerpo_de(r4)
    # 201 igual: un lead sin resumen se guarda.
    comprobar("sin resumen_solicitud -> 201 (el chat publicado no se rompe)", r4.status_code == 201, f"({r4.status_code})")
    # El estado y la columna.
    comprobar("resumen_solicitud_estado = sin_resumen", c4.get("resumen_solicitud_estado") == "sin_resumen")
    comprobar("en la base de datos, NULL", resumen_en_bd(TOKEN_4) == (True, None))
    # Su oportunidad, para la ficha del CASO 12.
    OP_4 = c4.get("oportunidad_id")

    # ==================================================================
    print("\nCASO 5 - null explícito, y solo espacios")
    # ==================================================================
    # null.
    r, token, _ = lead(resumen=None)
    comprobar("null -> 201 y sin_resumen", r.status_code == 201 and cuerpo_de(r).get("resumen_solicitud_estado")
              == "sin_resumen", f"({r.status_code} {cuerpo_de(r).get('resumen_solicitud_estado')})")
    comprobar("null -> NULL en la base de datos", resumen_en_bd(token) == (True, None))
    # Solo espacios.
    r, token, _ = lead(resumen="   \n  ")
    comprobar("solo espacios -> 201, sin_resumen y NULL", r.status_code == 201
              and cuerpo_de(r).get("resumen_solicitud_estado") == "sin_resumen" and resumen_en_bd(token) == (True, None))

    # ==================================================================
    print("\nCASO 6 - Con un email: el lead se guarda, el resumen no")
    # ==================================================================
    # Un resumen con un email inventado, capturando el stderr del servidor.
    r, token, err = lead(resumen=f"Escribir a ana.prueba@example.com {MARCA}", capturar=True)
    c = cuerpo_de(r)
    # 201: el lead no se pierde por el resumen.
    comprobar("con email -> 201 (el lead NO se pierde, P1 = b)", r.status_code == 201, f"({r.status_code})")
    # El estado dice por qué no se guardó el resumen.
    comprobar("resumen_solicitud_estado = descartado_email", c.get("resumen_solicitud_estado") == "descartado_email",
              f"({c.get('resumen_solicitud_estado')})")
    # El lead está, y su resumen es NULL.
    comprobar("el lead existe con resumen NULL", resumen_en_bd(token) == (True, None))
    # El [AVISO]: con el lead_id y el motivo, sin el texto.
    comprobar("[AVISO] en stderr con el lead_id y el motivo",
              f"{AVISO} {c.get('lead_id')} (descartado_email)" in err, f"({err.strip()[:120]!r})")
    comprobar("el [AVISO] no repite el texto (ni la marca ni el email)", MARCA not in err and "ana.prueba" not in err)

    # ==================================================================
    print("\nCASO 7 - Con un teléfono: el lead se guarda, el resumen no")
    # ==================================================================
    # Un resumen con un teléfono inventado, capturando el stderr.
    r, token, err = lead(resumen=f"Llamar al 699 000 111 por la tarde {MARCA}", capturar=True)
    c = cuerpo_de(r)
    # 201 y el estado del descarte.
    comprobar("con teléfono -> 201", r.status_code == 201, f"({r.status_code})")
    comprobar("resumen_solicitud_estado = descartado_telefono",
              c.get("resumen_solicitud_estado") == "descartado_telefono", f"({c.get('resumen_solicitud_estado')})")
    # El lead está con NULL, y el [AVISO] sin el texto ni el número.
    comprobar("el lead existe con resumen NULL", resumen_en_bd(token) == (True, None))
    comprobar("[AVISO] con el motivo y sin el texto", f"{AVISO} {c.get('lead_id')} (descartado_telefono)" in err
              and MARCA not in err and "699 000 111" not in err, f"({err.strip()[:120]!r})")

    # ==================================================================
    print("\nCASO 8 - Longitud: 301 se descarta; 300 se guarda")
    # ==================================================================
    # 301 caracteres (con la marca al principio).
    texto = (MARCA + " " + "x" * 300)[:301]
    # La petición, capturando el stderr.
    r, token, err = lead(resumen=texto, capturar=True)
    c = cuerpo_de(r)
    # 201 con el estado del descarte por longitud.
    comprobar("301 caracteres -> 201 y descartado_longitud", r.status_code == 201
              and c.get("resumen_solicitud_estado") == "descartado_longitud",
              f"({r.status_code} {c.get('resumen_solicitud_estado')})")
    # NULL en la columna (el CHECK no llega a actuar) y su [AVISO].
    comprobar("301 -> el lead existe con resumen NULL", resumen_en_bd(token) == (True, None))
    comprobar("301 -> [AVISO] con descartado_longitud", f"{AVISO} {c.get('lead_id')} (descartado_longitud)" in err)
    # Exactamente 300.
    texto = (MARCA + " " + "á" * 300)[:300]
    r, token, _ = lead(resumen=texto)
    comprobar("300 caracteres -> 201 y guardado", r.status_code == 201
              and cuerpo_de(r).get("resumen_solicitud_estado") == "guardado",
              f"({r.status_code} {cuerpo_de(r).get('resumen_solicitud_estado')})")
    # SQL: la longitud guardada, medida por Postgres.
    fila = consultar(f"SELECT char_length(resumen_solicitud) FROM leads WHERE lead_token = %(t)s AND {PROPIO['leads']};",
                     {"t": token, "patron": PATRON_EMAIL})
    comprobar("300 -> char_length 300 en la base de datos", fila is not None and fila[0] == 300, f"({fila})")

    # ==================================================================
    print("\nCASO 9 - Repetición: el resumen nuevo se IGNORA (P3)")
    # ==================================================================
    # El mismo token del CASO 3, con OTRO resumen válido.
    r, _, err = lead(resumen=f"Otro resumen distinto {MARCA}", token=TOKEN_3, capturar=True)
    c = cuerpo_de(r)
    # 200: no se ha creado nada.
    comprobar("repetición -> 200 y creado = false", r.status_code == 200 and c.get("creado") is False,
              f"({r.status_code} {c.get('creado')})")
    # El estado describe lo guardado en el original, que no cambia.
    comprobar("estado = guardado (lo GUARDADO en el original)", c.get("resumen_solicitud_estado") == "guardado")
    comprobar("el texto guardado sigue siendo el ORIGINAL", resumen_en_bd(TOKEN_3) == (True, TEXTO_3))
    comprobar("la repetición no escribe ningún [AVISO]", AVISO not in err)

    # ==================================================================
    print("\nCASO 10 - Repetición de un lead SIN resumen: no se rellena")
    # ==================================================================
    r, _, _ = lead(resumen=f"Ahora sí con resumen {MARCA}", token=TOKEN_4)
    c = cuerpo_de(r)
    comprobar("repetición -> 200 y sin_resumen", r.status_code == 200 and c.get("resumen_solicitud_estado")
              == "sin_resumen", f"({r.status_code} {c.get('resumen_solicitud_estado')})")
    comprobar("sigue NULL en la base de datos", resumen_en_bd(TOKEN_4) == (True, None))

    # ==================================================================
    print("\nCASO 11 - Caso con Gate: el resumen sale en GET /gate-avisos, en la raíz")
    # ==================================================================
    # Lead con cambios estructurales (Gate) y su resumen.
    TEXTO_G = f"Tirar el tabique entre baño y pasillo para ampliar. {MARCA}"
    r, TOKEN_G, _ = lead(resumen=TEXTO_G, estructural=True)
    OP_G = cuerpo_de(r).get("oportunidad_id")
    # El cálculo (como n8n): con cambios estructurales queda con Gate.
    r = pedir("POST", "/calculate-estimate", AUTH_WEBHOOK, {"oportunidad_id": OP_G})
    comprobar("calculate-estimate -> 200 y pendiente_aprobacion", r.status_code == 200
              and cuerpo_de(r).get("status") == "pendiente_aprobacion", f"({r.status_code} {cuerpo_de(r).get('status')})")
    # El aviso del Gate.
    r = pedir("GET", f"/gate-avisos/{OP_G}", AUTH_GATE)
    aviso = cuerpo_de(r)
    # 200, con el resumen en la raíz...
    comprobar("GET /gate-avisos -> 200", r.status_code == 200, f"({r.status_code})")
    comprobar("resumen_solicitud en la raíz, igual al guardado", aviso.get("resumen_solicitud") == TEXTO_G)
    # ...y NO dentro de "reforma" (la comparte /leads/session).
    comprobar("'reforma' sigue con sus 4 claves exactas (sin resumen)",
              set(aviso.get("reforma") or {}) == CLAVES_REFORMA, f"({sorted(aviso.get('reforma') or {})})")

    # ==================================================================
    print("\nCASO 12 - La ficha: el mismo resumen; null si no hay")
    # ==================================================================
    # La ficha del caso con Gate.
    r = pedir("GET", f"/oportunidades/{OP_G}/ficha", AUTH_GATE)
    ficha = cuerpo_de(r)
    comprobar("ficha del caso con Gate -> 200 y el mismo resumen en la raíz",
              r.status_code == 200 and ficha.get("resumen_solicitud") == TEXTO_G, f"({r.status_code})")
    comprobar("ficha: 'reforma' sin resumen", set(ficha.get("reforma") or {}) == CLAVES_REFORMA)
    # La ficha de un caso SIN Gate y con resumen (CASO 3).
    r = pedir("GET", f"/oportunidades/{OP_3}/ficha", AUTH_GATE)
    comprobar("ficha del CASO 3 (sin Gate) -> su resumen", r.status_code == 200
              and cuerpo_de(r).get("resumen_solicitud") == TEXTO_3, f"({r.status_code})")
    # La ficha de un caso sin resumen (CASO 4): la clave está, con null.
    r = pedir("GET", f"/oportunidades/{OP_4}/ficha", AUTH_GATE)
    f4 = cuerpo_de(r)
    comprobar("ficha del CASO 4 -> resumen_solicitud presente y null", r.status_code == 200
              and "resumen_solicitud" in f4 and f4["resumen_solicitud"] is None, f"({r.status_code})")

    # ==================================================================
    print("\nCASO 13 - Caso con Gate SIN resumen: null en GET /gate-avisos")
    # ==================================================================
    r, _, _ = lead(estructural=True)
    OP_G0 = cuerpo_de(r).get("oportunidad_id")
    # El cálculo, con Gate.
    r = pedir("POST", "/calculate-estimate", AUTH_WEBHOOK, {"oportunidad_id": OP_G0})
    comprobar("calculate-estimate -> 200", r.status_code == 200, f"({r.status_code})")
    # El aviso: la clave está, con null.
    r = pedir("GET", f"/gate-avisos/{OP_G0}", AUTH_GATE)
    a0 = cuerpo_de(r)
    comprobar("GET /gate-avisos -> 200 con resumen_solicitud presente y null",
              r.status_code == 200 and "resumen_solicitud" in a0 and a0["resumen_solicitud"] is None,
              f"({r.status_code})")

    # ==================================================================
    print("\nCASO 14 - GET /llamadas-del-dia NO lleva el resumen (D26 1.3)")
    # ==================================================================
    # El presupuesto PROPIO del CASO 11, 25 h atrás: pasa el plazo del
    # recordatorio del Gate (24 h) y sale en gates_sin_decision.
    # SQL: UPDATE limitado a lo propio (el AND con PROPIO).
    cur.execute(f"UPDATE presupuestos SET created_at = now() - %(i)s WHERE oportunidad_id = %(op)s "
                f"AND {PROPIO['presupuestos']};", {"i": timedelta(hours=25), "op": OP_G, "patron": PATRON_EMAIL})
    cn.commit()
    # La lista.
    r = pedir("GET", "/llamadas-del-dia", AUTH_GATE)
    L = cuerpo_de(r)
    comprobar("GET /llamadas-del-dia -> 200", r.status_code == 200, f"({r.status_code})")
    # SQL: los ids de las oportunidades propias.
    mios = {f[0] for f in consultar_todas(f"SELECT id FROM oportunidades WHERE {PROPIO['oportunidades']};",
                                         {"patron": PATRON_EMAIL})}
    # Los elementos propios de los cinco apartados (solo lo nuestro).
    propios = [e for valor in L.values() if isinstance(valor, list) for e in valor
               if isinstance(e, dict) and e.get("oportunidad_id") in mios]
    comprobar("el caso con Gate del CASO 11 sale en gates_sin_decision",
              any(e.get("oportunidad_id") == OP_G for e in L.get("gates_sin_decision", [])))
    # Ninguna clave con "resumen" en ningún nivel de los elementos propios.
    comprobar("ningún elemento propio tiene una clave con 'resumen'",
              not any("resumen" in k for k in claves_anidadas(propios)),
              f"({sorted(k for k in claves_anidadas(propios) if 'resumen' in k)})")
    # Ni el texto de ningún resumen propio.
    comprobar("el texto de ningún resumen propio sale en la lista", MARCA not in json.dumps(propios, ensure_ascii=False))

    # ==================================================================
    print("\nCASO 15 - GET /leads/session NO lleva el resumen")
    # ==================================================================
    # La sesión del lead del CASO 3 (con resumen guardado).
    r = pedir("GET", f"/leads/session/{TOKEN_3}", AUTH_WEBHOOK)
    s = cuerpo_de(r)
    # 200 y el lead existe.
    comprobar("GET /leads/session -> 200 y existe", r.status_code == 200 and s.get("existe") is True,
              f"({r.status_code})")
    # Las claves de siempre, en la raíz y en "reforma".
    comprobar("claves exactas de siempre (sin resumen)", set(s) == CLAVES_SESION, f"({sorted(s)})")
    comprobar("'reforma' con sus 4 claves exactas", set(s.get("reforma") or {}) == CLAVES_REFORMA)
    # Ni clave ni texto en ningún nivel (es su propio lead: se mira todo).
    comprobar("ninguna clave con 'resumen' y ningún texto del resumen en la sesión",
              not any("resumen" in k for k in claves_anidadas(s)) and MARCA not in r.text)

    # ==================================================================
    print("\nCASO 16 - Ningún 500")
    # ==================================================================
    comprobar("ninguna respuesta 500 en todo el script", 500 not in codigos,
              f"({len(codigos)} peticiones, códigos {sorted(set(codigos))})")

finally:
    # Apagado ordenado del servidor propio (el hilo de este proceso).
    servidor.should_exit = True
    hilo.join(timeout=15)

    # Logs del manejador global (500) de las rutas que usa este script: no
    # llevan marca propia; se borran por id EXACTO solo si son tantos como
    # respuestas 500 ha recibido este script (regla de CLAUDE.md).
    n_500 = codigos.count(500)
    # SQL: los logs error_no_controlado escritos durante la prueba (id >
    # id_base de logs) en las rutas de este script.
    logs_500 = consultar_todas(
        "SELECT id FROM logs WHERE id > %s AND accion = 'error_no_controlado' "
        "AND (detalle->>'ruta' IN ('/leads', '/calculate-estimate', '/llamadas-del-dia') "
        "OR detalle->>'ruta' LIKE '/gate-avisos/%%' OR detalle->>'ruta' LIKE '/oportunidades/%%' "
        "OR detalle->>'ruta' LIKE '/leads/session/%%') ORDER BY id;", (FOTO["logs"][0],))
    print(f"\n  Respuestas 500: {n_500}; logs error_no_controlado de estas rutas: {[f[0] for f in logs_500]}")
    comprobar("los logs de 500 son exactamente los esperados", len(logs_500) == n_500,
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
    recuento = recontar_ajeno(FOTO)
    # Una tabla cada vez: las antiguas, iguales; las nuevas, solo información.
    for tabla, (id_base, antes_n) in FOTO.items():
        # Las dos cifras de esa tabla.
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
    # Título de cada comprobación fallida.
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    # Código de salida 1.
    sys.exit(1)
