"""
Verificación por HTTP REAL de GET /huecos-libres (D32, Bloque B3, plan
docs/Plan_Horario_Visitas_D32.txt, secciones 3, 4 y 7).

Levanta el servidor de verdad (uvicorn.Server en un hilo de este proceso,
SIN --reload, en el puerto 8032: nunca el 8000, que es el uvicorn de n8n).
Los leads de prueba se crean con POST /leads y sus presupuestos con POST
/calculate-estimate; las visitas, con POST /visits, como el Agente 2.
Emails http-huecos-<8 caracteres>@example.com.

  CASO 1   401: sin cabecera, con el valor de WEBHOOK_SECRET, con otro valor,
           y antes que el 422.
  CASO 2   422 de forma: fechas mal escritas o imposibles y un parámetro de más.
  CASO 3   modo días (datos reales, solo lectura): claves exactas,
           dias_oferta_visita días, laborables desde hoy y en orden, horas
           de la rejilla y futuras, num_horas_libres, etiqueta exacta, y
           ningún laborable con horas saltado (N5, N6).
  CASO 4   modo un día: sábado (fin_de_semana), ayer (fecha_pasada), un
           laborable libre (la rejilla entera), hoy (solo horas futuras) y
           el primer día del modo días (lo mismo que dijo el modo días).
  CASO 4b  (H-D32-1, 2026-10-14) una visita 'confirmada' puesta por SQL
           ocupa su hora en los DOS modos y la siguiente de la rejilla sigue
           libre (el desplazamiento justo); una 'confirmada' antigua a las
           10:00 quita las 09:45 y las 11:00 (N4: sin mirar las 'confirmada').
  CASO 5   T1: CADA hora de CADA día del modo días pasa POST /visits (201,
           nunca 409 ni 422). Escribe visitas propias en días REALES (P10:
           la fase 2 va con n8n cerrado); se cancelan al terminar el caso.
  CASO 6   T2: al revés, en un día del bloque propio con una hora ocupada:
           POST /visits da 201 justo en las horas ofrecidas, 409 en la
           ocupada y 422 fuera de la rejilla.
  CASO 7   T3: una hora que se ocupa entre la página "Día" y la página
           "Hora" NO aparece en la segunda (N8), y el POST da 409.
  CASO 8   T4: un día del bloque lleno -> [] dia_completo; el primer día
           real del modo días, lleno -> desaparece del modo días (N6).
  CASO 9   503 configuracion_incompleta (una clave falsa EN MEMORIA, sin
           tocar reglas_negocio): solo con X-Gate-Secret.
  CASO 10  rendimiento del modo días ([INFO]) y ninguna clave fuera de las
           de los dos modelos en ninguna respuesta (N7).

"NO TOCA NADA AJENO": como check_visits_http, se cuentan las filas ajenas
antes y después con el mismo id_base. Limpieza: solo lo propio, en el orden
de las claves foráneas.

(Nota 2026-10-14, H-D32-2 de la revisión del tutor: ningún acceso por índice
a la lista de días ni a sus horas puede reventar; si el modo días viene vacío
o un día sin horas, FALLO contado con su título y el script sigue hasta el
final y la limpieza.)
"""

# sys: consola y código de salida.
import sys
# threading: el servidor en un hilo.
import threading
# time: esperar al servidor y medir tiempos.
import time
# uuid: tokens y emails únicos.
import uuid
# re: el formato "HH:MM".
import re
# Fechas.
from datetime import date, datetime, time as hora_del_dia, timedelta
# Path: rutas.
from pathlib import Path
# La zona de Madrid.
from zoneinfo import ZoneInfo

# La raíz del repositorio en sys.path (para "import app...").
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: la base de datos (solo filas propias y lecturas).
import psycopg2
# requests: las peticiones HTTP.
import requests
# uvicorn: el servidor.
import uvicorn

# La cadena de conexión y los dos secretos (nunca se imprimen).
from app.config import DATABASE_URL, GATE_SECRET, WEBHOOK_SECRET
# El módulo de las reglas de visita (CASO 9: la clave falsa en memoria).
import app.services.reglas_visita as rv

# Las fechas propias de este script.
import fechas_prueba

# Puerto propio (nunca el 8000).
PUERTO = 8032
# La dirección base.
BASE = f"http://127.0.0.1:{PUERTO}"
# La cabecera de n8n para POST /leads, /calculate-estimate y /visits.
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
# La cabecera de administración para GET /huecos-libres.
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# La marca de los clientes de prueba.
PATRON_EMAIL = "http-huecos-%@example.com"
# La zona de Madrid.
MADRID = ZoneInfo("Europe/Madrid")
# Los días de la semana, escritos aquí aparte (oráculo).
DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
# Las claves permitidas en las respuestas (los dos modelos).
CLAVES_DIAS = {"modo", "generado_en", "dias"}
# Las de cada día del modo días.
CLAVES_DIA_LISTA = {"fecha", "dia_semana", "manana", "tarde", "num_horas_libres", "etiqueta"}
# Las del modo un día.
CLAVES_UN_DIA = {"modo", "generado_en", "fecha", "dia_semana", "manana", "tarde", "horas", "sin_horas_motivo"}

# Contadores.
ok = 0
# Los fallos.
fallos = []
# Todas las respuestas JSON de GET /huecos-libres (CASO 10).
respuestas_huecos = []


def comprobar(titulo, condicion, detalle=""):
    """[OK] o [FALLO], y se cuenta."""
    # global: el contador de fuera.
    global ok
    # Correcta.
    if condicion:
        # Una más.
        ok += 1
        # Se enseña.
        print(f"    [OK]    {titulo}  {detalle}")
    # Incorrecta.
    else:
        # Para el resumen.
        fallos.append(f"{titulo} {detalle}")
        # Se enseña.
        print(f"    [FALLO] {titulo}  {detalle}")


# La conexión directa (lecturas y filas propias).
cn = psycopg2.connect(DATABASE_URL)
# Su cursor.
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una sola fila, cerrando la transacción de lectura."""
    # La consulta.
    cur.execute(sql, params)
    # La fila.
    fila = cur.fetchone()
    # Se cierra la transacción.
    cn.commit()
    # La fila.
    return fila


def ejecutar(sql, params=()):
    """Escritura directa (solo sobre filas propias)."""
    # La orden.
    cur.execute(sql, params)
    # Confirmada.
    cn.commit()


# Lo propio: todo lo que cuelga de los clientes de prueba.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
# Sus leads.
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
# Sus oportunidades.
SQL_OPORTUNIDADES = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# La condición de lo propio en cada tabla.
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
    # Tabla a tabla.
    for tabla in ORDEN_BORRADO:
        # SQL: solo lo propio.
        cur.execute(f"DELETE FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    # Confirmado.
    cn.commit()


def foto_ajena():
    """Para cada tabla: (id_base, filas ajenas con id <= id_base)."""
    # El resultado.
    foto = {}
    # Tabla a tabla.
    for tabla in ORDEN_BORRADO:
        # SQL: el id más alto.
        cur.execute(f"SELECT COALESCE(max(id), 0) FROM {tabla};")
        # El id_base.
        id_base = cur.fetchone()[0]
        # SQL: las ajenas hasta él.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});",
                    {"patron": PATRON_EMAIL, "base": id_base})
        # Se guarda.
        foto[tabla] = (id_base, cur.fetchone()[0])
    # Se cierra la lectura.
    cn.commit()
    # La foto.
    return foto


def recontar_ajeno(foto):
    """Vuelve a contar lo ajeno con los MISMOS id_base, y lo ajeno nuevo."""
    # El resultado.
    resultado = {}
    # Tabla a tabla.
    for tabla, (id_base, _) in foto.items():
        # Los parámetros.
        params = {"patron": PATRON_EMAIL, "base": id_base}
        # SQL: las ajenas antiguas.
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id <= %(base)s AND NOT ({PROPIO[tabla]});", params)
        # Cuántas.
        antiguas = cur.fetchone()[0]
        # SQL: las ajenas nuevas (de otro proceso).
        cur.execute(f"SELECT count(*) FROM {tabla} WHERE id > %(base)s AND NOT ({PROPIO[tabla]});", params)
        # Se guardan.
        resultado[tabla] = (antiguas, cur.fetchone()[0])
    # Se cierra la lectura.
    cn.commit()
    # El resultado.
    return resultado


def cuerpo(r):
    """JSON de la respuesta, o {} si no es JSON."""
    # try: puede no ser JSON.
    try:
        # El JSON.
        return r.json()
    # No lo es.
    except ValueError:
        # Vacío.
        return {}


def motivo(r):
    """El motivo de un rechazo de negocio, o None."""
    # El detalle.
    detalle = cuerpo(r).get("detail")
    # Solo si es un diccionario.
    return detalle.get("motivo") if isinstance(detalle, dict) else None


def huecos(params=None, cabeceras=None):
    """GET /huecos-libres (con X-Gate-Secret por defecto); guarda el JSON."""
    # La petición.
    r = requests.get(f"{BASE}/huecos-libres", headers=AUTH_GATE if cabeceras is None else cabeceras,
                     params=params or {}, timeout=60)
    # Los 200 se guardan para el CASO 10.
    if r.status_code == 200:
        # El JSON.
        respuestas_huecos.append(cuerpo(r))
    # La respuesta.
    return r


def lead_http():
    """Un lead sin Gate con su presupuesto. Devuelve (lead_token, oportunidad_id)."""
    # Token único.
    token = str(uuid.uuid4())
    # POST /leads.
    r = requests.post(f"{BASE}/leads", headers=AUTH, timeout=30, json={
        "nombre": "Prueba Huecos", "email": f"http-huecos-{token[:8]}@example.com", "telefono": "600000000",
        "tipo_reforma": "bano", "m2": 6, "nivel_acabados": "medio", "incluye_cambios_estructurales": False,
        "lead_token": token})
    # Sin el lead no hay caso: se para.
    assert r.status_code == 201, r.text
    # La oportunidad.
    op = r.json()["oportunidad_id"]
    # Su presupuesto (sin Gate).
    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH, json={"oportunidad_id": op}, timeout=30)
    # Sin presupuesto no hay caso: se para.
    assert r.status_code == 200, r.text
    # El token y la oportunidad.
    return token, op


def visita(token, dia, hora):
    """POST /visits como el Agente 2."""
    # La petición.
    return requests.post(f"{BASE}/visits", headers=AUTH, timeout=60, json={
        "lead_token": token, "fecha": dia.isoformat(), "hora": hora, "texto_cliente": "Me viene bien a esa hora"})


def cancelar_propias():
    """Pone 'cancelada' las visitas activas PROPIAS (para que dejen de ocupar)."""
    # SQL: solo las de oportunidades propias.
    ejecutar(f"UPDATE visitas SET estado = 'cancelada' WHERE estado IN ('solicitada', 'confirmada') "
             f"AND {PROPIO['visitas']};", {"patron": PATRON_EMAIL})


# Las reglas reales, leídas aparte, para el oráculo de la rejilla.
REGLAS = {c: int(v) for c, v in [consultar("SELECT clave, valor FROM reglas_negocio WHERE clave = %s;", (c,))
                                 for c in ("visita_manana_inicio_min", "visita_manana_fin_min",
                                           "visita_tarde_inicio_min", "visita_tarde_fin_min",
                                           "duracion_visita_min", "desplazamiento_min", "dias_oferta_visita")]}


# El oráculo: la rejilla calculada por la prueba, sin el código probado.
def rejilla_oraculo():
    """La rejilla por franja ("HH:MM"), calculada aquí aparte."""
    # Lo que ocupa una visita con el desplazamiento.
    paso = REGLAS["duracion_visita_min"] + REGLAS["desplazamiento_min"]
    # El resultado.
    resultado = {}
    # Las dos franjas.
    for nombre, ini, fin in (("manana", "visita_manana_inicio_min", "visita_manana_fin_min"),
                             ("tarde", "visita_tarde_inicio_min", "visita_tarde_fin_min")):
        # Cada inicio con la visita entera dentro.
        resultado[nombre] = [f"{m // 60:02d}:{m % 60:02d}"
                             for m in range(REGLAS[ini], REGLAS[fin] - REGLAS["duracion_visita_min"] + 1, paso)]
    # La rejilla.
    return resultado


def etiqueta_oraculo(dia, manana, tarde):
    """La etiqueta del desplegable, construida aquí aparte."""
    # El día y la fecha.
    texto = f"{DIAS_SEMANA[dia.weekday()].capitalize()} {dia:%d/%m}"
    # La mañana, si tiene horas.
    if manana:
        # Con " · ".
        texto += " — mañana: " + " · ".join(manana)
    # La tarde, si tiene horas.
    if tarde:
        # Con " · ".
        texto += " — tarde: " + " · ".join(tarde)
    # La etiqueta.
    return texto


def en_madrid(dia, hhmm):
    """Ese día a esa hora "HH:MM", en Madrid."""
    # Horas y minutos.
    h, m = map(int, hhmm.split(":"))
    # El instante.
    return datetime.combine(dia, hora_del_dia(h, m), tzinfo=MADRID)


# La rejilla real.
REJILLA = rejilla_oraculo()
# Todas sus horas, en orden.
TODAS = REJILLA["manana"] + REJILLA["tarde"]
# Hoy, en Madrid.
HOY = datetime.now(MADRID).date()
# Los días del bloque propio (para T2 y T4 y los casos de un día).
DIAS = fechas_prueba.Dias("check_huecos_libres_http", HOY)
# El lunes del bloque.
LUNES = fechas_prueba.lunes_de_prueba("check_huecos_libres_http", HOY)
# Antes de crear nada: ninguna visita activa ajena en el bloque.
fechas_prueba.exigir_libres("check_huecos_libres_http", PATRON_EMAIL,
                            [fechas_prueba.tramo_del_bloque("check_huecos_libres_http", HOY)])

# Limpieza previa y foto de lo ajeno.
limpiar()
# La foto.
FOTO = foto_ajena()
# Cabecera.
print("=" * 78)
# Lo que hay.
print(f"  rejilla real: {REJILLA}; dias_oferta_visita: {REGLAS['dias_oferta_visita']}; hoy {HOY}; lunes del bloque {LUNES}")
# Separador.
print("=" * 78)

# El servidor en un hilo.
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# El hilo.
hilo = threading.Thread(target=servidor.run, daemon=True)
# Arranca.
hilo.start()
# Se espera a /health.
inicio = time.time()
# Hasta 30 s.
while time.time() - inicio < 30:
    # try: puede no estar aún.
    try:
        # Arriba.
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            # Listo.
            break
    # Todavía no.
    except requests.exceptions.RequestException:
        # Se espera un poco.
        time.sleep(0.25)
# No arrancó.
else:
    # Se dice y se para.
    print("  El servidor no respondió en 30 s")
    # Código 1.
    sys.exit(1)
# Arriba.
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio:.2f} s")

# try/finally: el servidor se para y se limpia siempre.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación (401)")
    # Sin cabecera.
    r = huecos(cabeceras={})
    # 401.
    comprobar("sin X-Gate-Secret -> 401", r.status_code == 401, f"({r.status_code})")
    # Con el valor de WEBHOOK_SECRET (el que tiene el Agente 2).
    r = huecos(cabeceras={"X-Gate-Secret": WEBHOOK_SECRET})
    # 401.
    comprobar("con el valor de WEBHOOK_SECRET -> 401", r.status_code == 401, f"({r.status_code})")
    # Con otro valor.
    r = huecos(cabeceras={"X-Gate-Secret": "otro-valor"})
    # 401.
    comprobar("con otro valor -> 401", r.status_code == 401, f"({r.status_code})")
    # Sin cabecera y con la query mal: 401 antes que 422.
    r = huecos({"fecha": "mañana"}, cabeceras={})
    # 401.
    comprobar("sin cabecera y fecha mal escrita -> 401 (antes que el 422)", r.status_code == 401, f"({r.status_code})")

    # ------------------------------------------------------------------
    print("\nCASO 2 - Forma (422)")
    # Cada caso: (descripción, query).
    for nombre, params in (("fecha '15/10/2026'", {"fecha": "15/10/2026"}), ("fecha '2026-02-30'", {"fecha": "2026-02-30"}),
                           ("fecha 'mañana'", {"fecha": "mañana"}), ("fecha vacía", {"fecha": ""}),
                           ("parámetro de más (dias=3)", {"dias": "3"})):
        # La petición.
        r = huecos(params)
        # 422.
        comprobar(f"{nombre} -> 422", r.status_code == 422, f"({r.status_code}, {r.text[:150]})")

    # ------------------------------------------------------------------
    print("\nCASO 3 - Modo días (datos reales, solo lectura)")
    # La petición.
    r = huecos()
    # El JSON.
    j = cuerpo(r)
    # Los días.
    dias = j.get("dias") or []
    # Se enseñan las etiquetas.
    for d in dias:
        # Una por línea.
        print(f"      {d.get('etiqueta')}")
    # 200 con las claves exactas.
    comprobar("200, claves exactas y modo 'dias'", r.status_code == 200 and set(j) == CLAVES_DIAS and j.get("modo") == "dias",
              f"({r.status_code}, {sorted(j)})")
    # El instante del cálculo.
    generado = datetime.fromisoformat(j.get("generado_en", "2000-01-01T00:00:00+00:00"))
    # dias_oferta_visita días.
    comprobar(f"exactamente dias_oferta_visita ({REGLAS['dias_oferta_visita']}) días", len(dias) == REGLAS["dias_oferta_visita"],
              f"({len(dias)})")
    # Las fechas.
    fechas = [date.fromisoformat(d.get("fecha", "2000-01-01")) for d in dias]
    # Desde hoy, en orden estricto y todas laborables.
    comprobar("fechas desde hoy, en orden estricto, todas de lunes a viernes",
              all(f >= HOY and f.weekday() < 5 for f in fechas) and fechas == sorted(set(fechas)), f"({fechas})")
    # Cada día, por separado.
    malos = []
    # Uno a uno.
    for d, f in zip(dias, fechas):
        # Claves exactas.
        if set(d) != CLAVES_DIA_LISTA:
            # Se apunta.
            malos.append((f, "claves", sorted(d)))
            # Al siguiente.
            continue
        # Sus horas por franja.
        m, t = d["manana"], d["tarde"]
        # De la rejilla, en orden.
        if [h for h in REJILLA["manana"] if h in m] != m or [h for h in REJILLA["tarde"] if h in t] != t:
            # Se apunta.
            malos.append((f, "fuera de la rejilla o desordenadas", m, t))
        # Todas futuras.
        if any(en_madrid(f, h) <= generado for h in m + t):
            # Se apunta.
            malos.append((f, "hora pasada", m, t))
        # El número y al menos una.
        if d["num_horas_libres"] != len(m) + len(t) or len(m) + len(t) == 0:
            # Se apunta.
            malos.append((f, "num_horas_libres", d["num_horas_libres"]))
        # El día de la semana.
        if d["dia_semana"] != DIAS_SEMANA[f.weekday()]:
            # Se apunta.
            malos.append((f, "dia_semana", d["dia_semana"]))
        # La etiqueta exacta.
        if d["etiqueta"] != etiqueta_oraculo(f, m, t):
            # Se apunta.
            malos.append((f, "etiqueta", d["etiqueta"]))
    # Ninguno mal.
    comprobar("cada día: claves exactas, horas de la rejilla, futuras, num_horas_libres, dia_semana y etiqueta exacta",
              dias and not malos, f"({malos[:3]})")
    # Ningún laborable con horas saltado: los que no salen entre hoy y el
    # último, en modo un día, no tienen ninguna hora.
    saltados = [HOY + timedelta(days=i) for i in range(((fechas[-1] if fechas else HOY) - HOY).days)
                if (HOY + timedelta(days=i)).weekday() < 5 and (HOY + timedelta(days=i)) not in fechas]
    # Los que tienen alguna hora.
    con_horas = [s for s in saltados if cuerpo(huecos({"fecha": s.isoformat()})).get("horas")]
    # Ninguno.
    comprobar(f"ningún laborable con horas saltado ({len(saltados)} sin listar, todos sin horas)", con_horas == [],
              f"({con_horas})")

    # ------------------------------------------------------------------
    print("\nCASO 4 - Modo un día")
    # El sábado del bloque.
    SABADO = LUNES + timedelta(days=5)
    # La petición.
    j = cuerpo(huecos({"fecha": SABADO.isoformat()}))
    # Claves exactas, sin horas y fin_de_semana.
    comprobar("sábado: claves exactas, sin horas, sin_horas_motivo 'fin_de_semana', dia_semana 'sábado'",
              set(j) == CLAVES_UN_DIA and j.get("modo") == "dia" and j.get("horas") == [] and j.get("manana") == []
              and j.get("tarde") == [] and j.get("sin_horas_motivo") == "fin_de_semana" and j.get("dia_semana") == "sábado",
              f"({j})")
    # Ayer.
    j = cuerpo(huecos({"fecha": (HOY - timedelta(days=1)).isoformat()}))
    # Sin horas y fecha_pasada.
    comprobar("ayer: sin horas, 'fecha_pasada'", j.get("horas") == [] and j.get("sin_horas_motivo") == "fecha_pasada", f"({j})")
    # Un laborable del bloque, sin visitas: la rejilla entera.
    D_LIBRE = DIAS.siguiente()
    # La petición.
    j = cuerpo(huecos({"fecha": D_LIBRE.isoformat()}))
    # La rejilla entera, por franja y en horas, sin motivo.
    comprobar(f"{D_LIBRE} (sin visitas): la rejilla entera, horas = mañana + tarde, sin motivo",
              j.get("manana") == REJILLA["manana"] and j.get("tarde") == REJILLA["tarde"] and j.get("horas") == TODAS
              and j.get("sin_horas_motivo") is None and j.get("fecha") == D_LIBRE.isoformat(), f"({j})")
    # Hoy: solo las futuras (o el motivo si no queda ninguna).
    j = cuerpo(huecos({"fecha": HOY.isoformat()}))
    # El instante del cálculo.
    generado = datetime.fromisoformat(j.get("generado_en", "2000-01-01T00:00:00+00:00"))
    # Todas sus horas son futuras.
    comprobar("hoy: todas las horas devueltas son posteriores a generado_en (P4)",
              all(en_madrid(HOY, h) > generado for h in j.get("horas", [])), f"({j.get('horas')})")
    # El primer día del modo días: lo mismo en el modo un día.
    if dias:
        # El primer día (H-D32-2: con .get).
        d0 = dias[0]
        # La petición.
        j = cuerpo(huecos({"fecha": d0.get("fecha")}))
        # Las mismas horas por franja.
        comprobar(f"el primer día del modo días ({d0.get('fecha')}): las mismas horas en el modo un día",
                  (j.get("manana"), j.get("tarde")) == (d0.get("manana"), d0.get("tarde")), f"({j})")
    # Sin días no hay nada que comparar.
    else:
        # FALLO explícito.
        comprobar("el modo días devuelve al menos un día", False, "(lista vacía)")

    # ------------------------------------------------------------------
    print("\nCASO 4b - Una visita 'confirmada' ocupa su hora en los dos modos (H-D32-1, N4)")
    # Lo que ocupa una visita con el desplazamiento (oráculo, en minutos).
    PASO = REGLAS["duracion_visita_min"] + REGLAS["desplazamiento_min"]

    def a_minutos(hhmm):
        """'09:45' -> 585."""
        # Horas y minutos.
        h, m = map(int, hhmm.split(":"))
        # Total.
        return h * 60 + m

    def a_hhmm(minutos):
        """585 -> '09:45'."""
        # Con dos cifras.
        return f"{minutos // 60:02d}:{minutos % 60:02d}"

    # Un día real del modo días con una hora H cuya siguiente de la rejilla
    # (H + duración + desplazamiento, en la misma franja) también esté libre.
    eleccion = None
    # Los días, uno a uno (con .get: nada de índices, H-D32-2).
    for d in cuerpo(huecos()).get("dias") or []:
        # Las dos franjas.
        for franja in ("manana", "tarde"):
            # Sus horas libres.
            libres_f = d.get(franja) or []
            # La primera hora con su siguiente libre.
            h = next((x for x in libres_f if a_hhmm(a_minutos(x) + PASO) in libres_f), None)
            # Encontrada: se guarda y se deja de buscar.
            if h is not None and eleccion is None:
                # (día, hora, la siguiente).
                eleccion = (date.fromisoformat(d.get("fecha", "2000-01-01")), h, a_hhmm(a_minutos(h) + PASO))
    # Sin ningún día así: FALLO contado y se sigue.
    if eleccion is None:
        # Con su título.
        comprobar("4b: el modo días trae un día con dos horas seguidas libres", False, "(ninguno)")
    # Con día: la visita 'confirmada' por SQL y los dos modos.
    else:
        # El día, la hora y la siguiente.
        F4, H4, SIG4 = eleccion
        # Se enseña.
        print(f"      'confirmada' de prueba: {F4} {H4}; la siguiente de la rejilla: {SIG4}")
        # Una oportunidad propia.
        _, op_conf = lead_http()
        # SQL: su visita 'confirmada' (solo sobre una oportunidad propia; la
        # borra limpiar() en el finally).
        ejecutar("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                 "VALUES (%s, %s, 'confirmada', 'visita confirmada de prueba (H-D32-1)');", (op_conf, en_madrid(F4, H4)))
        # El modo días: ese día, por fecha.
        dia4 = next((d for d in cuerpo(huecos()).get("dias") or [] if d.get("fecha") == F4.isoformat()), {})
        # Sus horas.
        horas_dias = (dia4.get("manana") or []) + (dia4.get("tarde") or [])
        # Sin H y con la siguiente.
        comprobar(f"4b modo días: {F4} sin {H4} (la 'confirmada') y con {SIG4} (desplazamiento justo)",
                  H4 not in horas_dias and SIG4 in horas_dias, f"({horas_dias})")
        # El modo un día.
        horas_dia = cuerpo(huecos({"fecha": F4.isoformat()})).get("horas") or []
        # Igual.
        comprobar(f"4b modo un día: {F4} sin {H4} y con {SIG4}", H4 not in horas_dia and SIG4 in horas_dia,
                  f"({horas_dia})")
    # Una 'confirmada' ANTIGUA a las 10:00 (fuera de la rejilla) en un día del bloque.
    D_C4 = DIAS.siguiente()
    # Otra oportunidad propia.
    _, op_conf2 = lead_http()
    # SQL: la visita (oportunidad propia; limpiar() la borra).
    ejecutar("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
             "VALUES (%s, %s, 'confirmada', 'visita antigua confirmada de prueba (H-D32-1)');",
             (op_conf2, en_madrid(D_C4, "10:00")))
    # Lo esperado (oráculo): la rejilla sin las que chocan con las 10:00.
    esperado4 = [h for h in TODAS if abs(a_minutos(h) - 600) >= PASO]
    # El modo un día.
    horas_c4 = cuerpo(huecos({"fecha": D_C4.isoformat()})).get("horas") or []
    # Exacto.
    comprobar(f"4b modo un día: {D_C4} con una 'confirmada' antigua a las 10:00 -> {esperado4}",
              horas_c4 == esperado4, f"({horas_c4})")
    # Las visitas propias dejan de ocupar (día real).
    cancelar_propias()

    # ------------------------------------------------------------------
    print("\nCASO 5 - T1: cada hora ofrecida pasa POST /visits (días reales)")
    # Los días del modo días, otra vez (por si cambiaron).
    dias_t1 = cuerpo(huecos()).get("dias") or []
    # Los rechazos encontrados.
    rechazos = []
    # Cuántas peticiones.
    n_peticiones = 0
    # Un día cada vez, con una oportunidad propia (sus horas, por sustitución).
    for d in dias_t1:
        # El lead del día.
        token, _ = lead_http()
        # Cada hora del día.
        for h in (d.get("manana") or []) + (d.get("tarde") or []):
            # La petición.
            r = visita(token, date.fromisoformat(d.get("fecha", "2000-01-01")), h)
            # Una más.
            n_peticiones += 1
            # Tiene que ser 201.
            if r.status_code != 201:
                # Se apunta.
                rechazos.append((d.get("fecha"), h, r.status_code, motivo(r)))
    # Se enseña.
    print(f"      {n_peticiones} peticiones en {len(dias_t1)} días; rechazos: {rechazos}")
    # Ninguna rechazada.
    comprobar(f"T1: las {n_peticiones} horas ofrecidas dan 201 (ningún 409 ni 422)", n_peticiones > 0 and rechazos == [],
              f"({rechazos[:5]})")
    # Las visitas propias dejan de ocupar (días reales).
    cancelar_propias()

    # ------------------------------------------------------------------
    print("\nCASO 6 - T2: al revés, en un día del bloque propio")
    # El día.
    D_T2 = DIAS.siguiente()
    # Una oportunidad ocupa la tercera hora de la rejilla (11:00 hoy).
    OCUPADA = TODAS[2]
    # Su lead.
    token_x, _ = lead_http()
    # La ocupa.
    r = visita(token_x, D_T2, OCUPADA)
    # Sin ella no hay caso.
    comprobar(f"T2: {D_T2} {OCUPADA} ocupada (201)", r.status_code == 201, f"({r.status_code}, {motivo(r)})")
    # Lo que ofrece el modo un día.
    ofrecidas = set(cuerpo(huecos({"fecha": D_T2.isoformat()})).get("horas") or [])
    # Se enseña.
    print(f"      ofrecidas: {sorted(ofrecidas)}")
    # Cada hora de la rejilla y dos fuera de ella, con una oportunidad nueva SIN visita.
    codigos = {}
    # Una a una.
    for h in TODAS + ["10:00", "12:30"]:
        # Su lead.
        token, _ = lead_http()
        # La petición.
        r = visita(token, D_T2, h)
        # El código y el motivo.
        codigos[h] = (r.status_code, motivo(r))
    # Se enseña.
    print(f"      códigos: {codigos}")
    # Las de 201 son exactamente las ofrecidas.
    comprobar("T2: POST /visits da 201 exactamente en las horas ofrecidas",
              {h for h, (c, _) in codigos.items() if c == 201} == ofrecidas, f"({codigos})")
    # La ocupada: 409 hueco_ocupado.
    comprobar(f"T2: la ocupada ({OCUPADA}) da 409 hueco_ocupado", codigos[OCUPADA] == (409, "hueco_ocupado"),
              f"({codigos[OCUPADA]})")
    # Las de fuera de la rejilla: 422 fuera_de_rejilla.
    comprobar("T2: 10:00 y 12:30 dan 422 fuera_de_rejilla",
              codigos["10:00"] == (422, "fuera_de_rejilla") and codigos["12:30"] == (422, "fuera_de_rejilla"),
              f"({codigos['10:00']}, {codigos['12:30']})")

    # ------------------------------------------------------------------
    print("\nCASO 7 - T3: la hora se ocupa entre la página 'Día' y la página 'Hora'")
    # Página "Día": el modo días.
    dias_t3 = cuerpo(huecos()).get("dias") or []
    # H-D32-2: el primer día que tenga alguna hora (nunca un índice sobre una
    # lista que puede venir vacía).
    primero = next((d for d in dias_t3 if (d.get("manana") or []) + (d.get("tarde") or [])), None)
    # Sin ninguno: FALLO contado con su título, y el script sigue.
    if primero is None:
        # El fallo.
        comprobar("T3: el modo días trae al menos un día con horas", False, f"({dias_t3[:2]})")
    # Con día: la prueba de siempre.
    else:
        # El día.
        F = date.fromisoformat(primero.get("fecha", "2000-01-01"))
        # Su primera hora.
        H = ((primero.get("manana") or []) + (primero.get("tarde") or []))[0]
        # Se enseña.
        print(f"      página 'Día': {F} con {H} libre")
        # Otra oportunidad la coge entre las dos páginas.
        token_p1, _ = lead_http()
        # La coge.
        r = visita(token_p1, F, H)
        # 201.
        comprobar(f"T3: otro cliente coge {F} {H} (201)", r.status_code == 201, f"({r.status_code}, {motivo(r)})")
        # Página "Hora": el modo un día, recalculado.
        j = cuerpo(huecos({"fecha": F.isoformat()}))
        # La hora ya no está.
        comprobar(f"T3: la página 'Hora' ya NO ofrece {H} (N8)", H not in (j.get("horas") or []), f"({j.get('horas')})")
        # El POST con esa hora: 409.
        token_p2, _ = lead_http()
        # La petición.
        r = visita(token_p2, F, H)
        # 409 hueco_ocupado.
        comprobar(f"T3: POST con {H}: 409 hueco_ocupado", r.status_code == 409 and motivo(r) == "hueco_ocupado",
                  f"({r.status_code}, {motivo(r)})")
    # Las visitas propias dejan de ocupar (día real).
    cancelar_propias()

    # ------------------------------------------------------------------
    print("\nCASO 8 - T4: el día se llena")
    # Un día del bloque, lleno con 6 oportunidades.
    D_T4 = DIAS.siguiente()
    # Una por hora.
    codigos_t4 = []
    # Cada hora de la rejilla.
    for h in TODAS:
        # Su lead.
        token, _ = lead_http()
        # La petición.
        codigos_t4.append(visita(token, D_T4, h).status_code)
    # Todas 201.
    comprobar(f"T4: {D_T4} lleno ({len(TODAS)} visitas, todas 201)", codigos_t4 == [201] * len(TODAS), f"({codigos_t4})")
    # El modo un día.
    j = cuerpo(huecos({"fecha": D_T4.isoformat()}))
    # Sin horas y dia_completo.
    comprobar("T4: modo un día del día lleno -> horas [] y 'dia_completo'",
              j.get("horas") == [] and j.get("sin_horas_motivo") == "dia_completo", f"({j})")
    # El primer día real del modo días, lleno con sus horas libres.
    dias_t4 = cuerpo(huecos()).get("dias") or []
    # El primer día.
    # H-D32-2: el primer día con horas, o {} (sin índices).
    G = next((d for d in dias_t4 if (d.get("manana") or []) + (d.get("tarde") or [])), {})
    # Una oportunidad por hora libre.
    for h in G.get("manana", []) + G.get("tarde", []):
        # Su lead.
        token, _ = lead_http()
        # La petición (la hora está libre: 201).
        visita(token, date.fromisoformat(G.get("fecha", "2000-01-01")), h)
    # El modo días otra vez.
    despues = cuerpo(huecos()).get("dias") or []
    # El día lleno ya no está, y siguen siendo dias_oferta_visita.
    comprobar(f"T4: el día real lleno ({G.get('fecha')}) desaparece del modo días, que sigue con "
              f"{REGLAS['dias_oferta_visita']} días",
              bool(G) and G.get("fecha") not in [d.get("fecha") for d in despues]
              and len(despues) == REGLAS["dias_oferta_visita"], f"({[d.get('fecha') for d in despues]})")
    # Las visitas propias dejan de ocupar (día real).
    cancelar_propias()

    # ------------------------------------------------------------------
    print("\nCASO 9 - 503 configuracion_incompleta (clave falsa EN MEMORIA)")
    # Las claves reales.
    claves_reales = rv.CLAVES_REGLAS
    # try/finally: las reales vuelven siempre.
    try:
        # Una clave que no existe, añadida en memoria (el servidor corre en este proceso).
        rv.CLAVES_REGLAS = claves_reales + ("clave_que_no_existe_d32",)
        # Los dos modos.
        for nombre, params in (("días", None), ("un día", {"fecha": LUNES.isoformat()})):
            # La petición.
            r = huecos(params)
            # El detalle.
            d = cuerpo(r).get("detail") or {}
            # 503 con la clave nombrada.
            comprobar(f"modo {nombre}: 503 configuracion_incompleta con 'clave_que_no_existe_d32: no existe'",
                      r.status_code == 503 and d.get("motivo") == "configuracion_incompleta"
                      and "clave_que_no_existe_d32: no existe" in (d.get("faltan") or []), f"({r.status_code}, {d})")
        # Y sin cabecera, 401 (la llave va antes que la configuración).
        comprobar("503 solo con la llave: sin cabecera, 401", huecos(cabeceras={}).status_code == 401)
    # Siempre: las reales.
    finally:
        # Restauradas.
        rv.CLAVES_REGLAS = claves_reales

    # ------------------------------------------------------------------
    print("\nCASO 10 - Rendimiento y ninguna clave de más")
    # Cinco peticiones del modo días, medidas.
    tiempos = []
    # Cinco veces.
    for _ in range(5):
        # El reloj.
        t0 = time.perf_counter()
        # La petición.
        huecos()
        # Milisegundos.
        tiempos.append((time.perf_counter() - t0) * 1000)
    # [INFO]: no se exige un tiempo.
    print(f"  [INFO]  modo días: {', '.join(f'{t:.0f}' for t in tiempos)} ms (media {sum(tiempos) / len(tiempos):.0f} ms)")

    def claves(objeto):
        """Todas las claves de un JSON, a cualquier nivel."""
        # Diccionario: sus claves y las de dentro.
        if isinstance(objeto, dict):
            # Las suyas y las de sus valores.
            return set(objeto) | set().union(*(claves(v) for v in objeto.values()))
        # Lista: las de cada elemento.
        if isinstance(objeto, list):
            # Unión.
            return set().union(*(claves(v) for v in objeto))
        # Otra cosa: ninguna.
        return set()

    # Todas las claves de todas las respuestas 200.
    todas = set().union(*(claves(j) for j in respuestas_huecos))
    # Solo las de los dos modelos.
    comprobar(f"ninguna clave fuera de los dos modelos en {len(respuestas_huecos)} respuestas (N7)",
              todas <= CLAVES_DIAS | CLAVES_DIA_LISTA | CLAVES_UN_DIA, f"({sorted(todas - CLAVES_DIAS - CLAVES_DIA_LISTA - CLAVES_UN_DIA)})")
    # Toda hora en manana, tarde u horas es "HH:MM".
    formato = all(re.fullmatch(r"\d{2}:\d{2}", h) for j in respuestas_huecos
                  for lista in ([j.get("manana", []), j.get("tarde", []), j.get("horas", [])]
                                + [[*d.get("manana", []), *d.get("tarde", [])] for d in j.get("dias", [])])
                  for h in lista)
    # Todas.
    comprobar("todas las horas devueltas tienen la forma HH:MM (ningún otro dato)", formato)

# Siempre: servidor parado, limpieza y comprobación de lo ajeno.
finally:
    # Se para el servidor.
    servidor.should_exit = True
    # Se espera al hilo.
    hilo.join(timeout=15)
    # Limpieza de lo propio.
    limpiar()
    # SQL: clientes de prueba restantes.
    restos = consultar(f"SELECT count(*) FROM clientes WHERE {PROPIO['clientes']};", {"patron": PATRON_EMAIL})[0]
    # Se enseña.
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")
    # Ninguno.
    comprobar("no queda ningún dato de prueba", restos == 0)
    # Lo ajeno, con el mismo id_base.
    print("\nNO TOCA NADA AJENO (mismo id_base que al empezar)")
    # El recuento.
    despues = recontar_ajeno(FOTO)
    # Tabla a tabla.
    for tabla, (id_base, antes) in FOTO.items():
        # Antiguas y nuevas.
        antiguas, nuevas = despues[tabla]
        # Las antiguas no cambian.
        comprobar(f"{tabla}: ajenas con id <= {id_base}: {antes} -> {antiguas}", antiguas == antes,
                  f"(ajenas NUEVAS durante la prueba, solo información: {nuevas})")
    # Se cierra la conexión.
    cn.close()

# Resumen.
total = ok + len(fallos)
# Separador.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{total} correctas")
# Separador.
print("=" * 78)
# Con fallos, se listan y código 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
