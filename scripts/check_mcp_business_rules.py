"""
Verificación REAL de la tool MCP get_business_rules (Bloque B3 del plan del
Agente 2, docs/Plan_Agente2_Calendar.txt, sección 3.4 y sección 7).

Levanta el servidor real (uvicorn.Server en un hilo de este proceso, SIN
--reload, en el puerto 8029: nunca el 8000, que es el uvicorn de n8n) y lo
prueba como lo hará el nodo MCP Client de n8n, con fastmcp.Client.

  CASO 1. Autenticación de /mcp por HTTP a pelo: 401 sin Bearer, con el
          WEBHOOK_SECRET y con el GATE_SECRET (el Agente 2 nunca lleva
          X-Gate-Secret); 200 con el MCP_SECRET.
  CASO 2. list_tools: EXACTAMENTE las tools publicadas (calculate_estimate y
          get_business_rules; desde D32, 2026-10-13, también
          consultar_huecos_libres); get_business_rules sin parámetros.
  CASO 3. Llamada: claves EXACTAS en todos los niveles y valores iguales a
          los leídos aquí, aparte, de la base de datos (el recargo con IVA
          se calcula aquí con su propia cuenta, no con aplicar_iva).
  CASO 4. Nada prohibido (C4): ninguna clave de precio, margen, umbral ni
          porcentaje, y ningún valor igual a un precio_m2, al margen, al
          tipo de IVA o a un umbral del Gate.
  CASO 5. Segunda llamada: la misma respuesta.
  CASO 6. Configuración rota EN MEMORIA (la clave del recargo cambiada por
          una que no existe; ninguna fila de reglas_negocio se toca): error
          de la tool con el mensaje genérico, que no nombra la clave; un
          [AVISO] en stderr que sí la nombra; ningún log de configuración;
          y, restaurada la clave, la tool vuelve a responder igual.

No crea ni borra ninguna fila: la tool es de solo lectura y no necesita
datos de prueba. Sale con código 1 si algo falla.
"""

# asyncio: fastmcp.Client es asíncrono; asyncio.run ejecuta sus pruebas.
import asyncio
# contextlib.redirect_stderr: captura lo que el servidor escribe en stderr.
import contextlib
# io.StringIO: un "archivo" en memoria donde queda lo capturado.
import io
# re: para buscar claves prohibidas por su nombre.
import re
# sys: ruta del paquete "app", salida en UTF-8 y código de salida.
import sys
# threading: el servidor corre en un hilo de este mismo proceso.
import threading
# time: para esperar a que el servidor arranque.
import time
# Decimal y ROUND_HALF_UP: la cuenta del recargo con IVA, hecha aquí aparte.
from decimal import ROUND_HALF_UP, Decimal
# Path: para calcular la raíz del repositorio desde este archivo.
from pathlib import Path

# El paquete "app" está en la raíz del repositorio. En una copia de prueba
# en negativo, parents[1] es la carpeta de la copia, y se importa SU app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Se pone la primera de la lista de búsqueda de módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes y eñes en la consola de Windows sin UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: la conexión propia del script, para leer lo esperado.
import psycopg2
# requests: las peticiones HTTP a pelo del CASO 1.
import requests
# uvicorn: el servidor real, en un hilo.
import uvicorn
# El cliente MCP de fastmcp (el mismo protocolo que el nodo de n8n).
from fastmcp import Client
# El transporte HTTP del cliente, con el token Bearer.
from fastmcp.client.transports import StreamableHttpTransport
# El error que lanza el cliente cuando la tool falla.
from fastmcp.exceptions import ToolError

# La cadena de conexión y los tres secretos (del .env).
from app.config import DATABASE_URL, GATE_SECRET, MCP_SECRET, WEBHOOK_SECRET
# El módulo del servidor MCP: su mensaje de error genérico (CASO 6).
from app.mcp_server.server import MENSAJE_REGLAS_NO_DISPONIBLES
# El módulo del servicio: el CASO 6 cambia su clave del recargo en memoria.
from app.services import business_rules_service as servicio

# Puerto propio (nunca el 8000).
PUERTO = 8029
# La dirección base del servidor de prueba.
BASE = f"http://127.0.0.1:{PUERTO}"
# La ruta de la puerta MCP.
URL_MCP = f"{BASE}/mcp/"
# El nombre de la tool que se prueba.
TOOL = "get_business_rules"

# El primer mensaje del protocolo MCP, para las pruebas HTTP a pelo.
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}},
}
# La cabecera Accept que exige el transporte HTTP de MCP.
CABECERAS_MCP = {"Accept": "application/json, text/event-stream"}

# Claves exactas de cada nivel de la respuesta.
CLAVES_RAIZ = {"visita", "recargo_informe_tecnico_con_iva", "incluye_tipico"}
# Las de "visita".
CLAVES_VISITA = {"dias", "manana", "tarde", "duracion_min"}
# Las de cada franja.
CLAVES_FRANJA = {"inicio", "fin"}
# Las de cada entrada de incluye_tipico.
CLAVES_INCLUYE = {"tipo_reforma", "nivel_acabados", "incluye_tipico"}
# Una clave con alguna de estas palabras no debe aparecer en ningún nivel.
PROHIBIDAS = re.compile(r"precio|margen|umbral|pct|porcentaje|tarifa")

# Contadores: comprobaciones correctas y lista de fallos.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Imprime [OK] o [FALLO] y lo cuenta."""
    # global: se cambia el contador de fuera de la función.
    global ok
    # Si la condición se cumple, cuenta como correcta.
    if condicion:
        # Una correcta más.
        ok += 1
        # Se enseña con su detalle.
        print(f"    [OK]    {titulo}  {detalle}")
    # Si no, se anota el fallo.
    else:
        # Para el resumen final.
        fallos.append(f"{titulo} {detalle}")
        # Y en su sitio.
        print(f"    [FALLO] {titulo}  {detalle}")


def hhmm(minutos):
    """Minutos desde medianoche -> "HH:MM", escrito aquí aparte."""
    # Horas y minutos con dos cifras cada uno.
    return f"{int(minutos) // 60:02d}:{int(minutos) % 60:02d}"


def leer_esperado():
    """
    Lee de la base de datos, con una conexión propia, todo lo que la tool
    debería devolver y todo lo que NUNCA debería devolver.
    """
    # Conexión propia; se cierra al terminar.
    cn = psycopg2.connect(DATABASE_URL)
    # try / finally: la conexión se cierra siempre.
    try:
        # El cursor, cerrado al salir.
        with cn.cursor() as cur:
            # SQL: todas las reglas de una vez, como diccionario clave -> valor.
            cur.execute("SELECT clave, valor FROM reglas_negocio;")
            # Diccionario con todas las filas.
            reglas = dict(cur.fetchall())
            # SQL: tarifas_base completa, en el orden de la tool.
            cur.execute("SELECT tipo_reforma, nivel_acabados, incluye_tipico, precio_m2 "
                        "FROM tarifas_base ORDER BY tipo_reforma, nivel_acabados;")
            # Lista de tuplas.
            tarifas = cur.fetchall()
            # SQL: los umbrales del Gate.
            cur.execute("SELECT umbral FROM umbrales_gate;")
            # Lista de umbrales.
            umbrales = [f[0] for f in cur.fetchall()]
    # Se cierra la conexión.
    finally:
        # Solo lectura: no hay nada que confirmar.
        cn.close()
    # El recargo con IVA, con la cuenta de D14 escrita aquí: base x (1 + iva/100),
    # redondeado a céntimos con ROUND_HALF_UP.
    con_iva = (reglas["recargo_informe_tecnico"] * (1 + reglas["iva_estandar_pct"] / Decimal(100))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    # La respuesta esperada, con la misma forma que el JSON de la tool.
    esperado = {
        "visita": {
            "dias": "de lunes a viernes",
            "manana": {"inicio": hhmm(reglas["visita_manana_inicio_min"]), "fin": hhmm(reglas["visita_manana_fin_min"])},
            "tarde": {"inicio": hhmm(reglas["visita_tarde_inicio_min"]), "fin": hhmm(reglas["visita_tarde_fin_min"])},
            "duracion_min": int(reglas["duracion_visita_min"]),
        },
        "recargo_informe_tecnico_con_iva": con_iva,
        "incluye_tipico": [
            {"tipo_reforma": t, "nivel_acabados": n, "incluye_tipico": i} for t, n, i, _ in tarifas
        ],
    }
    # Los valores que NUNCA deben salir: precios por metro, margen, IVA y umbrales.
    prohibidos = {p for *_, p in tarifas} | {reglas["margen_empresa_pct"], reglas["iva_estandar_pct"]} | set(umbrales)
    # Se devuelven los dos.
    return esperado, prohibidos


def hojas(objeto, ruta=""):
    """Recorre el JSON y devuelve (ruta, clave, valor) de cada valor final."""
    # La lista que se va llenando.
    salida = []
    # Un diccionario: se baja por cada clave.
    if isinstance(objeto, dict):
        # Cada clave y su valor.
        for clave, valor in objeto.items():
            # Se anota la clave (para el control de nombres) con su valor.
            salida.append((f"{ruta}.{clave}", clave, valor))
            # Y se sigue bajando.
            salida += hojas(valor, f"{ruta}.{clave}")
    # Una lista: se baja por cada elemento.
    elif isinstance(objeto, list):
        # Cada elemento con su posición.
        for i, valor in enumerate(objeto):
            # Sin clave: solo el valor.
            salida.append((f"{ruta}[{i}]", None, valor))
            # Y se sigue bajando.
            salida += hojas(valor, f"{ruta}[{i}]")
    # Se devuelve la lista.
    return salida


def como_decimal(valor):
    """Un número o un texto numérico como Decimal; None si no lo es."""
    # bool es un int en Python: no es un importe.
    if isinstance(valor, bool):
        # No cuenta.
        return None
    # try: Decimal("08:30") o Decimal("texto") fallan.
    try:
        # str() primero, para no arrastrar errores de float.
        return Decimal(str(valor))
    # No era un número.
    except Exception:
        # No cuenta.
        return None


def normalizar(respuesta):
    """La respuesta con el recargo como Decimal, para compararla entera."""
    # Copia superficial del diccionario.
    copia = dict(respuesta)
    # El recargo puede llegar como texto o como número: a Decimal.
    copia["recargo_informe_tecnico_con_iva"] = como_decimal(copia.get("recargo_informe_tecnico_con_iva"))
    # Se devuelve la copia.
    return copia


async def pruebas_mcp(esperado, prohibidos):
    """Los CASOS 2 a 6, con el protocolo MCP completo."""
    # El transporte con el token Bearer correcto.
    transporte = StreamableHttpTransport(URL_MCP, auth=MCP_SECRET)
    # "async with": el cliente se conecta y se desconecta solo.
    async with Client(transporte) as client:
        # ------------------------------------------------------------
        print("\nCASO 2 - list_tools")
        # Las tools publicadas, por nombre.
        tools = {t.name: t for t in await client.list_tools()}
        # Exactamente las tres (D32 añade consultar_huecos_libres).
        comprobar("exactamente calculate_estimate, get_business_rules y consultar_huecos_libres",
                  set(tools) == {"calculate_estimate", TOOL, "consultar_huecos_libres"}, f"({sorted(tools)})")
        # El esquema de entrada de la tool nueva (vacío si no está).
        entrada = tools[TOOL].input_schema if TOOL in tools else {}
        # Se enseña tal cual.
        print(f"      esquema de entrada: {entrada}")
        # Sin ningún parámetro.
        comprobar("get_business_rules no tiene parámetros", not entrada.get("properties"), f"({entrada.get('properties')})")

        # ------------------------------------------------------------
        print("\nCASO 3 - Llamada: claves exactas y valores de la base de datos")
        # La llamada; structured_content es el JSON de la respuesta.
        r = await client.call_tool(TOOL, {})
        # El JSON.
        sc = r.structured_content
        # Se enseña entero.
        print(f"      {sc}")
        # Claves de la raíz.
        comprobar("claves exactas de la raíz", set(sc) == CLAVES_RAIZ, f"({sorted(sc)})")
        # La parte de la visita (o {} si falta).
        visita = sc.get("visita") or {}
        # Claves de la visita.
        comprobar("claves exactas de visita", set(visita) == CLAVES_VISITA, f"({sorted(visita)})")
        # Claves de cada franja.
        comprobar("claves exactas de las dos franjas",
                  all(set(visita.get(f) or {}) == CLAVES_FRANJA for f in ("manana", "tarde")))
        # La lista de incluye_tipico.
        incluye = sc.get("incluye_tipico") or []
        # Claves de cada entrada.
        comprobar(f"claves exactas en las {len(incluye)} entradas de incluye_tipico",
                  bool(incluye) and all(set(e) == CLAVES_INCLUYE for e in incluye))
        # La visita, igual a la de la base de datos.
        comprobar("visita = reglas de reglas_negocio (franjas en HH:MM y duración)",
                  visita == esperado["visita"], f"({visita} frente a {esperado['visita']})")
        # El recargo con IVA, igual a la cuenta hecha aquí.
        recargo = como_decimal(sc.get("recargo_informe_tecnico_con_iva"))
        # Comparación de Decimal.
        comprobar("recargo_informe_tecnico_con_iva = recargo x (1 + IVA/100), a céntimos",
                  recargo == esperado["recargo_informe_tecnico_con_iva"],
                  f"({sc.get('recargo_informe_tecnico_con_iva')!r} frente a {esperado['recargo_informe_tecnico_con_iva']})")
        # incluye_tipico, igual a tarifas_base en el mismo orden.
        comprobar(f"incluye_tipico = las {len(esperado['incluye_tipico'])} filas de tarifas_base, en orden",
                  incluye == esperado["incluye_tipico"])

        # ------------------------------------------------------------
        print("\nCASO 4 - Nada prohibido (C4)")
        # Todas las claves y valores de la respuesta.
        todas = hojas(sc)
        # Claves con un nombre prohibido.
        malas = [ruta for ruta, clave, _ in todas if clave and PROHIBIDAS.search(clave)]
        # Ninguna.
        comprobar("ninguna clave de precio, margen, umbral, porcentaje ni tarifa", not malas, f"({malas})")
        # Valores numéricos iguales a uno prohibido.
        iguales = [(ruta, v) for ruta, _, v in todas
                   if not isinstance(v, (dict, list)) and como_decimal(v) in prohibidos]
        # Ninguno.
        comprobar(f"ningún valor igual a un precio_m2, al margen, al IVA o a un umbral ({len(prohibidos)} valores)",
                  not iguales, f"({iguales})")

        # ------------------------------------------------------------
        print("\nCASO 5 - Segunda llamada")
        # Otra llamada igual.
        r2 = await client.call_tool(TOOL, {})
        # La misma respuesta.
        comprobar("misma respuesta", r2.structured_content == sc)

        # ------------------------------------------------------------
        print("\nCASO 6 - Configuración rota en memoria")
        # El id de log más alto antes, para ver que no se escribe nada.
        cn = psycopg2.connect(DATABASE_URL)
        # El cursor de la comprobación.
        cur = cn.cursor()
        # SQL: el id máximo de logs.
        cur.execute("SELECT coalesce(max(id), 0) FROM logs;")
        # Se guarda.
        id_base = cur.fetchone()[0]
        # Se cierra la transacción de lectura.
        cn.commit()
        # La clave original, para restaurarla.
        original = servicio.CLAVE_RECARGO_LEIDA
        # Una clave que no existe en reglas_negocio.
        clave_rota = "recargo_informe_tecnico_no_existe"
        # Lo que escriba el servidor en stderr.
        buffer = io.StringIO()
        # try / finally: la clave original vuelve SIEMPRE.
        try:
            # El cambio en memoria: el servidor (mismo proceso) lo ve al instante.
            servicio.CLAVE_RECARGO_LEIDA = clave_rota
            # Se captura stderr durante la llamada.
            with contextlib.redirect_stderr(buffer):
                # try / except / else: la rama else marca FALLO si NO sale el error.
                try:
                    # Debe fallar.
                    await client.call_tool(TOOL, {})
                # Sale: se comprueba el mensaje.
                except ToolError as error:
                    # Exactamente el mensaje genérico.
                    comprobar("error de la tool con el mensaje genérico",
                              str(error) == MENSAJE_REGLAS_NO_DISPONIBLES, f"({error})")
                    # Que no nombra la clave.
                    comprobar("el mensaje no nombra la clave", clave_rota not in str(error))
                # No sale: la tool ha respondido con la regla rota.
                else:
                    # FALLO explícito.
                    comprobar("error de la tool con la clave rota", False, "(la excepción se ha tragado)")
        # Se restaura la clave.
        finally:
            # Siempre.
            servicio.CLAVE_RECARGO_LEIDA = original
        # Se enseña lo capturado.
        for linea in buffer.getvalue().splitlines():
            # Línea a línea.
            print(f"      stderr> {linea}")
        # Un [AVISO] que nombra la clave rota.
        comprobar("sale un [AVISO] en stderr que nombra la clave",
                  any(l.startswith("[AVISO]") and clave_rota in l for l in buffer.getvalue().splitlines()))
        # SQL: logs de configuración nuevos desde id_base (la marca es la
        # acción, combinada con id > id_base, regla de CLAUDE.md).
        cur.execute("SELECT count(*) FROM logs WHERE id > %s AND accion ILIKE %s;", (id_base, "%configuracion%"))
        # El recuento.
        nuevos = cur.fetchone()[0]
        # Se cierra la conexión.
        cn.close()
        # Ninguno.
        comprobar("ningún log de configuración nuevo", nuevos == 0, f"({nuevos})")
        # Restaurada, vuelve la misma respuesta.
        r3 = await client.call_tool(TOOL, {})
        # Igual que en el CASO 3.
        comprobar("restaurada la clave, la misma respuesta que en el CASO 3", r3.structured_content == sc)


# Lo esperado y lo prohibido, leídos ANTES de arrancar el servidor.
ESPERADO, PROHIBIDOS = leer_esperado()
# Se enseña lo esperado.
print("=" * 78)
# La cabecera.
print("LO ESPERADO (leído de la base de datos por este script)")
# La visita.
print(f"  visita: {ESPERADO['visita']}")
# El recargo.
print(f"  recargo con IVA: {ESPERADO['recargo_informe_tecnico_con_iva']}")
# Cuántas filas.
print(f"  incluye_tipico: {len(ESPERADO['incluye_tipico'])} filas; valores prohibidos: {len(PROHIBIDOS)}")

# El servidor real, sin --reload, en el puerto propio.
servidor = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning"))
# En un hilo daemon: si el script termina, el hilo no lo retiene.
hilo = threading.Thread(target=servidor.run, daemon=True)
# Arranca.
hilo.start()
# Hora de inicio de la espera.
inicio = time.time()
# Hasta 30 s esperando a /health.
while time.time() - inicio < 30:
    # try: mientras arranca, la conexión se rechaza.
    try:
        # Arriba: se sale del bucle.
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            # Listo.
            break
    # Todavía no responde.
    except requests.exceptions.RequestException:
        # Se espera un poco.
        time.sleep(0.25)
# El bucle terminó sin break: no arrancó.
else:
    # Se avisa.
    print("  El servidor no respondió en 30 s")
    # Y se sale con error.
    sys.exit(1)
# Arriba.
print(f"\n  Servidor arriba en el puerto {PUERTO} en {time.time() - inicio:.2f} s")

# try / finally: el servidor se detiene siempre.
try:
    # ------------------------------------------------------------------
    print("\nCASO 1 - Autenticación de /mcp (HTTP a pelo)")
    # Cada caso: título, cabeceras y código esperado.
    for titulo, cabeceras, esperado in [
        ("sin cabecera Authorization", {}, 401),
        ("WEBHOOK_SECRET como token", {"Authorization": f"Bearer {WEBHOOK_SECRET}"}, 401),
        ("GATE_SECRET como token (el Agente 2 nunca lo lleva)", {"Authorization": f"Bearer {GATE_SECRET}"}, 401),
        ("MCP_SECRET correcto", {"Authorization": f"Bearer {MCP_SECRET}"}, 200),
    ]:
        # El mensaje initialize con esas cabeceras.
        r = requests.post(URL_MCP, json=INITIALIZE, headers={**CABECERAS_MCP, **cabeceras}, timeout=10)
        # El código esperado.
        comprobar(f"{titulo} -> {esperado}", r.status_code == esperado, f"({r.status_code})")

    # Los CASOS 2 a 6.
    asyncio.run(pruebas_mcp(ESPERADO, PROHIBIDOS))

# El servidor se detiene siempre.
finally:
    # Le pide a uvicorn que termine.
    servidor.should_exit = True
    # Y espera al hilo.
    hilo.join(timeout=15)

# Resumen: total y fallos.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
# Separador.
print("=" * 78)
# Cada fallo.
for f in fallos:
    # Con su detalle.
    print(f"  FALLO: {f}")
# Código de salida: 0 sin fallos, 1 con alguno.
sys.exit(0 if not fallos else 1)
