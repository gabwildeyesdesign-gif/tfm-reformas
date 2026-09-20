"""
BLOQUE 4 - Verificación REAL de la puerta MCP: autenticación de /mcp y
tool calculate_estimate.

Levanta el servidor real (uvicorn.Server en un hilo, sin --reload) y lo
prueba de dos formas:
  - Con peticiones HTTP "a pelo" (requests) contra /mcp/, para ver los
    códigos de estado de la autenticación: lo que recibiría cualquiera
    que intente entrar sin permiso.
  - Con fastmcp.Client, que habla el protocolo MCP completo, igual que
    el nodo MCP Client Tool de n8n: lista las tools y las llama.

Al final comprueba el fail fast: que uvicorn se niega a arrancar si
MCP_SECRET está vacío.

Datos de prueba: emails mcp-estimate-*@example.com, borrados al empezar y
al terminar.
"""

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from app.config import DATABASE_URL, MCP_SECRET, WEBHOOK_SECRET

PUERTO = 8012
BASE = f"http://127.0.0.1:{PUERTO}"
URL_MCP = f"{BASE}/mcp/"
PATRON_EMAIL = "mcp-estimate-%@example.com"

# Primer mensaje del protocolo MCP ("initialize"): el que envía cualquier
# cliente al conectarse. Se usa para las pruebas HTTP a pelo.
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "check", "version": "0"},
    },
}
CABECERAS_MCP = {"Accept": "application/json, text/event-stream"}

ok = 0
fallos = []


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


def lead_http(sufijo, tipo, nivel, m2, estructural):
    r = requests.post(
        f"{BASE}/leads",
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        json={
            "nombre": f"Prueba {sufijo}",
            "email": f"mcp-estimate-{sufijo}@example.com",
            "telefono": "600000000",
            "tipo_reforma": tipo,
            "m2": m2,
            "nivel_acabados": nivel,
            "incluye_cambios_estructurales": estructural,
            "lead_token": f"tok-{sufijo}",
        },
        timeout=20,
    )
    assert r.status_code == 201, r.text
    return r.json()["oportunidad_id"]


def post_mcp(cabeceras):
    """POST /mcp/ con el mensaje initialize; devuelve la respuesta HTTP."""
    return requests.post(URL_MCP, json=INITIALIZE, headers={**CABECERAS_MCP, **cabeceras}, timeout=10)


async def pruebas_con_cliente(ops):
    """Todas las pruebas que hablan el protocolo MCP completo."""
    transporte = StreamableHttpTransport(URL_MCP, auth=MCP_SECRET)
    async with Client(transporte) as client:
        print("\n  list_tools")
        tools = {t.name: t for t in await client.list_tools()}
        comprobar("calculate_estimate está registrada", "calculate_estimate" in tools,
                  f"({list(tools)})")
        esquema = tools["calculate_estimate"].input_schema
        print(f"    esquema de entrada que ve el agente: {esquema}")
        comprobar("el agente solo puede enviar oportunidad_id",
                  list(esquema["properties"]) == ["oportunidad_id"]
                  and esquema.get("additionalProperties") is False
                  and esquema.get("required") == ["oportunidad_id"])

        async def llamar(op):
            r = await client.call_tool("calculate_estimate", {"oportunidad_id": op})
            return r.structured_content

        casos = [
            ("sin Gate -> importes visibles", ops["sin_gate"], ("8349.00", "9601.35"), None),
            ("importe_superior_umbral -> importes visibles", ops["umbral"],
             ("77440.00", "89056.00"), "importe_superior_umbral"),
            ("cambios_estructurales -> importes NULL", ops["estr"], (None, None),
             "cambios_estructurales"),
            ("ambos -> importes NULL", ops["ambos"], (None, None), "ambos"),
        ]
        for titulo, op, importes, motivo in casos:
            print(f"\n  call_tool: {titulo}")
            sc = await llamar(op)
            print(f"    {sc}")
            comprobar(titulo,
                      (sc["importe_min_con_iva"], sc["importe_max_con_iva"]) == importes
                      and sc["motivo_gate"] == motivo and sc["presupuesto_id"] is not None
                      and sc["creado"] is True)

        # Los importes se ocultan AL AGENTE, pero existen y están guardados.
        cur.execute(
            "SELECT importe_min_con_iva, importe_max_con_iva FROM presupuestos WHERE oportunidad_id = %s;",
            (ops["estr"],),
        )
        guardado = cur.fetchone()
        cn.commit()
        comprobar("los importes ocultados SÍ están guardados en presupuestos",
                  tuple(str(x) for x in guardado) == ("9075.00", "10436.25"), f"({guardado})")

        print("\n  call_tool: segunda llamada (idempotencia)")
        sc = await llamar(ops["sin_gate"])
        comprobar("creado=false y mismos importes",
                  sc["creado"] is False and sc["importe_min_con_iva"] == "8349.00")

        print("\n  call_tool: errores")
        for titulo, args in [
            ("oportunidad inexistente", {"oportunidad_id": 2_000_000_000}),
            ("argumento extra m2 (el agente intenta pasar datos)",
             {"oportunidad_id": ops["sin_gate"], "m2": 1}),
            ("oportunidad_id = 0", {"oportunidad_id": 0}),
        ]:
            try:
                await client.call_tool("calculate_estimate", args)
                comprobar(f"{titulo} -> ToolError", False, "(no falló)")
            except ToolError as e:
                comprobar(f"{titulo} -> ToolError", True, f"({str(e).splitlines()[0]})")

        # El mensaje de error no debe filtrar detalles internos.
        try:
            await client.call_tool("calculate_estimate", {"oportunidad_id": 2_000_000_000})
        except ToolError as e:
            comprobar("el mensaje de error es el genérico, sin traceback",
                      str(e) == "No existe ninguna oportunidad con ese id.", f"({e})")


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
    print("\n" + "=" * 78)
    print("M1 - AUTENTICACIÓN DE /mcp con HTTP a pelo")
    print("=" * 78)
    for titulo, cabeceras, esperado in [
        ("sin cabecera Authorization", {}, 401),
        ("Bearer incorrecto", {"Authorization": "Bearer malo"}, 401),
        # requests envía las cabeceras en latin-1, así que la ñ viaja como
        # un byte no ASCII: el caso que en security.py daría 500 si se
        # comparase en str.
        ("Bearer con carácter no ASCII (ñ)", {"Authorization": "Bearer ñ"}, 401),
        ("WEBHOOK_SECRET como token (secretos separados)",
         {"Authorization": f"Bearer {WEBHOOK_SECRET}"}, 401),
        ("MCP_SECRET correcto", {"Authorization": f"Bearer {MCP_SECRET}"}, 200),
    ]:
        r = post_mcp(cabeceras)
        detalle = f"({r.status_code}, WWW-Authenticate: {r.headers.get('www-authenticate', '-')[:40]})"
        comprobar(f"{titulo} -> {esperado}", r.status_code == esperado, detalle)

    print("\n" + "=" * 78)
    print("M2 - PROTOCOLO MCP COMPLETO con fastmcp.Client")
    print("=" * 78)
    ops = {
        "sin_gate": lead_http("singate", "bano", "medio", 6, False),
        "umbral": lead_http("umbral", "integral_vivienda", "medio", 80, False),
        "estr": lead_http("estr", "bano", "medio", 6, True),
        "ambos": lead_http("ambos", "integral_vivienda", "medio", 80, True),
    }
    asyncio.run(pruebas_con_cliente(ops))

finally:
    limpiar()
    print("\n  Datos de prueba eliminados.")
    servidor.should_exit = True
    hilo.join(timeout=15)
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print("M3 - FAIL FAST: uvicorn no arranca con MCP_SECRET vacío")
print("=" * 78)
# Se lanza uvicorn en un PROCESO aparte con MCP_SECRET="". load_dotenv()
# no sobrescribe una variable que ya existe en el entorno, así que el
# proceso hijo ve el valor vacío y no el del .env.
# PYTHONIOENCODING=utf-8: sin él, el hijo escribiría su error en cp1252
# (tiene tildes) y leerlo como UTF-8 reventaría (gotcha de CLAUDE.md).
entorno = dict(os.environ, MCP_SECRET="", PYTHONIOENCODING="utf-8")
proc = subprocess.run(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8013"],
    cwd=RAIZ_REPO, env=entorno, capture_output=True, encoding="utf-8", timeout=60,
)
ultima = [l for l in proc.stderr.splitlines() if "MCP_SECRET" in l]
comprobar("uvicorn termina con error, sin quedarse sirviendo", proc.returncode != 0,
          f"(código {proc.returncode})")
comprobar("el error explica que falta MCP_SECRET",
          any("MCP_SECRET no está definido" in l for l in ultima),
          f"({ultima[-1][:90] if ultima else 'sin mensaje'})")

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
for f in fallos:
    print(f"  FALLO: {f}")
print("=" * 78)
sys.exit(0 if not fallos else 1)
