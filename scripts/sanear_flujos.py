# =============================================================================
# scripts/sanear_flujos.py: sanea los flujos exportados de n8n para n8n/
# =============================================================================
# Qué hace: lee los JSON que exporta la CLI de n8n (n8n export:workflow, en
# entrega_28-11/flujos/, carpeta que NUNCA se sube) y escribe en n8n/ una copia
# saneada con la convención de n8n/README.md ("Qué se cambia al sanear"):
#   - dominio de ngrok        -> TU-DOMINIO.ngrok-free.dev
#   - chatId de Telegram      -> CHAT_ID_TECNICO (WF4) o CHAT_ID_ADMINISTRACION
#   - emails reales           -> admin@example.com
#   - ID del calendario (WF5) -> PEGA_AQUI_ID_DEL_CALENDARIO
#   - webhookId de cada nodo  -> se borra la clave
#   - "id" de cada credencial -> se borra (quedan el tipo y el "name"; anexo C
#     de la memoria: "sin ids de credenciales")
#   - meta, versionId, shared y demás claves de la CLI -> fuera (solo quedan
#     las 8 claves del formato de "Download")
#   - active -> false; pinData -> {}
# Los flujos retirados de n8n/historico/ (ya saneados, no se exportan) solo
# pierden los ids de credenciales, en el mismo archivo.
# Después COMPRUEBA cada archivo saneado. Si queda cualquier dato sensible, para
# con código 1 y NO escribe ningún archivo (todos o ninguno).
#
# Uso (Git Bash, desde la raíz del repositorio):
#   PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/sanear_flujos.py
# Nunca imprime un dato real: solo recuentos.
# =============================================================================
import json
import re
import sys
from pathlib import Path

# La raíz del repositorio: dos niveles por encima de este archivo.
RAIZ = Path(__file__).resolve().parents[1]
# De dónde se leen las exportaciones (sin sanear) y dónde se escriben.
ENTRADA = RAIZ / "entrega_28-11" / "flujos"
SALIDA = RAIZ / "n8n"
# Los flujos retirados: no se exportan, solo se les quitan los ids de credenciales.
HISTORICO = SALIDA / "historico"

# Las 8 claves del formato de n8n/, en su orden (el de "Download" de n8n).
CLAVES = ["name", "nodes", "pinData", "connections", "active", "settings", "id", "tags"]

# Los marcadores que sustituyen a los datos reales (tabla de n8n/README.md).
DOMINIO_FALSO = "TU-DOMINIO.ngrok-free.dev"
EMAIL_FALSO = "admin@example.com"
CALENDARIO_FALSO = "PEGA_AQUI_ID_DEL_CALENDARIO"
CHAT_TECNICO = "CHAT_ID_TECNICO"
CHAT_ADMIN = "CHAT_ID_ADMINISTRACION"

# Patrones de búsqueda.
# Un dominio de ngrok (cualquier subdominio).
PATRON_NGROK = re.compile(r"[\w-]+\.ngrok(?:-free)?\.(?:dev|app|io)")
# El ID de un calendario de Google (tiene forma de email: va ANTES que los emails).
PATRON_CALENDARIO = re.compile(r"[\w.-]+@group\.calendar\.google\.com")
# Un email cualquiera.
PATRON_EMAIL = re.compile(r"[\w.+%-]+@[\w-]+(?:\.[\w-]+)+")
# Dominios de email ficticios que se conservan (ejemplos de los prompts y pruebas).
DOMINIOS_FICTICIOS = re.compile(r"@(example\.(com|org|net|invalid)|dominio\.com)$", re.IGNORECASE)


def cambiar_email(m: re.Match) -> str:
    """El email encontrado se queda si es ficticio; si no, pasa a admin@example.com."""
    # m.group(0) es el texto completo que ha encajado con el patrón.
    return m.group(0) if DOMINIOS_FICTICIOS.search(m.group(0)) else EMAIL_FALSO


def recorrer(valor, funcion):
    """Devuelve una copia de valor con funcion aplicada a cada texto, a cualquier profundidad."""
    # Un diccionario: se recorren sus valores (las claves no cambian).
    if isinstance(valor, dict):
        return {k: recorrer(v, funcion) for k, v in valor.items()}
    # Una lista: se recorre cada elemento.
    if isinstance(valor, list):
        return [recorrer(v, funcion) for v in valor]
    # Un texto: aquí se sanea.
    if isinstance(valor, str):
        return funcion(valor)
    # Números, booleanos y None se quedan como están.
    return valor


def quitar_ids_credenciales(nodos) -> list:
    """Los nodos con cada credencial reducida a su "name" (el tipo es la clave que la contiene)."""
    limpios = []
    for nodo in nodos:
        # dict(nodo): una copia, para no cambiar el original.
        nodo = dict(nodo)
        if "credentials" in nodo:
            # {tipo: {"id": ..., "name": ...}} -> {tipo: {"name": ...}}
            nodo["credentials"] = {
                tipo: {k: v for k, v in datos.items() if k != "id"}
                for tipo, datos in nodo["credentials"].items()
            }
        limpios.append(nodo)
    return limpios


def ids_de_credenciales(flujo: dict) -> int:
    """Cuántas credenciales conservan todavía un "id" (tiene que ser 0)."""
    return sum(
        1
        for nodo in flujo["nodes"]
        for datos in nodo.get("credentials", {}).values()
        if isinstance(datos, dict) and "id" in datos
    )


def chat_ids(nodos) -> set[str]:
    """Los valores reales de chatId de los nodos (para buscarlos también en el código)."""
    encontrados = set()
    for nodo in nodos:
        # El chatId de un nodo de Telegram va en sus parámetros.
        valor = nodo.get("parameters", {}).get("chatId")
        # Solo un valor real (texto o número), nunca un marcador ya saneado.
        if valor not in (None, "", CHAT_TECNICO, CHAT_ADMIN):
            encontrados.add(str(valor))
    return encontrados


def sanear(flujo: dict) -> tuple[dict, set[str]]:
    """El flujo saneado, y los chatId reales que tenía (para la comprobación final)."""
    # WF4 avisa al grupo técnico; los demás, a administración (n8n/README.md).
    marcador_chat = CHAT_TECNICO if flujo["name"].startswith("WF4") else CHAT_ADMIN
    # Los chatId reales de este flujo.
    reales = chat_ids(flujo["nodes"])

    def limpiar(texto: str) -> str:
        """Todas las sustituciones de un texto, en orden."""
        # Primero el calendario (tiene forma de email), después el dominio y los emails.
        texto = PATRON_CALENDARIO.sub(CALENDARIO_FALSO, texto)
        texto = PATRON_NGROK.sub(DOMINIO_FALSO, texto)
        texto = PATRON_EMAIL.sub(cambiar_email, texto)
        # Cada chatId real, en cualquier sitio (parámetros o código).
        for real in reales:
            texto = texto.replace(real, marcador_chat)
        return texto

    # Los nodos sin la clave webhookId (n8n crea otro al importar) y sin los
    # ids de sus credenciales.
    nodos = quitar_ids_credenciales([{k: v for k, v in nodo.items() if k != "webhookId"} for nodo in flujo["nodes"]])
    # Un chatId numérico (no texto) no lo ve limpiar: se cambia aquí, en los parámetros.
    for nodo in nodos:
        parametros = nodo.get("parameters", {})
        if str(parametros.get("chatId")) in reales:
            parametros["chatId"] = marcador_chat
    # El flujo con solo las 8 claves, en su orden, y los valores fijos.
    limpio = {clave: flujo.get(clave) for clave in CLAVES}
    limpio["nodes"] = nodos
    limpio["active"] = False
    limpio["pinData"] = {}
    # Sin etiquetas si no las había (la CLI puede dar None).
    limpio["tags"] = flujo.get("tags") or []
    # Las sustituciones de texto, en todo el flujo.
    return recorrer(limpio, limpiar), reales


def problemas(texto: str, reales: set[str]) -> list[str]:
    """Lo que NO puede quedar en un archivo saneado (solo nombres, nunca el dato)."""
    lista = []
    # Ningún webhookId, instanceId, versionId ni shared.
    for clave in ('"webhookId"', '"instanceId"', '"versionId"', '"activeVersionId"', '"shared"'):
        if clave in texto:
            lista.append(f"clave {clave}")
    # Ningún dominio de ngrok que no sea el marcador.
    if any(d != DOMINIO_FALSO for d in PATRON_NGROK.findall(texto)):
        lista.append("dominio de ngrok real")
    # Ningún email real.
    if any(not DOMINIOS_FICTICIOS.search(e) for e in PATRON_EMAIL.findall(texto)):
        lista.append("email real")
    # Ningún calendario real ni chatId real.
    if PATRON_CALENDARIO.search(texto):
        lista.append("ID de calendario")
    if any(r in texto for r in reales):
        lista.append("chatId real")
    # Ningún id de grupo de Telegram (-100 y diez cifras), token JWT (eyJ) ni clave sk-.
    if re.search(r"-100\d{10}", texto):
        lista.append("id de grupo de Telegram")
    if re.search(r"eyJ[\w-]{10,}|\bsk-[A-Za-z0-9]{10,}", texto):
        lista.append("token o clave")
    # Ningún "id" dentro de "credentials" (se mira en el JSON, no en el texto,
    # porque "id" aparece en muchos sitios legítimos: nodos, conexiones...).
    datos = json.loads(texto)
    flujo = datos[0] if isinstance(datos, list) else datos
    if ids_de_credenciales(flujo):
        lista.append(f"{ids_de_credenciales(flujo)} id(s) de credenciales")
    return lista


def main() -> int:
    """Sanea todos, comprueba todos y solo entonces escribe. Devuelve el código de salida."""
    archivos = sorted(ENTRADA.glob("*.json"))
    if not archivos:
        print(f"[FALLO] no hay exportaciones en {ENTRADA}")
        return 1
    preparados = []
    fallos = 0
    for archivo in archivos:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        # La CLI exporta una lista con un flujo; "Download", el flujo solo.
        flujo = datos[0] if isinstance(datos, list) else datos
        limpio, reales = sanear(flujo)
        # El texto final: el mismo formato que los archivos de n8n/ (sangría 2,
        # tildes tal cual y salto de línea al final).
        texto = json.dumps(limpio, indent=2, ensure_ascii=False) + "\n"
        encontrados = problemas(texto, reales)
        if encontrados:
            fallos += 1
            print(f"[FALLO] {archivo.name}: queda {', '.join(encontrados)}")
        else:
            print(f"[OK]    {archivo.name}: {len(limpio['nodes'])} nodos, {len(reales)} chatId sustituido(s)")
        preparados.append((SALIDA / archivo.name, texto))
    # Los retirados de historico/: solo se quitan los ids de credenciales, y
    # pasan por la misma comprobación.
    for archivo in sorted(HISTORICO.glob("*.json")):
        flujo = json.loads(archivo.read_text(encoding="utf-8"))
        flujo["nodes"] = quitar_ids_credenciales(flujo["nodes"])
        texto = json.dumps(flujo, indent=2, ensure_ascii=False) + "\n"
        encontrados = problemas(texto, set())
        if encontrados:
            fallos += 1
            print(f"[FALLO] historico/{archivo.name}: queda {', '.join(encontrados)}")
        else:
            print(f"[OK]    historico/{archivo.name}: {len(flujo['nodes'])} nodos (solo ids de credenciales)")
        preparados.append((archivo, texto))
    # Todos o ninguno: con un solo fallo no se escribe nada.
    if fallos:
        print(f"\n{fallos} archivo(s) con datos sensibles: NO se ha escrito nada en {SALIDA}.")
        return 1
    for destino, texto in preparados:
        # newline="\n": saltos de línea LF también en Windows, como el resto de n8n/.
        destino.write_text(texto, encoding="utf-8", newline="\n")
    print(f"\n{len(preparados)} archivo(s) escritos en {SALIDA}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
