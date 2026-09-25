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
  3. Token inexistente -> 200 con existe=false, todo null y presupuesto
     {existe: false, requiere_aprobacion: null, motivo_gate: null}.
  4. Lead sin presupuesto -> existe=true, ids correctos, estado 'nueva',
     presupuesto.existe=false.
  5. Lead con presupuesto normal -> presupuesto {true, false, null}.
  6. Lead con Gate (cambios estructurales) -> {true, true,
     'cambios_estructurales'}, estado 'pendiente_aprobacion'.
  7. Token de 101 caracteres CON cabecera -> 422 que señala lead_token.
     Token de 100 caracteres -> 200 (el límite exacto es válido).
  8. Lead con DOS oportunidades -> devuelve la primera, y el servidor
     deja un [AVISO] en stderr.
  9. En TODAS las respuestas 200, ninguna clave, a ningún nivel de
     anidamiento, contiene "importe". Y, para que esa ausencia signifique
     algo, se comprueba en la base de datos que esos presupuestos SÍ
     tienen importes.

Tokens: uuid4 en cada ejecución, ninguno fijo (un token fijo compartido
entre scripts es una fragilidad ya anotada en CLAUDE.md).
Datos de prueba: emails http-sesion-*@example.com, borrados al empezar y
al terminar.
"""

import contextlib
import io
import sys
import threading
import time
import uuid
from pathlib import Path

# El paquete "app" está en la raíz del repositorio, no en scripts/: se
# añade la raíz a sys.path (misma técnica que los demás check_*.py).
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn

from app.config import DATABASE_URL, WEBHOOK_SECRET

# Puerto propio, distinto de los demás scripts HTTP (8010, 8011, 8012,
# 8015), para que no choquen si alguno se queda colgado.
PUERTO = 8016
BASE = f"http://127.0.0.1:{PUERTO}"
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
PATRON_EMAIL = "http-sesion-%@example.com"

ok = 0
fallos = []

# Todas las respuestas 200 del endpoint, para el caso 9.
respuestas_200 = []


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


def limpiar():
    """Borra los datos de prueba, en orden inverso a las claves foráneas."""
    cur.execute(
        """SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id
           JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %s;""",
        (PATRON_EMAIL,),
    )
    ids = [f[0] for f in cur.fetchall()]
    cur.execute("DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM presupuestos WHERE oportunidad_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM oportunidades WHERE id = ANY(%s);", (ids,))
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %s);",
        (PATRON_EMAIL,),
    )
    cur.execute("DELETE FROM clientes WHERE email LIKE %s;", (PATRON_EMAIL,))
    cn.commit()


def lead_http(nivel, estructural):
    """Crea un lead por POST /leads con un token uuid4 nuevo.

    Devuelve (lead_token, respuesta JSON de POST /leads)."""
    token = str(uuid.uuid4())
    r = requests.post(
        f"{BASE}/leads",
        headers=AUTH,
        json={
            "nombre": "Prueba Sesión",
            # Los 8 primeros caracteres del token bastan para un email único.
            "email": f"http-sesion-{token[:8]}@example.com",
            "telefono": "600000000",
            "tipo_reforma": "bano",
            "m2": 6,
            "nivel_acabados": nivel,
            "incluye_cambios_estructurales": estructural,
            "lead_token": token,
        },
        timeout=20,
    )
    assert r.status_code == 201, r.text
    return token, r.json()


def calcular(oportunidad_id):
    """POST /calculate-estimate; devuelve el JSON de la respuesta."""
    r = requests.post(
        f"{BASE}/calculate-estimate", headers=AUTH, json={"oportunidad_id": oportunidad_id}, timeout=20
    )
    assert r.status_code == 200, r.text
    return r.json()


def sesion(token, cabeceras=AUTH):
    """GET /leads/session/{token}. Guarda las respuestas 200 para el caso 9."""
    r = requests.get(f"{BASE}/leads/session/{token}", headers=cabeceras, timeout=20)
    if r.status_code == 200:
        respuestas_200.append((token[:12], r.json()))
    return r


def cuerpo(r):
    """
    JSON de la respuesta si fue 200; si no, un diccionario vacío.

    Así, si el servidor devuelve un error (un 500, por ejemplo), las
    comprobaciones de abajo salen como [FALLO] en vez de reventar el
    script con un KeyError al buscar un campo que no existe.
    """
    return r.json() if r.status_code == 200 else {}


def claves_con_importe(objeto, ruta=""):
    """
    Recorre el JSON entero, incluidos los objetos anidados, y devuelve las
    rutas de todas las claves cuyo nombre contiene "importe".

    Es una función RECURSIVA: cuando encuentra un diccionario dentro de
    otro, se llama a sí misma para mirar también dentro. Así una clave
    escondida en presupuesto.importe_min_con_iva no se escapa.
    """
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


PRESUPUESTO_VACIO = {"existe": False, "requiere_aprobacion": None, "motivo_gate": None}

limpiar()

servidor = uvicorn.Server(
    uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning")
)
hilo = threading.Thread(target=servidor.run, daemon=True)
print("=" * 78)
print("ARRANQUE DEL SERVIDOR (uvicorn.Server, sin --reload)")
print("=" * 78)
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
print(f"  Servidor arriba en {time.time() - inicio:.2f} s")

try:
    token_inexistente = str(uuid.uuid4())
    token_101 = "x" * 101
    token_100 = "y" * 100

    print("\nCASO 1 - Sin cabecera X-Webhook-Secret")
    r = sesion(token_inexistente, cabeceras={})
    comprobar("401 sin cabecera", r.status_code == 401, f"(HTTP {r.status_code})")

    print("\nCASO 2 - Token de 101 caracteres SIN cabecera")
    r = sesion(token_101, cabeceras={})
    comprobar("401, no 422: la autenticación va antes", r.status_code == 401, f"(HTTP {r.status_code})")

    print("\nCASO 3 - Token inexistente")
    r = sesion(token_inexistente)
    print(f"      <- {r.status_code} {r.text}")
    esperado = {
        "existe": False,
        "lead_id": None,
        "oportunidad_id": None,
        "estado_oportunidad": None,
        "presupuesto": PRESUPUESTO_VACIO,
    }
    comprobar("200", r.status_code == 200)
    comprobar("JSON exacto: existe=false, todo null, presupuesto vacío", cuerpo(r) == esperado)

    print("\nCASO 4 - Lead sin presupuesto")
    token_sin, alta_sin = lead_http("medio", False)
    r = sesion(token_sin)
    print(f"      <- {r.status_code} {r.text}")
    j = cuerpo(r)
    comprobar("200 y existe=true", r.status_code == 200 and j.get("existe") is True)
    comprobar(
        "lead_id y oportunidad_id = los de POST /leads",
        (j.get("lead_id"), j.get("oportunidad_id")) == (alta_sin["lead_id"], alta_sin["oportunidad_id"]),
    )
    comprobar("estado_oportunidad = 'nueva'", j.get("estado_oportunidad") == "nueva")
    comprobar(
        "presupuesto = {false, null, null}: null y no false en requiere_aprobacion",
        j.get("presupuesto") == PRESUPUESTO_VACIO,
    )

    print("\nCASO 5 - Lead con presupuesto normal (sin Gate)")
    token_normal, alta_normal = lead_http("medio", False)
    calc_normal = calcular(alta_normal["oportunidad_id"])
    r = sesion(token_normal)
    print(f"      <- {r.status_code} {r.text}")
    j = cuerpo(r)
    comprobar(
        "presupuesto = {true, false, null}",
        j.get("presupuesto") == {"existe": True, "requiere_aprobacion": False, "motivo_gate": None},
    )
    comprobar(
        "estado_oportunidad = el status que devolvió calculate-estimate",
        j.get("estado_oportunidad") == calc_normal["status"],
        f"({j.get('estado_oportunidad')!r})",
    )

    print("\nCASO 6 - Lead con Gate (cambios estructurales)")
    token_gate, alta_gate = lead_http("medio", True)
    calcular(alta_gate["oportunidad_id"])
    r = sesion(token_gate)
    print(f"      <- {r.status_code} {r.text}")
    j = cuerpo(r)
    comprobar(
        "presupuesto = {true, true, 'cambios_estructurales'}",
        j.get("presupuesto")
        == {"existe": True, "requiere_aprobacion": True, "motivo_gate": "cambios_estructurales"},
    )
    comprobar("estado_oportunidad = 'pendiente_aprobacion'", j.get("estado_oportunidad") == "pendiente_aprobacion")

    print("\nCASO 7 - Longitud del token (mismas reglas que LeadCreate)")
    r = sesion(token_101)
    detalle = r.json().get("detail", [{}])[0] if r.status_code == 422 else {}
    comprobar("101 caracteres con cabecera -> 422", r.status_code == 422, f"(HTTP {r.status_code})")
    comprobar(
        "el 422 señala el parámetro de ruta lead_token",
        detalle.get("loc") == ["path", "lead_token"],
        f"(loc {detalle.get('loc')}, {detalle.get('type')})",
    )
    r = sesion(token_100)
    comprobar(
        "100 caracteres -> 200 con existe=false (el límite es válido)",
        r.status_code == 200 and cuerpo(r).get("existe") is False,
        f"(HTTP {r.status_code})",
    )

    print("\nCASO 8 - Lead con DOS oportunidades")
    # Segunda oportunidad insertada a mano: la API no tiene ningún camino
    # que la cree, y precisamente por eso el caso hay que provocarlo.
    cur.execute(
        "INSERT INTO oportunidades (lead_id, tipo_reforma) VALUES (%s, 'bano') RETURNING id;",
        (alta_sin["lead_id"],),
    )
    segunda = cur.fetchone()[0]
    cn.commit()
    print(f"      oportunidades del lead {alta_sin['lead_id']}: {alta_sin['oportunidad_id']} (primera) y {segunda}")

    # El servidor corre en un hilo de ESTE proceso, así que su print a
    # sys.stderr se puede capturar sustituyendo sys.stderr un momento.
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        r = sesion(token_sin)
    avisos = [l for l in buffer.getvalue().splitlines() if "oportunidades" in l]
    for linea in buffer.getvalue().splitlines():
        print(f"      stderr> {linea}")
    j = cuerpo(r)
    comprobar(
        "devuelve la PRIMERA oportunidad",
        j.get("oportunidad_id") == alta_sin["oportunidad_id"],
        f"({j.get('oportunidad_id')})",
    )
    comprobar(
        "sale un [AVISO] con 2 oportunidades",
        len(avisos) == 1 and avisos[0].startswith("[AVISO]") and "2 oportunidades" in avisos[0],
        f"({len(avisos)} aviso/s)",
    )

    print("\nCASO 9 - Ningún importe en ninguna respuesta 200")
    # Primero, que la comprobación tenga sentido: los presupuestos de los
    # casos 5 y 6 TIENEN importes en la base de datos.
    cur.execute(
        "SELECT oportunidad_id, importe_min_con_iva, importe_max_con_iva FROM presupuestos WHERE oportunidad_id = ANY(%s) ORDER BY oportunidad_id;",
        ([alta_normal["oportunidad_id"], alta_gate["oportunidad_id"]],),
    )
    filas = cur.fetchall()
    cn.commit()
    for f in filas:
        print(f"      en la base de datos: oportunidad {f[0]} -> importes {f[1]} / {f[2]}")
    comprobar(
        "los 2 presupuestos de prueba tienen importes en la base de datos",
        len(filas) == 2 and all(f[1] is not None and f[2] is not None for f in filas),
    )
    print(f"      respuestas 200 revisadas: {len(respuestas_200)}")
    for token_corto, cuerpo in respuestas_200:
        encontradas = claves_con_importe(cuerpo)
        comprobar(
            f"sin claves 'importe' (token {token_corto}...)",
            not encontradas,
            f"-> encontradas: {encontradas}" if encontradas else "",
        )
finally:
    servidor.should_exit = True
    hilo.join(timeout=10)
    limpiar()
    cur.execute("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON_EMAIL,))
    restos = cur.fetchone()[0]
    cn.commit()
    cn.close()
    print(f"\n  Limpieza: clientes de prueba restantes = {restos}")

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
