"""
Verificación REAL de la tool MCP consultar_huecos_libres (D32, Bloque B4,
plan docs/Plan_Horario_Visitas_D32.txt, 3.5 y 7).

Levanta el servidor real (uvicorn.Server en un hilo de este proceso, SIN
--reload, en el puerto 8033: nunca el 8000) y lo prueba como el nodo MCP
Client de n8n, con fastmcp.Client. Solo lectura: no crea ninguna fila.

  CASO 1. /mcp sin Bearer y con el valor de GATE_SECRET: 401 (el Agente 2
          nunca lleva X-Gate-Secret).
  CASO 2. list_tools: las tres tools; consultar_huecos_libres con un solo
          parámetro, "fecha", opcional.
  CASO 3. Modo días: la misma respuesta que GET /huecos-libres (salvo
          generado_en), sin etiqueta, con las claves exactas (envuelta por
          fastmcp en {"result": ...}).
  CASO 4. Modo un día (un laborable del bloque propio y su sábado): la
          misma respuesta que GET /huecos-libres?fecha= (salvo generado_en).
  CASO 5. Fechas mal escritas por el LLM ("mañana", "15/10/2026",
          "2026-02-30", "2026-10-15T10:00", ""): error de la tool con el
          mensaje del formato (rama else: si no sale, FALLO; N13).
  CASO 6. Configuración rota EN MEMORIA (una clave falsa; ninguna fila se
          toca): error genérico de la tool; restaurada, vuelve a responder.
  CASO 7. Estructura (ast): la tool llama a consultar_dias y consultar_dia
          del servicio y no calcula horas (N11).

Sale con código 1 si algo falla.
"""

# ast: estructura del código (CASO 7).
import ast
# asyncio: fastmcp.Client es asíncrono.
import asyncio
# sys: ruta del paquete, consola y código de salida.
import sys
# threading: el servidor en un hilo.
import threading
# time: esperar a que arranque.
import time
# Fechas.
from datetime import datetime, timedelta
# Path: rutas.
from pathlib import Path
# La zona de Madrid.
from zoneinfo import ZoneInfo

# La raíz del repositorio en sys.path.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# requests: las peticiones HTTP (CASO 1 y la referencia REST).
import requests
# uvicorn: el servidor real.
import uvicorn
# El cliente MCP de fastmcp.
from fastmcp import Client
# El transporte HTTP con el token Bearer.
from fastmcp.client.transports import StreamableHttpTransport
# El error que lanza el cliente cuando la tool falla.
from fastmcp.exceptions import ToolError

# Los secretos (nunca se imprimen).
from app.config import GATE_SECRET, MCP_SECRET
# Los dos mensajes de error de la tool.
from app.mcp_server.server import MENSAJE_FECHA_NO_VALIDA, MENSAJE_REGLAS_NO_DISPONIBLES
# Las reglas de visita (CASO 6: la clave falsa en memoria).
import app.services.reglas_visita as rv

# Las fechas propias (un laborable lejano del bloque de check_huecos_libres_http,
# solo para leer: este script no escribe nada).
import fechas_prueba

# Puerto propio (nunca el 8000).
PUERTO = 8033
# La dirección base.
BASE = f"http://127.0.0.1:{PUERTO}"
# La puerta MCP.
URL_MCP = f"{BASE}/mcp/"
# La tool que se prueba.
TOOL = "consultar_huecos_libres"
# La cabecera de administración para la referencia REST.
AUTH_GATE = {"X-Gate-Secret": GATE_SECRET}
# El primer mensaje del protocolo MCP (CASO 1).
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}}}
# La cabecera Accept del transporte HTTP de MCP.
CABECERAS_MCP = {"Accept": "application/json, text/event-stream"}
# Las claves de un día del modo días en la tool (sin etiqueta).
CLAVES_DIA_AGENTE = {"fecha", "dia_semana", "manana", "tarde", "num_horas_libres"}
# La zona de Madrid.
MADRID = ZoneInfo("Europe/Madrid")
# Hoy y un laborable lejano (el jueves de la primera semana del bloque).
HOY = datetime.now(MADRID).date()
# El lunes del bloque.
LUNES = fechas_prueba.lunes_de_prueba("check_huecos_libres_http", HOY)

# Contadores.
ok = 0
# Los fallos.
fallos = []


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


def rest(params=None):
    """GET /huecos-libres por REST (la referencia), como JSON."""
    # La petición con la llave de administración.
    return requests.get(f"{BASE}/huecos-libres", headers=AUTH_GATE, params=params or {}, timeout=60).json()


def sin(objeto, *claves):
    """Una copia del diccionario sin esas claves."""
    # Solo las demás.
    return {k: v for k, v in objeto.items() if k not in claves}


async def pruebas_mcp():
    """Los CASOS 2 a 6, con el cliente MCP."""
    # El transporte con el token Bearer correcto.
    transporte = StreamableHttpTransport(URL_MCP, auth=MCP_SECRET)
    # El cliente se conecta y se desconecta solo.
    async with Client(transporte) as client:
        # ------------------------------------------------------------
        print("\nCASO 2 - list_tools")
        # Las tools, por nombre.
        tools = {t.name: t for t in await client.list_tools()}
        # Las tres.
        comprobar("las tres tools: calculate_estimate, get_business_rules y consultar_huecos_libres",
                  set(tools) == {"calculate_estimate", "get_business_rules", TOOL}, f"({sorted(tools)})")
        # Su esquema de entrada.
        entrada = tools[TOOL].input_schema if TOOL in tools else {}
        # Se enseña.
        print(f"      esquema de entrada: {entrada}")
        # Solo "fecha", y no obligatoria.
        comprobar("un solo parámetro, 'fecha', opcional",
                  list(entrada.get("properties", {})) == ["fecha"] and not entrada.get("required"), f"({entrada})")

        # ------------------------------------------------------------
        print("\nCASO 3 - Modo días: igual que GET /huecos-libres, sin etiqueta")
        # La tool, sin fecha.
        r = await client.call_tool(TOOL, {})
        # La salida estructurada (fastmcp envuelve la unión en "result").
        sc = (r.structured_content or {}).get("result", {})
        # La referencia REST, justo después.
        ref = rest()
        # Se enseña el primer día de cada una.
        print(f"      tool: {(sc.get('dias') or [None])[0]}")
        # Y la de REST.
        print(f"      REST: {(ref.get('dias') or [None])[0]}")
        # Claves de cada día sin etiqueta.
        comprobar("cada día de la tool tiene las claves exactas (sin etiqueta)",
                  bool(sc.get("dias")) and all(set(d) == CLAVES_DIA_AGENTE for d in sc["dias"]), f"({sc.get('dias', [])[:1]})")
        # Los mismos días que REST, sin etiqueta ni generado_en.
        comprobar("los mismos días y horas que GET /huecos-libres",
                  sc.get("modo") == "dias" and sc.get("dias") == [sin(d, "etiqueta") for d in ref.get("dias", [])],
                  f"({len(sc.get('dias', []))} / {len(ref.get('dias', []))})")

        # ------------------------------------------------------------
        print("\nCASO 4 - Modo un día: igual que GET /huecos-libres?fecha=")
        # Un jueves del bloque y su sábado.
        for dia in (LUNES + timedelta(days=3), LUNES + timedelta(days=5)):
            # La tool con la fecha.
            r = await client.call_tool(TOOL, {"fecha": dia.isoformat()})
            # La salida estructurada.
            sc = (r.structured_content or {}).get("result", {})
            # La referencia REST.
            ref = rest({"fecha": dia.isoformat()})
            # Iguales salvo generado_en.
            comprobar(f"{dia} ({ref.get('dia_semana')}): la misma respuesta que REST (salvo generado_en)",
                      sc.get("modo") == "dia" and sin(sc, "generado_en") == sin(ref, "generado_en"), f"({sc})")

        # ------------------------------------------------------------
        print("\nCASO 5 - Fechas mal escritas por el LLM (N13)")
        # Cada una.
        for mala in ("mañana", "15/10/2026", "2026-02-30", "2026-10-15T10:00", ""):
            # try/except/else: sin el error, FALLO.
            try:
                # Debe fallar.
                await client.call_tool(TOOL, {"fecha": mala})
            # Sale: con el mensaje del formato.
            except ToolError as error:
                # El mensaje exacto.
                comprobar(f"fecha {mala!r}: error de la tool con el mensaje del formato",
                          str(error) == MENSAJE_FECHA_NO_VALIDA, f"({error})")
            # No sale: la tool ha aceptado la fecha.
            else:
                # FALLO explícito.
                comprobar(f"fecha {mala!r}: error de la tool", False, "(la excepción se ha tragado)")

        # ------------------------------------------------------------
        print("\nCASO 6 - Configuración rota EN MEMORIA")
        # Las claves reales.
        reales = rv.CLAVES_REGLAS
        # try/finally: vuelven siempre.
        try:
            # Una clave falsa (el servidor corre en este proceso: la ve).
            rv.CLAVES_REGLAS = reales + ("clave_que_no_existe_d32",)
            # try/except/else: sin el error, FALLO.
            try:
                # Debe fallar.
                await client.call_tool(TOOL, {})
            # Sale: el mensaje genérico, sin la clave.
            except ToolError as error:
                # El genérico.
                comprobar("error de la tool con el mensaje genérico, sin nombrar la clave",
                          str(error) == MENSAJE_REGLAS_NO_DISPONIBLES and "clave_que_no_existe" not in str(error), f"({error})")
            # No sale.
            else:
                # FALLO explícito.
                comprobar("error de la tool con la configuración rota", False, "(la excepción se ha tragado)")
        # Siempre: las reales.
        finally:
            # Restauradas.
            rv.CLAVES_REGLAS = reales
        # Restaurada, vuelve a responder.
        r = await client.call_tool(TOOL, {})
        # Con días.
        comprobar("restaurada, la tool vuelve a responder con días",
                  bool(((r.structured_content or {}).get("result") or {}).get("dias")))


# El servidor real, sin --reload, en el puerto propio.
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# En un hilo daemon.
hilo = threading.Thread(target=servidor.run, daemon=True)
# Arranca.
hilo.start()
# Se espera a /health.
inicio = time.time()
# Hasta 30 s.
while time.time() - inicio < 30:
    # try: mientras arranca, se rechaza.
    try:
        # Arriba.
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            # Listo.
            break
    # Todavía no.
    except requests.exceptions.RequestException:
        # Se espera.
        time.sleep(0.25)
# No arrancó.
else:
    # Se avisa.
    print("  El servidor no respondió en 30 s")
    # Código 1.
    sys.exit(1)
# Arriba.
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio:.2f} s")

# try/finally: el servidor se para siempre.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación de /mcp")
    # Sin Bearer.
    r = requests.post(URL_MCP, json=INITIALIZE, headers=CABECERAS_MCP, timeout=10)
    # 401.
    comprobar("sin Bearer -> 401", r.status_code == 401, f"({r.status_code})")
    # Con el valor de GATE_SECRET.
    r = requests.post(URL_MCP, json=INITIALIZE, headers={**CABECERAS_MCP, "Authorization": f"Bearer {GATE_SECRET}"},
                      timeout=10)
    # 401.
    comprobar("con el valor de GATE_SECRET -> 401", r.status_code == 401, f"({r.status_code})")
    # Los CASOS 2 a 6.
    asyncio.run(pruebas_mcp())

    # ------------------------------------------------------------------
    print("\nCASO 7 - Estructura de la tool (ast)")
    # El árbol de server.py.
    arbol = ast.parse((RAIZ_REPO / "app/mcp_server/server.py").read_text(encoding="utf-8"))
    # La función de la tool.
    funcion = next((n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef) and n.name == TOOL), None)
    # Las llamadas de tipo modulo.funcion dentro de ella.
    atributos = {f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(funcion or ast.Module(body=[], type_ignores=[]))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)}
    # Llama a las dos funciones del servicio.
    comprobar("la tool llama a huecos_libres_service.consultar_dias y consultar_dia",
              {"huecos_libres_service.consultar_dias", "huecos_libres_service.consultar_dia"} <= atributos, f"({sorted(atributos)})")
    # Sin bucles ni range: no calcula horas.
    comprobar("la tool no tiene while ni range (no calcula horas)",
              funcion is not None and not any(isinstance(n, ast.While) or (isinstance(n, ast.Call)
                                              and isinstance(n.func, ast.Name) and n.func.id == "range")
                                              for n in ast.walk(funcion)))
# Siempre: el servidor se para.
finally:
    # Que termine.
    servidor.should_exit = True
    # Se espera al hilo.
    hilo.join(timeout=15)

# Resumen.
total = ok + len(fallos)
# Separador.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{total} correctas")
# Separador.
print("=" * 78)
# Con fallos, código 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
