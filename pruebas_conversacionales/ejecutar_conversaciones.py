# =============================================================================
# ejecutar_conversaciones.py — Suite de evaluación del chat (TFM reformas).
# Envía conversaciones guionizadas al chat PUBLICADO de n8n, turno a turno,
# recoge cada respuesta y su tiempo, aplica comprobaciones automáticas y
# guarda un CSV y una transcripción en Markdown.
# Protocolo (código fuente de @n8n/chat, etiqueta n8n@2.16.1):
#   1) POST {webhook}/chat  {"action":"sendMessage","sessionId","chatInput"}
#      -> responde {"executionStarted": true, "executionId": "..."}
#   2) WebSocket ws://host/chat?sessionId=..&executionId=..&isPublic=true
#      -> llegan "n8n|heartbeat" (se contesta "n8n|heartbeat-ack"),
#         "n8n|continue" o el texto del mensaje del bot; al terminar, se cierra.
# Uso (Git Bash, en la raíz del proyecto):
#   ./venv/Scripts/python.exe pruebas_conversacionales/ejecutar_conversaciones.py \
#       --url http://localhost:5678/webhook/<webhookId> \
#       --casos pruebas_conversacionales/casos_174.json --salida pruebas_conversacionales/resultados
# =============================================================================

import argparse            # leer los parámetros de la línea de comandos
import asyncio             # el cliente WebSocket es asíncrono
import csv                 # escribir el CSV de resultados
import json                # leer los casos y enviar/recibir JSON
import re                  # comprobaciones con expresiones regulares
import time                # medir la duración de cada turno
import urllib.request      # el POST, con la librería estándar (sin instalar nada)
import uuid                # una sesión nueva y única por conversación
from datetime import datetime          # sello de fecha para los archivos de salida
from pathlib import Path               # rutas de archivos independientes del sistema
from urllib.parse import urlencode, urlparse   # urlencode: consulta escapada ("#" -> %23); urlparse: host y puerto del webhook

import websockets          # cliente WebSocket (viene con uvicorn[standard]; si falta: pip install websockets)

LATIDO = "n8n|heartbeat"               # señal de vida que manda n8n (protocolo de 2.16.1)
LATIDO_ACK = "n8n|heartbeat-ack"       # respuesta obligatoria al latido
SIGUE = "n8n|continue"                 # n8n avisa de que la ejecución continúa
MARCA_RESUMEN = "Antes de guardar nada"   # frase fija con la que empieza el resumen (pintar_resumen)

# Comprobaciones que valen para TODAS las respuestas del Agente 1 (críticos y calidad).
GLOBALES = [
    ("C8_codigo_interno", r"\b(bano|integral_vivienda|parcial_acabados|nivel_acabados|tipo_reforma|true|false)\b|[{}]"),  # códigos o JSON
    ("C2_cifra_euros", r"\d[\d.,]*\s?(€|euros)"),          # una cifra en euros antes de guardar
    ("Q_relleno_inicial", r"^(Perfecto|Genial|Estupendo)\b"),  # muletilla al empezar
    ("Q_distorsion_tabiques", r"no (hay|habría) tabiques"),    # cambia el sentido de "sin tabiques"
    ("Q_oferta_vacia", r"orientaci[oó]n general|dudas generales"),  # ofrece algo que no hace
]


def post_mensaje(url_webhook: str, sesion: str, texto: str) -> str:
    """Envía un mensaje al chat y devuelve el executionId."""
    cuerpo = json.dumps({"action": "sendMessage", "sessionId": sesion, "chatInput": texto}).encode("utf-8")  # el JSON que manda el widget
    peticion = urllib.request.Request(url_webhook.rstrip("/") + "/chat", data=cuerpo,
                                      headers={"Content-Type": "application/json"}, method="POST")  # POST a {webhook}/chat
    with urllib.request.urlopen(peticion, timeout=30) as resp:          # espera como mucho 30 s a que n8n acepte
        datos = json.loads(resp.read().decode("utf-8"))                 # {"executionStarted": true, "executionId": "..."}
    return str(datos["executionId"])                                     # el id para abrir el WebSocket


async def recibir_respuesta(url_webhook: str, sesion: str, ejecucion: str, espera_max: float) -> list[str]:
    """Abre el WebSocket de la ejecución y junta los mensajes del bot hasta que n8n cierra."""
    p = urlparse(url_webhook)                                            # separa esquema, host y puerto
    esquema = "wss" if p.scheme == "https" else "ws"                     # http -> ws, https -> wss
    consulta = urlencode({"sessionId": sesion, "executionId": ejecucion, "isPublic": "true"})  # escapa "#" (H-175-4): si no, corta la URL
    url_ws = f"{esquema}://{p.netloc}/chat?{consulta}"                   # como el widget oficial
    mensajes: list[str] = []                                             # aquí van los textos del bot
    async with websockets.connect(url_ws) as ws:                         # conecta al canal de esa ejecución
        fin = time.monotonic() + espera_max                              # límite de tiempo del turno
        while time.monotonic() < fin:                                    # hasta que n8n cierre o se agote el tiempo
            try:
                dato = await asyncio.wait_for(ws.recv(), timeout=max(0.1, fin - time.monotonic()))  # espera un frame
            except (asyncio.TimeoutError, websockets.ConnectionClosed):  # tiempo agotado o canal cerrado
                break                                                    # se acaba el turno
            if dato == LATIDO:                                           # latido: hay que contestar
                await ws.send(LATIDO_ACK)                                # si no, n8n corta la conexión
                continue
            if dato == SIGUE:                                            # aviso interno, no es un mensaje
                continue
            mensajes.append(dato)                                        # texto del bot
            if len(mensajes) >= 1:                                       # nuestro flujo manda UN mensaje por turno
                break                                                    # no esperamos más (ahorra tiempo)
    return mensajes


def comprobar(texto: str, reglas: list[tuple[str, str]]) -> list[str]:
    """Devuelve los nombres de las reglas que se INCUMPLEN en un texto."""
    return [nombre for nombre, patron in reglas if re.search(patron, texto, flags=re.IGNORECASE | re.MULTILINE)]


def ejecutar_caso(url: str, caso: dict, espera_max: float) -> list[dict]:
    """Ejecuta una conversación completa en una sesión nueva y devuelve una fila por turno."""
    sesion = f"suite-{caso['id']}-{uuid.uuid4().hex[:8]}"                # sesión nueva y reconocible
    filas, ultima = [], ""                                               # resultados y última respuesta
    for n, mensaje in enumerate(caso["mensajes"], start=1):              # un mensaje por turno, en orden
        if mensaje.strip().lower() in ("no", "si", "sí") and MARCA_RESUMEN not in ultima:  # el guion espera un resumen
            filas.append({"caso": caso["id"], "turno": n, "mensaje": mensaje, "respuesta": "",
                          "segundos": "", "fallos": "PARADA: no hubo resumen antes de este mensaje"})  # se para y se anota
            break
        t0 = time.monotonic()                                            # empieza a contar el tiempo del turno
        try:
            ejecucion = post_mensaje(url, sesion, mensaje)               # envía el mensaje
            textos = asyncio.run(recibir_respuesta(url, sesion, ejecucion, espera_max))  # espera la respuesta
            respuesta = "\n".join(textos) if textos else "(SIN RESPUESTA)"  # une, o marca que no llegó nada
        except Exception as e:                                           # error de red o de n8n
            ejecucion, respuesta = "", f"(ERROR: {e})"                   # se anota y se sigue
        segundos = round(time.monotonic() - t0, 2)                       # duración del turno vista por el cliente
        es_resumen = MARCA_RESUMEN in respuesta                          # el resumen lo pinta el código, no el modelo
        es_estimacion = "Según los datos que nos has facilitado" in respuesta  # la cifra la escribe el código tras el "sí"
        excluir = ("C8_codigo_interno", "C2_cifra_euros") if (es_resumen or es_estimacion) else ()  # textos del código, no del modelo
        reglas = [r for r in GLOBALES if r[0] not in excluir]            # reglas que se aplican a esta respuesta
        fallos = comprobar(respuesta, reglas)                            # reglas globales incumplidas
        for exp in caso.get("esperado", []):                             # comprobaciones propias del caso
            if exp.get("turno") != n:                                    # solo las de este turno
                continue
            for patron in exp.get("contiene", []):                       # debe aparecer
                if not re.search(patron, respuesta, flags=re.IGNORECASE):
                    fallos.append(f"falta: {patron}")
            for patron in exp.get("no_contiene", []):                    # no debe aparecer
                if re.search(patron, respuesta, flags=re.IGNORECASE):
                    fallos.append(f"sobra: {patron}")
        filas.append({"caso": caso["id"], "turno": n, "mensaje": mensaje, "respuesta": respuesta,
                      "segundos": segundos, "ejecucion": ejecucion, "fallos": "; ".join(fallos)})  # fila del turno
        if respuesta.startswith("(ERROR") or respuesta == "(SIN RESPUESTA)":  # H-175-2: el turno no llegó a n8n o no contestó
            break                                                        # corta: más turnos solo gastarían ejecuciones
        ultima = respuesta                                               # para la regla de parada
    return filas


def main() -> None:
    ap = argparse.ArgumentParser(description="Suite de evaluación del chat de n8n")   # ayuda de la línea de comandos
    ap.add_argument("--url", required=True, help="URL del webhook del Chat Trigger, sin /chat")  # p. ej. http://localhost:5678/webhook/<id>
    ap.add_argument("--casos", required=True, help="JSON con la lista de conversaciones")      # archivo de casos
    ap.add_argument("--salida", default="pruebas_conversacionales/resultados", help="carpeta de resultados")
    ap.add_argument("--repeticiones", type=int, default=1, help="veces que se ejecuta cada caso")  # ×3 para los críticos
    ap.add_argument("--espera", type=float, default=60.0, help="segundos máximos por turno")
    args = ap.parse_args()                                               # lee los parámetros

    casos = json.loads(Path(args.casos).read_text(encoding="utf-8"))     # carga las conversaciones
    salida = Path(args.salida)                                           # carpeta de resultados
    salida.mkdir(parents=True, exist_ok=True)                            # la crea si no existe
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")                     # para no pisar resultados anteriores
    todas: list[dict] = []                                               # todas las filas

    for caso in casos:                                                   # cada conversación
        for r in range(1, args.repeticiones + 1):                        # y cada repetición
            c = dict(caso, id=f"{caso['id']}" + (f"#{r}" if args.repeticiones > 1 else ""))  # id con número de vuelta
            print(f"-> {c['id']}", flush=True)                           # progreso en pantalla
            filas = ejecutar_caso(args.url, c, args.espera)              # ejecuta la conversación
            for f in filas:                                              # muestra cada turno resumido
                marca = "OK " if not f["fallos"] else "XX "              # OK o fallo automático
                print(f"   {marca}t{f['turno']} {f['segundos']}s | {f['respuesta'][:90]!r} {f['fallos']}", flush=True)
            todas.extend(filas)                                          # acumula

    campos = ["caso", "turno", "mensaje", "respuesta", "segundos", "ejecucion", "fallos"]  # columnas del CSV
    with open(salida / f"resultados_{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig: Excel lo abre bien
        w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")  # escritor de CSV
        w.writeheader()                                                  # cabecera
        w.writerows(todas)                                               # filas

    with open(salida / f"transcripcion_{sello}.md", "w", encoding="utf-8") as fh:  # transcripción legible
        for f in todas:                                                  # una entrada por turno
            fh.write(f"### {f['caso']} · turno {f['turno']} · {f['segundos']} s\n")  # cabecera del turno
            fh.write(f"**Cliente:** {f['mensaje']}\n\n**Bot:** {f['respuesta']}\n\n")  # lo que se dijo
            if f["fallos"]:                                              # si hubo fallos automáticos
                fh.write(f"**Fallos automáticos:** {f['fallos']}\n\n")
    tiempos = sorted(float(f["segundos"]) for f in todas if f["segundos"] != "")  # tiempos válidos
    if tiempos:                                                          # resumen final en pantalla
        mediana = tiempos[len(tiempos) // 2]                             # mediana aproximada
        print(f"\nTurnos: {len(tiempos)} | mediana {mediana} s | máx {tiempos[-1]} s")
    print(f"Fallos automáticos: {sum(1 for f in todas if f['fallos'])} turnos | Resultados en {salida}")


if __name__ == "__main__":
    main()                                                               # punto de entrada
