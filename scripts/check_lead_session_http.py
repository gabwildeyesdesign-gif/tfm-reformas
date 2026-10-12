"""
Verificación por HTTP REAL de GET /leads/session/{lead_token}.

Levanta el servidor de verdad (uvicorn.Server en un hilo, SIN --reload,
por la regla crítica de CLAUDE.md) y le manda peticiones HTTP reales con
requests. Los leads de prueba se crean con POST /leads y sus presupuestos
con POST /calculate-estimate, por HTTP: el mismo recorrido que hará n8n.

CASOS:
  1. Sin cabecera X-Webhook-Secret -> 401.
  2. Token de 101 caracteres SIN cabecera -> 401 (la autenticación va
     antes que la validación de la ruta, igual que en POST /leads).
  3. Token inexistente -> 200 con existe=false, todo null, presupuesto
     {existe: false, requiere_aprobacion: null, motivo_gate: null}, y
     reforma y plazo_orientativo null.
  4. Lead sin presupuesto -> existe=true, ids correctos, estado 'nueva',
     presupuesto.existe=false; reforma exacta; plazo null (sin presupuesto).
  5. Lead con presupuesto normal (baño, sin cambios) -> presupuesto {true,
     false, null}; plazo de baño = el de reglas_negocio, sin fabricación.
  6. Lead con Gate (cambios estructurales) -> {true, true,
     'cambios_estructurales'}, estado 'pendiente_aprobacion'; plazo null.
  7. Token de 101 caracteres CON cabecera -> 422 que señala lead_token.
     Token de 100 caracteres -> 200 (el límite exacto es válido).
  8. Lead con DOS oportunidades -> devuelve la primera, y el servidor
     deja un [AVISO] en stderr.
  10. Cocina sin cambios ni Gate -> obra Y fabricación de reglas_negocio.
  11. Reforma integral sin Gate -> plazo null (tipo sin plazo, D18.7).
  12. Baño con Gate por IMPORTE, sin cambios -> plazo null (condición del
      Gate, N2 del plan).
  13. Baño sin Gate con cambios estructurales = true (dato puesto A MANO en
      la fila propia: con el flujo real, los cambios siempre dan Gate) ->
      plazo null (condición de los cambios, N2 del plan). Y con la clave
      borrada (null) -> plazo null también (D4: no se adivina).
  14. Regla del plazo rota EN MEMORIA (el servidor corre en este proceso) ->
      503 configuracion_incompleta que la nombra, [AVISO] en stderr, ningún
      log; y los casos que no necesitan plazo siguen dando 200.
  15. Claves EXACTAS de todas las respuestas 200 (nivel superior, reforma,
      presupuesto y plazo), sin una de más ni de menos.
  9. (Al final, sobre TODAS las respuestas 200.) Ninguna clave, a ningún
     nivel de anidamiento, contiene "importe". Y, para que esa ausencia
     signifique algo, se comprueba en la base de datos que esos
     presupuestos SÍ tienen importes (N1 del plan del Agente 2).

Tokens: uuid4 en cada ejecución, ninguno fijo (un token fijo compartido
entre scripts es una fragilidad ya anotada en CLAUDE.md).
Datos de prueba: emails http-sesion-*@example.com, borrados al empezar y
al terminar (regla de borrados de CLAUDE.md: solo lo propio).
"""

# contextlib.redirect_stderr: captura lo que el servidor escribe en stderr.
import contextlib
# io.StringIO: un "archivo" en memoria donde cae esa captura.
import io
# sys: sys.path, la consola y el código de salida.
import sys
# threading: el servidor corre en un hilo de este mismo proceso.
import threading
# time: para esperar a que el servidor arranque.
import time
# uuid: tokens únicos en cada ejecución.
import uuid
# Decimal: para comparar m2 como número exacto.
from decimal import Decimal
# Path: rutas de archivos independientes del sistema operativo.
from pathlib import Path

# El paquete "app" está en la raíz del repositorio, no en scripts/: se
# añade la raíz a sys.path (misma técnica que los demás check_*.py).
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así salen las tildes.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: hablar con PostgreSQL. requests: peticiones HTTP. uvicorn: el
# servidor.
import psycopg2
import requests
import uvicorn

# La dirección de la base de datos y el secreto del webhook (nunca se
# imprimen).
from app.config import DATABASE_URL, WEBHOOK_SECRET
# El módulo del plazo: el caso 14 cambia una de sus claves EN MEMORIA.
from app.services import plazo_orientativo

# Puerto propio, distinto de los demás scripts HTTP (8010, 8011, 8012,
# 8015), para que no choquen si alguno se queda colgado. Nunca el 8000.
PUERTO = 8016
# La dirección base de las peticiones.
BASE = f"http://127.0.0.1:{PUERTO}"
# La cabecera del webhook, que exige el endpoint.
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
# La marca de los datos propios: emails del dominio reservado example.com.
PATRON_EMAIL = "http-sesion-%@example.com"

# Las claves EXACTAS de la respuesta (caso 15): el nivel superior...
CLAVES_SESION = {"existe", "lead_id", "oportunidad_id", "estado_oportunidad",
                 "presupuesto", "reforma", "plazo_orientativo"}
# ... el presupuesto y la reforma...
CLAVES_PRESUPUESTO = {"existe", "requiere_aprobacion", "motivo_gate"}
CLAVES_REFORMA = {"tipo_reforma", "m2", "nivel_acabados", "incluye_cambios_estructurales"}
# ... y el plazo con sus tramos.
CLAVES_PLAZO = {"obra_semanas", "fabricacion_muebles_semanas"}
CLAVES_TRAMO = {"min", "max"}

# Contadores: comprobaciones correctas y títulos de las que fallan.
ok = 0
fallos = []

# Todas las respuestas 200 del endpoint, para los casos 15 y 9.
respuestas_200 = []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global: se modifica la variable ok del archivo, no una copia local.
    global ok
    # Verdadera: se suma una correcta.
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta en la lista de fallos.
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


# Conexión directa para preparar datos y limpiar (solo filas propias).
cn = psycopg2.connect(DATABASE_URL)
# El cursor de esa conexión.
cur = cn.cursor()


def limpiar():
    """Borra los datos de prueba, en orden inverso a las claves foráneas."""
    # SQL: los ids de las oportunidades propias (por el email de prueba).
    cur.execute(
        """SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id
           JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %s;""",
        (PATRON_EMAIL,),
    )
    # La lista de ids.
    ids = [f[0] for f in cur.fetchall()]
    # Sus logs, presupuestos y oportunidades, por id exacto.
    cur.execute("DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM presupuestos WHERE oportunidad_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM oportunidades WHERE id = ANY(%s);", (ids,))
    # Sus leads, por el email de prueba de su cliente...
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %s);",
        (PATRON_EMAIL,),
    )
    # ... y los clientes.
    cur.execute("DELETE FROM clientes WHERE email LIKE %s;", (PATRON_EMAIL,))
    # Definitivo.
    cn.commit()


def lead_http(nivel, estructural, tipo="bano", m2=6):
    """Crea un lead por POST /leads con un token uuid4 nuevo.

    Devuelve (lead_token, respuesta JSON de POST /leads)."""
    # Token único de esta ejecución.
    token = str(uuid.uuid4())
    # POST /leads con los datos mínimos de una solicitud.
    r = requests.post(
        f"{BASE}/leads",
        headers=AUTH,
        json={
            "nombre": "Prueba Sesión",
            # Los 8 primeros caracteres del token bastan para un email único.
            "email": f"http-sesion-{token[:8]}@example.com",
            "telefono": "600000000",
            "tipo_reforma": tipo,
            "m2": m2,
            "nivel_acabados": nivel,
            "incluye_cambios_estructurales": estructural,
            "lead_token": token,
        },
        timeout=20,
    )
    # Si el alta no es un 201, la preparación ha fallado: se para aquí.
    assert r.status_code == 201, r.text
    # El token y la respuesta.
    return token, r.json()


def calcular(oportunidad_id):
    """POST /calculate-estimate; devuelve el JSON de la respuesta."""
    # El cálculo por HTTP, como lo haría el agente por MCP.
    r = requests.post(
        f"{BASE}/calculate-estimate", headers=AUTH, json={"oportunidad_id": oportunidad_id}, timeout=20
    )
    # Si no es 200, la preparación ha fallado.
    assert r.status_code == 200, r.text
    # El JSON del presupuesto.
    return r.json()


def sesion(token, cabeceras=AUTH):
    """GET /leads/session/{token}. Guarda las respuestas 200 para los casos 15 y 9."""
    # La petición.
    r = requests.get(f"{BASE}/leads/session/{token}", headers=cabeceras, timeout=20)
    # Las 200 se guardan con el principio del token, para identificarlas.
    if r.status_code == 200:
        respuestas_200.append((token[:12], r.json()))
    # La respuesta entera, para que cada caso mire lo que necesite.
    return r


def cuerpo(r):
    """
    JSON de la respuesta si fue 200; si no, un diccionario vacío.

    Así, si el servidor devuelve un error (un 500, por ejemplo), las
    comprobaciones de abajo salen como [FALLO] en vez de reventar el
    script con un KeyError al buscar un campo que no existe.
    """
    # Solo las 200 tienen el JSON del contrato.
    return r.json() if r.status_code == 200 else {}


def pedir(token):
    """GET /leads/session, enseña la respuesta y devuelve (r, JSON o {})."""
    # La petición (se guarda si es 200).
    r = sesion(token)
    # Se enseña tal cual llega.
    print(f"      <- {r.status_code} {r.text}")
    # La respuesta y su cuerpo.
    return r, cuerpo(r)


def claves_con_importe(objeto, ruta=""):
    """
    Recorre el JSON entero, incluidos los objetos anidados, y devuelve las
    rutas de todas las claves cuyo nombre contiene "importe".

    Es una función RECURSIVA: cuando encuentra un diccionario dentro de
    otro, se llama a sí misma para mirar también dentro. Así una clave
    escondida en presupuesto.importe_min_con_iva no se escapa.
    """
    # Las rutas encontradas.
    encontradas = []
    # Un diccionario: se mira cada clave y se baja a su valor.
    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            # La ruta de esta clave ("presupuesto.existe").
            ruta_clave = f"{ruta}.{clave}" if ruta else clave
            # Si el nombre contiene "importe", se anota.
            if "importe" in clave.lower():
                encontradas.append(ruta_clave)
            # Y se mira dentro de su valor.
            encontradas.extend(claves_con_importe(valor, ruta_clave))
    # Una lista: se mira cada elemento.
    elif isinstance(objeto, list):
        for i, valor in enumerate(objeto):
            encontradas.extend(claves_con_importe(valor, f"{ruta}[{i}]"))
    # Todas las encontradas.
    return encontradas


def plazo_esperado(tipo):
    """
    El plazo que DEBE salir para ese tipo, construido con los valores que
    hay AHORA en reglas_negocio (no escritos aquí: Gabi puede cambiarlos).
    """
    # Las parejas del tipo, del mismo diccionario que usa el servidor.
    tramos = plazo_orientativo.CLAVES_PLAZO[tipo]
    # Todas sus claves.
    claves = [c for pareja in tramos.values() for c in pareja]
    # SQL: sus valores actuales (datos de configuración).
    cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);", (claves,))
    # clave -> entero.
    valores = {c: int(v) for c, v in cur.fetchall()}
    # Se cierra la transacción de lectura.
    cn.commit()
    # La forma exacta del contrato; fabricación solo si el tipo la tiene.
    return {
        "obra_semanas": {"min": valores[tramos["obra"][0]], "max": valores[tramos["obra"][1]]},
        "fabricacion_muebles_semanas": (
            {"min": valores[tramos["fabricacion"][0]], "max": valores[tramos["fabricacion"][1]]}
            if "fabricacion" in tramos else None
        ),
    }


# Las diferencias de claves de una respuesta (caso 15).
def problemas_de_claves(respuesta):
    """Lista de diferencias entre las claves de una respuesta 200 y las del contrato."""
    # Lista de problemas de esta respuesta.
    problemas = []
    # Nivel superior.
    if set(respuesta) != CLAVES_SESION:
        problemas.append(f"nivel superior {sorted(set(respuesta) ^ CLAVES_SESION)}")
    # presupuesto, siempre objeto.
    if set(respuesta.get("presupuesto") or {}) != CLAVES_PRESUPUESTO:
        problemas.append("presupuesto")
    # reforma: null solo sin lead; si no, sus cuatro claves.
    reforma = respuesta.get("reforma")
    # Con objeto, sus claves exactas.
    if reforma is not None and set(reforma) != CLAVES_REFORMA:
        problemas.append(f"reforma {sorted(set(reforma) ^ CLAVES_REFORMA)}")
    # null con lead sería un error.
    if reforma is None and respuesta.get("existe"):
        problemas.append("reforma null con lead")
    # plazo: null, o sus dos claves con tramos {min, max}.
    plazo = respuesta.get("plazo_orientativo")
    # Con objeto, sus claves exactas...
    if plazo is not None:
        if set(plazo) != CLAVES_PLAZO:
            problemas.append(f"plazo {sorted(set(plazo) ^ CLAVES_PLAZO)}")
        # ... y cada tramo presente con las suyas.
        for tramo in (plazo.get("obra_semanas"), plazo.get("fabricacion_muebles_semanas")):
            if tramo is not None and set(tramo) != CLAVES_TRAMO:
                problemas.append(f"tramo {sorted(tramo)}")
    # Todos los problemas (vacía si todo cuadra).
    return problemas


# El presupuesto de un lead sin presupuesto (casos 3 y 4).
PRESUPUESTO_VACIO = {"existe": False, "requiere_aprobacion": None, "motivo_gate": None}

# Se empieza sin restos de ejecuciones anteriores.
limpiar()

# El servidor, sin --reload, en el puerto propio.
servidor = uvicorn.Server(
    uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning")
)
# daemon=True: el hilo no impide que el script termine.
hilo = threading.Thread(target=servidor.run, daemon=True)
# Cabecera de la salida.
print("=" * 78)
print("ARRANQUE DEL SERVIDOR (uvicorn.Server, sin --reload)")
print("=" * 78)
# Arranca.
hilo.start()
# Espera hasta 30 s a que /health responda.
inicio = time.time()
while time.time() - inicio < 30:
    # Mientras no responde, requests lanza un error de conexión.
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    # Todavía no escucha: se espera un poco.
    except requests.exceptions.RequestException:
        time.sleep(0.25)
# El else de un while se ejecuta si el bucle NO terminó con break.
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
# Arriba.
print(f"  Servidor arriba en {time.time() - inicio:.2f} s")

# try / finally: pase lo que pase, se para el servidor y se limpia.
try:
    # Tokens de esta ejecución para los casos de forma.
    token_inexistente = str(uuid.uuid4())
    token_101 = "x" * 101
    token_100 = "y" * 100

    # ---- CASO 1: la llave del webhook ----
    print("\nCASO 1 - Sin cabecera X-Webhook-Secret")
    # Sin cabeceras: 401.
    r = sesion(token_inexistente, cabeceras={})
    comprobar("401 sin cabecera", r.status_code == 401, f"(HTTP {r.status_code})")

    print("\nCASO 2 - Token de 101 caracteres SIN cabecera")
    # Token demasiado largo y sin cabecera: gana el 401.
    r = sesion(token_101, cabeceras={})
    comprobar("401, no 422: la autenticación va antes", r.status_code == 401, f"(HTTP {r.status_code})")

    print("\nCASO 3 - Token inexistente")
    # Un token que no existe: 200 con todo a null.
    r, j = pedir(token_inexistente)
    # El JSON exacto que tiene que salir.
    esperado = {
        "existe": False,
        "lead_id": None,
        "oportunidad_id": None,
        "estado_oportunidad": None,
        "presupuesto": PRESUPUESTO_VACIO,
        "reforma": None,
        "plazo_orientativo": None,
    }
    # El código y el JSON completo.
    comprobar("200", r.status_code == 200)
    comprobar("JSON exacto: existe=false, todo null, presupuesto vacío, reforma y plazo null", j == esperado)

    print("\nCASO 4 - Lead sin presupuesto")
    # Un lead de baño, sin calcular.
    token_sin, alta_sin = lead_http("medio", False)
    r, j = pedir(token_sin)
    # Existe, con sus ids.
    comprobar("200 y existe=true", r.status_code == 200 and j.get("existe") is True)
    comprobar(
        "lead_id y oportunidad_id = los de POST /leads",
        (j.get("lead_id"), j.get("oportunidad_id")) == (alta_sin["lead_id"], alta_sin["oportunidad_id"]),
    )
    # Estado inicial y presupuesto vacío.
    comprobar("estado_oportunidad = 'nueva'", j.get("estado_oportunidad") == "nueva")
    comprobar(
        "presupuesto = {false, null, null}: null y no false en requiere_aprobacion",
        j.get("presupuesto") == PRESUPUESTO_VACIO,
    )
    # La reforma tal como se envió. m2 sale como el texto exacto guardado
    # en la solicitud ("6.0" para un 6 enviado): se compara como número
    # (Decimal), y el resto de claves, exactas.
    reforma = j.get("reforma") or {}
    comprobar(
        "reforma = lo enviado a POST /leads",
        {k: v for k, v in reforma.items() if k != "m2"}
        == {"tipo_reforma": "bano", "nivel_acabados": "medio", "incluye_cambios_estructurales": False}
        and reforma.get("m2") is not None and Decimal(reforma["m2"]) == Decimal("6"),
        f"({j.get('reforma')})",
    )
    # Sin presupuesto, sin plazo.
    comprobar("plazo_orientativo = null (sin presupuesto)", j.get("plazo_orientativo") is None)

    print("\nCASO 5 - Lead con presupuesto normal (baño, sin Gate ni cambios)")
    # Lead de baño y su cálculo (sin Gate).
    token_normal, alta_normal = lead_http("medio", False)
    calc_normal = calcular(alta_normal["oportunidad_id"])
    r, j = pedir(token_normal)
    # Presupuesto sin Gate y el estado que dejó el cálculo.
    comprobar(
        "presupuesto = {true, false, null}",
        j.get("presupuesto") == {"existe": True, "requiere_aprobacion": False, "motivo_gate": None},
    )
    # El estado del cálculo (presupuesto_enviado).
    comprobar(
        "estado_oportunidad = el status que devolvió calculate-estimate",
        j.get("estado_oportunidad") == calc_normal["status"],
        f"({j.get('estado_oportunidad')!r})",
    )
    # El plazo de baño, con los valores de reglas_negocio y sin fabricación.
    esperado_bano = plazo_esperado("bano")
    comprobar(
        "plazo de baño = reglas_negocio, fabricación null",
        j.get("plazo_orientativo") == esperado_bano,
        f"({j.get('plazo_orientativo')} / esperado {esperado_bano})",
    )

    # ---- CASO 6: Gate por cambios estructurales ----
    print("\nCASO 6 - Lead con Gate (cambios estructurales)")
    # Lead con cambios: el cálculo pone Gate.
    token_gate, alta_gate = lead_http("medio", True)
    calcular(alta_gate["oportunidad_id"])
    r, j = pedir(token_gate)
    # Gate por cambios y su estado.
    comprobar(
        "presupuesto = {true, true, 'cambios_estructurales'}",
        j.get("presupuesto")
        == {"existe": True, "requiere_aprobacion": True, "motivo_gate": "cambios_estructurales"},
    )
    # El estado de un caso con Gate.
    comprobar("estado_oportunidad = 'pendiente_aprobacion'", j.get("estado_oportunidad") == "pendiente_aprobacion")
    # Con Gate, sin plazo.
    comprobar("plazo_orientativo = null (Gate)", j.get("plazo_orientativo") is None)

    print("\nCASO 7 - Longitud del token (mismas reglas que LeadCreate)")
    # 101 caracteres con cabecera: 422.
    r = sesion(token_101)
    # El primer error del 422 (o un diccionario vacío si no fue 422).
    detalle = r.json().get("detail", [{}])[0] if r.status_code == 422 else {}
    comprobar("101 caracteres con cabecera -> 422", r.status_code == 422, f"(HTTP {r.status_code})")
    comprobar(
        "el 422 señala el parámetro de ruta lead_token",
        detalle.get("loc") == ["path", "lead_token"],
        f"(loc {detalle.get('loc')}, {detalle.get('type')})",
    )
    # 100 caracteres: el límite es válido.
    r = sesion(token_100)
    comprobar(
        "100 caracteres -> 200 con existe=false (el límite es válido)",
        r.status_code == 200 and cuerpo(r).get("existe") is False,
        f"(HTTP {r.status_code})",
    )

    # ---- CASO 8: varias oportunidades en un lead ----
    print("\nCASO 8 - Lead con DOS oportunidades")
    # Segunda oportunidad insertada a mano: la API no tiene ningún camino
    # que la cree, y precisamente por eso el caso hay que provocarlo.
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano') RETURNING id;",
        (alta_sin["lead_id"],),
    )
    # Su id, confirmado.
    segunda = cur.fetchone()[0]
    cn.commit()
    print(f"      oportunidades del lead {alta_sin['lead_id']}: {alta_sin['oportunidad_id']} (primera) y {segunda}")

    # El servidor corre en un hilo de ESTE proceso, así que su print a
    # sys.stderr se puede capturar sustituyendo sys.stderr un momento.
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        r = sesion(token_sin)
    # Las líneas del aviso de varias oportunidades.
    avisos = [l for l in buffer.getvalue().splitlines() if "oportunidades" in l]
    # Se enseña todo lo capturado.
    for linea in buffer.getvalue().splitlines():
        print(f"      stderr> {linea}")
    # La respuesta: la primera oportunidad, y un aviso.
    j = cuerpo(r)
    comprobar(
        "devuelve la PRIMERA oportunidad",
        j.get("oportunidad_id") == alta_sin["oportunidad_id"],
        f"({j.get('oportunidad_id')})",
    )
    # Y un solo aviso en stderr.
    comprobar(
        "sale un [AVISO] con 2 oportunidades",
        len(avisos) == 1 and avisos[0].startswith("[AVISO]") and "2 oportunidades" in avisos[0],
        f"({len(avisos)} aviso/s)",
    )

    # ---- CASO 10: cocina, con fabricación de muebles ----
    print("\nCASO 10 - Cocina sin cambios ni Gate: obra y fabricación")
    # Lead de cocina y su cálculo.
    token_cocina, alta_cocina = lead_http("medio", False, tipo="cocina")
    calcular(alta_cocina["oportunidad_id"])
    r, j = pedir(token_cocina)
    # Que de verdad no haya Gate (si lo hubiera, el caso no probaría nada).
    comprobar("sin Gate (requiere_aprobacion = false)",
              (j.get("presupuesto") or {}).get("requiere_aprobacion") is False)
    # El plazo de cocina, con obra Y fabricación de reglas_negocio.
    esperado_cocina = plazo_esperado("cocina")
    comprobar(
        "plazo de cocina = reglas_negocio, con fabricación",
        j.get("plazo_orientativo") == esperado_cocina,
        f"({j.get('plazo_orientativo')} / esperado {esperado_cocina})",
    )

    # ---- CASO 11: tipo sin plazo ----
    print("\nCASO 11 - Reforma integral sin Gate: tipo sin plazo")
    # Lead de reforma integral, pequeña y básica (sin Gate), y su cálculo.
    token_integral, alta_integral = lead_http("basico", False, tipo="integral_vivienda")
    calcular(alta_integral["oportunidad_id"])
    r, j = pedir(token_integral)
    # Sin Gate, y aun así sin plazo: el tipo no lo tiene.
    comprobar("sin Gate (requiere_aprobacion = false)",
              (j.get("presupuesto") or {}).get("requiere_aprobacion") is False)
    comprobar("plazo_orientativo = null (integral_vivienda no tiene plazo)", j.get("plazo_orientativo") is None)

    # ---- CASO 12: Gate por importe (condición del Gate) ----
    print("\nCASO 12 - Baño con Gate por IMPORTE, sin cambios estructurales")
    # 40 m2 en nivel alto superan el umbral del baño (13.000 EUR sin IVA).
    token_importe, alta_importe = lead_http("alto", False, m2=40)
    calcular(alta_importe["oportunidad_id"])
    r, j = pedir(token_importe)
    # Que el Gate sea por importe y no por cambios: solo así se aísla la
    # condición del Gate.
    comprobar(
        "Gate por importe (requiere_aprobacion = true, motivo distinto de cambios_estructurales)",
        (j.get("presupuesto") or {}).get("requiere_aprobacion") is True
        and (j.get("presupuesto") or {}).get("motivo_gate") != "cambios_estructurales"
        and (j.get("reforma") or {}).get("incluye_cambios_estructurales") is False,
        f"({j.get('presupuesto')})",
    )
    # Con Gate, sin plazo.
    comprobar("plazo_orientativo = null (Gate)", j.get("plazo_orientativo") is None)

    print("\nCASO 13 - Baño sin Gate con cambios estructurales (dato puesto a mano)")
    # Lead de baño sin cambios y su cálculo (sin Gate).
    token_cambios, alta_cambios = lead_http("medio", False)
    calcular(alta_cambios["oportunidad_id"])
    # A mano, en la fila PROPIA (por su lead_id): cambios = true sin Gate.
    # jsonb_set cambia una sola clave del JSONB.
    cur.execute(
        """UPDATE leads SET datos_estructurados =
               jsonb_set(datos_estructurados, '{incluye_cambios_estructurales}', 'true')
           WHERE id = %s;""",
        (alta_cambios["lead_id"],),
    )
    # Se confirma el cambio y se pide la sesión.
    cn.commit()
    r, j = pedir(token_cambios)
    # El estado preparado: sin Gate y con cambios.
    comprobar(
        "sin Gate y con cambios = true en la reforma",
        (j.get("presupuesto") or {}).get("requiere_aprobacion") is False
        and (j.get("reforma") or {}).get("incluye_cambios_estructurales") is True,
    )
    # Con cambios, sin plazo.
    comprobar("plazo_orientativo = null (cambios estructurales)", j.get("plazo_orientativo") is None)
    # Ahora sin la clave (el operador - quita una clave del JSONB): null.
    cur.execute(
        "UPDATE leads SET datos_estructurados = datos_estructurados - 'incluye_cambios_estructurales' WHERE id = %s;",
        (alta_cambios["lead_id"],),
    )
    # Se confirma el cambio y se pide otra vez la sesión.
    cn.commit()
    r, j = pedir(token_cambios)
    # La clave falta: null en la reforma, y sin plazo (D4).
    comprobar("sin la clave: cambios = null en la reforma",
              r.status_code == 200 and (j.get("reforma") or {}).get("incluye_cambios_estructurales", "falta") is None)
    comprobar("plazo_orientativo = null (dato que falta, D4)", j.get("plazo_orientativo") is None)

    # ---- CASO 14: el 503 de configuración ----
    print("\nCASO 14 - Regla del plazo rota en memoria: 503 solo si hace falta")
    # El id de log más alto ANTES, para comprobar que el 503 no escribe.
    cur.execute("SELECT coalesce(max(id), 0) FROM logs;")
    id_base = cur.fetchone()[0]
    cn.commit()
    # Se guarda la pareja original de la obra del baño para restaurarla.
    original = plazo_orientativo.CLAVES_PLAZO["bano"]["obra"]
    # Una clave que no existe en reglas_negocio.
    clave_rota = "semanas_obra_bano_min_no_existe"
    # try / finally: la clave original vuelve SIEMPRE, falle lo que falle.
    try:
        # El cambio en memoria: el servidor (mismo proceso) lo ve al instante.
        plazo_orientativo.CLAVES_PLAZO["bano"]["obra"] = (clave_rota, original[1])
        # Se captura el stderr del servidor durante la petición.
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            r = sesion(token_normal)
        # La respuesta, tal cual.
        print(f"      <- {r.status_code} {r.text}")
        # Se enseña lo capturado.
        for linea in buffer.getvalue().splitlines():
            print(f"      stderr> {linea}")
        # El cuerpo del 503 (o vacío si no lo fue).
        detalle = r.json().get("detail", {}) if r.status_code == 503 else {}
        comprobar("baño sin Gate con la regla rota -> 503", r.status_code == 503, f"(HTTP {r.status_code})")
        comprobar("motivo configuracion_incompleta", detalle.get("motivo") == "configuracion_incompleta")
        # La clave rota, nombrada en la respuesta y en el aviso.
        comprobar("faltan nombra la clave rota",
                  any(clave_rota in f for f in detalle.get("faltan", [])), f"({detalle.get('faltan')})")
        comprobar("sale un [AVISO] en stderr que la nombra",
                  any(l.startswith("[AVISO]") and clave_rota in l for l in buffer.getvalue().splitlines()))
        # Los que no necesitan plazo NO leen las reglas: siguen en 200.
        r_integral = sesion(token_integral)
        r_gate = sesion(token_gate)
        r_nuevo = sesion(str(uuid.uuid4()))
        # Lo demás NO lee las reglas: sigue en 200.
        comprobar(
            "integral, Gate y token inexistente siguen en 200 (no leen las reglas)",
            (r_integral.status_code, r_gate.status_code, r_nuevo.status_code) == (200, 200, 200),
            f"({r_integral.status_code}, {r_gate.status_code}, {r_nuevo.status_code})",
        )
    # Se restaura la pareja original.
    finally:
        plazo_orientativo.CLAVES_PLAZO["bano"]["obra"] = original
    # Ningún log nuevo de las oportunidades propias desde id_base (el 503
    # es de solo lectura). Se combina la marca con id > id_base (regla de
    # CLAUDE.md) para no contar lo que escriba otro proceso.
    cur.execute(
        """SELECT count(*) FROM logs WHERE id > %s AND entity_type = 'oportunidad'
           AND entity_id = ANY(%s);""",
        (id_base, [alta_normal["oportunidad_id"], alta_integral["oportunidad_id"], alta_gate["oportunidad_id"]]),
    )
    # El recuento, y se cierra la transacción de lectura.
    nuevos_logs = cur.fetchone()[0]
    cn.commit()
    comprobar("el 503 no escribe ningún log", nuevos_logs == 0, f"({nuevos_logs})")
    # Con la clave restaurada, vuelve el 200 con el plazo.
    r = sesion(token_normal)
    comprobar("restaurada la clave, baño vuelve a 200 con su plazo",
              r.status_code == 200 and cuerpo(r).get("plazo_orientativo") == esperado_bano)

    # ---- CASO 15: claves exactas ----
    print("\nCASO 15 - Claves EXACTAS de todas las respuestas 200")
    # Cuántas respuestas 200 se revisan.
    print(f"      respuestas 200 revisadas: {len(respuestas_200)}")
    # Cada respuesta 200, nivel a nivel.
    for token_corto, respuesta in respuestas_200:
        problemas = problemas_de_claves(respuesta)
        comprobar(f"claves exactas (token {token_corto}...)", not problemas,
                  f"-> {problemas}" if problemas else "")

    # ---- CASO 9: ningún importe (va el último, sobre todas las 200) ----
    print("\nCASO 9 - Ningún importe en ninguna respuesta 200")
    # Primero, que la comprobación tenga sentido: los presupuestos de los
    # casos 5, 6, 10 y 12 TIENEN importes en la base de datos.
    con_presupuesto = [alta_normal["oportunidad_id"], alta_gate["oportunidad_id"],
                       alta_cocina["oportunidad_id"], alta_importe["oportunidad_id"]]
    cur.execute(
        "SELECT oportunidad_id, importe_min_con_iva, importe_max_con_iva FROM presupuestos WHERE oportunidad_id = ANY(%s) ORDER BY oportunidad_id;",
        (con_presupuesto,),
    )
    # Las filas, y se cierra la transacción de lectura.
    filas = cur.fetchall()
    cn.commit()
    # Se enseñan.
    for f in filas:
        print(f"      en la base de datos: oportunidad {f[0]} -> importes {f[1]} / {f[2]}")
    comprobar(
        "los 4 presupuestos de prueba tienen importes en la base de datos",
        len(filas) == 4 and all(f[1] is not None and f[2] is not None for f in filas),
    )
    # Cuántas respuestas 200 se revisan.
    print(f"      respuestas 200 revisadas: {len(respuestas_200)}")
    # Ninguna clave con "importe" en ninguna respuesta 200.
    for token_corto, respuesta in respuestas_200:
        encontradas = claves_con_importe(respuesta)
        comprobar(
            f"sin claves 'importe' (token {token_corto}...)",
            not encontradas,
            f"-> encontradas: {encontradas}" if encontradas else "",
        )
# Pase lo que pase: servidor parado y datos propios borrados.
finally:
    # Se para el servidor y se espera a que termine su hilo.
    servidor.should_exit = True
    hilo.join(timeout=10)
    # Se borran los datos propios y se comprueba que no queda ninguno.
    limpiar()
    cur.execute("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON_EMAIL,))
    restos = cur.fetchone()[0]
    # Se cierra la conexión.
    cn.commit()
    cn.close()
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")

# Resumen.
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
# Cierre de la cabecera del resultado.
print("=" * 78)
# Con algún fallo: se listan y el script sale con código 1.
if fallos:
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        print(f"  - {f}")
    # Código 1: fallo.
    sys.exit(1)
