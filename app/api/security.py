"""
Autenticación de los webhooks que llegan desde n8n.

Este archivo es parte de la capa de ADAPTADOR (app/api/), no de la
lógica de negocio: importa fastapi, y services/ nunca puede hacerlo.
Decidir si una petición HTTP puede entrar o no es un asunto del mundo
HTTP (cabeceras, códigos 401), no del dominio de las reformas.

Mecanismo elegido: un secreto compartido. n8n y FastAPI conocen el
mismo valor (WEBHOOK_SECRET en el .env); n8n lo manda en la cabecera
X-Webhook-Secret de cada petición y aquí se comprueba que coincide. Se
eligió por ser la opción de menor coste que cierra el riesgo real
(cualquiera que conociera la URL podía crear leads) y porque n8n
soporta de forma nativa la autenticación por cabecera ("Header Auth")
en sus nodos HTTP.

Desde el 2026-09-30 hay una SEGUNDA puerta con el mismo mecanismo:
POST /gate-decisions, con su propio secreto (GATE_SECRET) y su propia
cabecera (X-Gate-Secret). Este archivo comprueba además, al arrancar, que
los tres secretos del sistema (WEBHOOK_SECRET, MCP_SECRET y GATE_SECRET)
son distintos entre sí (plan de /gate-decisions, P4 y P10).
"""

import secrets

# HTTPException es la forma de decirle a FastAPI "corta aquí y responde
# con este código de error". Lanzarla dentro de una dependencia impide
# que la función del endpoint llegue a ejecutarse.
from fastapi import HTTPException, Security

# APIKeyHeader es una clase de FastAPI que sabe leer una cabecera
# concreta de la petición. Se usa en vez del Header() normal porque,
# además de leerla, la declara como ESQUEMA DE SEGURIDAD en la
# documentación automática: /docs muestra un botón "Authorize" donde se
# pega el secreto una vez y todas las pruebas manuales desde el
# navegador lo envían solas.
from fastapi.security import APIKeyHeader

# Los tres secretos: WEBHOOK_SECRET y GATE_SECRET se usan aquí para
# autenticar; MCP_SECRET solo se lee para comprobar al arrancar que no
# coincide con ninguno de los otros dos (su autenticación vive en
# app/mcp_server/server.py).
from app.config import GATE_SECRET, MCP_SECRET, WEBHOOK_SECRET

# Nombre de la cabecera. En una constante para que el endpoint, los
# scripts de verificación y la documentación usen exactamente el mismo
# texto, sin riesgo de que uno escriba "X-Webhook-Token" y otro
# "X-Webhook-Secret".
NOMBRE_CABECERA = "X-Webhook-Secret"

# Cabecera de la puerta del Gate (POST /gate-decisions). Mismo motivo para
# tenerla en una constante.
NOMBRE_CABECERA_GATE = "X-Gate-Secret"

# Cómo generar un secreto, para los mensajes de error de arranque. En una
# constante para no repetir el mismo texto en cuatro mensajes.
_COMO_GENERAR = (
    "Genera uno con:  python -c \"import secrets; "
    "print(secrets.token_urlsafe(32))\"  y ponlo en el .env. Ver .env.example."
)


# ---------------------------------------------------------------------
# Comprobación de arranque (fail fast)
# ---------------------------------------------------------------------
# Este bloque está a nivel de MÓDULO, fuera de cualquier función. Eso
# significa que Python lo ejecuta UNA vez, en el momento en que alguien
# importa este archivo. La cadena de imports del servidor es
# main.py -> api/leads.py -> api/security.py, así que si el secreto
# falta, uvicorn se detiene al cargar la aplicación, ANTES de abrir el
# puerto. El error aparece en el arranque, delante de quien lo arranca,
# y no más tarde, con la primera petición de n8n.
#
# Por qué aquí y no en app/config.py: config.py lo importan también
# scripts que no tienen nada que ver con webhooks (los check_*.py que
# solo necesitan DATABASE_URL). Poniendo la comprobación aquí, solo se
# le exige el secreto a quien de verdad lo usa.
#
# Por qué .strip(): un valor con solo espacios (WEBHOOK_SECRET="   ")
# pasaría un simple "if not WEBHOOK_SECRET", pero en la práctica es tan
# inútil como no tener secreto. strip() quita los espacios de los
# extremos; si no queda nada, se trata como ausente.
if WEBHOOK_SECRET is None or not WEBHOOK_SECRET.strip():
    # RuntimeError es la excepción estándar de Python para "el programa
    # no puede funcionar en este estado". El mensaje dice qué falta y
    # cómo arreglarlo, para que no haga falta buscar en el código.
    raise RuntimeError(
        "WEBHOOK_SECRET no está definido (o está vacío) en el .env. "
        "Es obligatorio: autentica las llamadas de n8n a POST /leads. "
        "Genera uno con:  python -c \"import secrets; "
        "print(secrets.token_urlsafe(32))\"  y añádelo al .env como "
        "WEBHOOK_SECRET=<valor>. Ver .env.example."
    )

# --- GATE_SECRET: obligatorio y no vacío (P4) ------------------------
# Mismo patrón y mismos motivos que la comprobación de WEBHOOK_SECRET de
# arriba. Se comprueba ANTES de compararlo con los demás: si no existe,
# el mensaje útil es "falta", no "coincide".
if GATE_SECRET is None or not GATE_SECRET.strip():
    raise RuntimeError(
        "GATE_SECRET no está definido (o está vacío) en el .env. "
        "Es obligatorio: autentica el formulario de n8n con el que "
        "administración registra las decisiones del Gate (POST "
        "/gate-decisions). Debe ser DISTINTO de WEBHOOK_SECRET y de "
        "MCP_SECRET. " + _COMO_GENERAR
    )

# --- Los tres secretos, distintos entre sí (P4 y P10) -----------------
# Por qué importa: cada secreto abre UNA puerta, y lo tiene un cliente
# distinto. WEBHOOK_SECRET lo tiene la herramienta HTTP del Agente 2 (un
# LLM). Si GATE_SECRET valiera lo mismo, el agente podría decidir su
# propio Gate, que es justo lo que D18 prohíbe; si MCP_SECRET valiera lo
# mismo que WEBHOOK_SECRET, quien tenga uno tendría las dos puertas.
#
# Los mensajes dicen QUÉ variables coinciden, pero NUNCA su valor: un
# error de arranque acaba en la consola y en los logs de uvicorn, y un
# secreto no debe aparecer ahí.
#
# Se compara con "==" y no con compare_digest: esto se ejecuta una sola
# vez, al arrancar, con valores del propio .env, y no hay ningún atacante
# midiendo tiempos. compare_digest solo tiene sentido cuando el valor
# comparado llega desde fuera, en una petición (ver _secreto_coincide).
if GATE_SECRET == WEBHOOK_SECRET:
    raise RuntimeError(
        "GATE_SECRET es igual a WEBHOOK_SECRET en el .env. Tienen que ser "
        "distintos: WEBHOOK_SECRET lo tiene la herramienta del Agente 2, y "
        "con el mismo valor el agente podría registrar decisiones del Gate. "
        + _COMO_GENERAR
    )

# MCP_SECRET puede ser None aquí (si falta, lo detecta después
# app/mcp_server/server.py con su propio mensaje). Comparar un texto con
# None da simplemente False, así que estas comprobaciones no fallan por
# eso: solo saltan cuando los dos valores existen y son iguales.
if GATE_SECRET == MCP_SECRET:
    raise RuntimeError(
        "GATE_SECRET es igual a MCP_SECRET en el .env. Tienen que ser "
        "distintos: MCP_SECRET lo tiene el agente de IA para la puerta "
        "/mcp. " + _COMO_GENERAR
    )

# P10: la comprobación que faltaba. El mensaje de error de server.py ya
# decía "Debe ser DISTINTO de WEBHOOK_SECRET", pero nadie lo comprobaba.
# Va aquí, junto a las de GATE_SECRET, para que TODAS las comparaciones
# entre secretos estén en un solo sitio. main.py importa este archivo
# (vía app/api/leads.py) antes que app/mcp_server/server.py, así que
# también se ejecuta al arrancar, antes de abrir el puerto.
if MCP_SECRET == WEBHOOK_SECRET:
    raise RuntimeError(
        "MCP_SECRET es igual a WEBHOOK_SECRET en el .env. Tienen que ser "
        "distintos: son dos puertas con permisos distintos (/mcp y los "
        "webhooks de n8n). " + _COMO_GENERAR
    )

# Se convierte el secreto a bytes UNA sola vez, al arrancar, en lugar de
# repetirlo en cada petición. Ver más abajo por qué se compara en bytes.
_SECRETO_ESPERADO = WEBHOOK_SECRET.encode("utf-8")

# Lo mismo para el secreto del Gate.
_SECRETO_GATE_ESPERADO = GATE_SECRET.encode("utf-8")

# auto_error=False: si la cabecera falta, APIKeyHeader NO lanza su
# propio error; devuelve None y el 401 lo lanzamos nosotros. Así "falta
# la cabecera" y "la cabecera es incorrecta" producen exactamente la
# misma respuesta, y quien esté probando secretos al azar no puede
# distinguir un caso del otro. (Comprobado en el código fuente de
# fastapi 0.141.1: con auto_error=True respondería también 401 si
# falta, pero con otro texto, "Not authenticated".)
_lector_cabecera = APIKeyHeader(name=NOMBRE_CABECERA, auto_error=False)


# async def (y no def, como el resto de endpoints del proyecto) a
# propósito: el resto usa def porque hace llamadas BLOQUEANTES a
# Postgres, y FastAPI las manda a su pool de hilos para no congelar el
# servidor. Esta función no bloquea nada: comparar dos cadenas cortas
# es instantáneo. Con async def se ejecuta directamente en el bucle de
# eventos, sin ocupar uno de los hilos limitados del pool en cada
# petición.
async def verificar_webhook_secret(
    # Security(...) funciona igual que Depends(...), pero además marca
    # el parámetro como requisito de seguridad para /docs. FastAPI
    # llama a _lector_cabecera, que devuelve el valor de la cabecera
    # (un str) o None si no viene.
    secreto_recibido: str | None = Security(_lector_cabecera),
) -> None:
    """
    Dependencia de FastAPI: deja pasar la petición solo si trae la
    cabecera X-Webhook-Secret con el valor correcto. En cualquier otro
    caso lanza HTTPException(401) y el endpoint NO se ejecuta, así que
    tampoco se toca la base de datos.

    No devuelve nada (-> None): el endpoint no necesita el secreto, solo
    que la comprobación haya pasado.
    """
    # La comparación vive en _secreto_coincide (más abajo), compartida con
    # verificar_gate_secret desde el 2026-09-30, para que la lógica
    # delicada (bytes + tiempo constante) exista una sola vez.
    if not _secreto_coincide(secreto_recibido, _SECRETO_ESPERADO):
        raise _no_autorizado()


# Lector de la cabecera del Gate. Igual que _lector_cabecera, con dos
# diferencias:
#   - name: lee X-Gate-Secret, no X-Webhook-Secret. Por eso el valor de
#     WEBHOOK_SECRET enviado en su cabecera de siempre no sirve aquí: esta
#     puerta ni siquiera mira esa cabecera.
#   - scheme_name: el nombre del esquema de seguridad en /docs. Por
#     defecto FastAPI usa el nombre de la clase ("APIKeyHeader"), y con dos
#     lectores de la misma clase los dos se llamarían igual en la
#     documentación: uno taparía al otro en el botón "Authorize". Con un
#     nombre propio, /docs ofrece dos casillas, una por secreto.
_lector_cabecera_gate = APIKeyHeader(
    name=NOMBRE_CABECERA_GATE, scheme_name="GateSecret", auto_error=False
)


# async def por el mismo motivo que verificar_webhook_secret: no bloquea.
async def verificar_gate_secret(
    # Security(...) llama a _lector_cabecera_gate, que devuelve el valor de
    # la cabecera X-Gate-Secret o None si no viene.
    secreto_recibido: str | None = Security(_lector_cabecera_gate),
) -> None:
    """
    Dependencia de FastAPI para POST /gate-decisions: deja pasar la
    petición solo si trae la cabecera X-Gate-Secret con el valor de
    GATE_SECRET. En cualquier otro caso, 401 idéntico al de /leads (misma
    función _no_autorizado), y el endpoint no se ejecuta.
    """
    if not _secreto_coincide(secreto_recibido, _SECRETO_GATE_ESPERADO):
        raise _no_autorizado()


def _secreto_coincide(secreto_recibido: str | None, esperado: bytes) -> bool:
    """
    True solo si la cabecera trae exactamente el secreto esperado.

    secreto_recibido: lo que llegó en la cabecera (None si no vino).
    esperado:         el secreto del .env, ya convertido a bytes al arrancar.
    """
    # Cabecera ausente o vacía: se rechaza sin llegar a comparar.
    if not secreto_recibido:
        return False

    # POR QUÉ compare_digest Y NO ==
    #
    # "==" entre dos cadenas compara carácter a carácter y se detiene en
    # el PRIMERO que no coincide. Así, un secreto que acierta los 10
    # primeros caracteres tarda un poquito más en rechazarse que uno que
    # falla en el primero. Enviando muchísimas peticiones y midiendo
    # esos microsegundos, un atacante puede ir descubriendo el secreto
    # carácter a carácter (es un "ataque de tiempo", timing attack).
    # secrets.compare_digest tarda siempre lo mismo, acierte lo que
    # acierte, así que el tiempo de respuesta no filtra información.
    #
    # POR QUÉ SE CONVIERTE A BYTES (UTF-8) ANTES DE COMPARAR
    #
    # compare_digest acepta dos str, pero lanza TypeError si alguno
    # contiene caracteres no ASCII (ñ, tildes, emojis...). Starlette
    # decodifica las cabeceras HTTP como latin-1, así que cualquiera
    # puede enviar "X-Webhook-Secret: ñ". Si se comparase en str, ese
    # TypeError no lo capturaría nadie y caería en el manejador global
    # de app/main.py: respondería 500 en lugar de 401 y escribiría una
    # fila en la tabla logs POR CADA petición, es decir, cualquiera
    # podría llenar logs desde fuera sin conocer el secreto. Con bytes,
    # compare_digest acepta cualquier contenido, y un valor raro es
    # simplemente un secreto incorrecto más: 401.
    return secrets.compare_digest(secreto_recibido.encode("utf-8"), esperado)


def _no_autorizado() -> HTTPException:
    """
    Construye el error 401. Está en una función aparte para que los dos
    casos de rechazo (cabecera ausente y cabecera incorrecta) devuelvan
    exactamente la misma respuesta, byte a byte.

    401 "Unauthorized" es el código HTTP de "no has demostrado quién
    eres". La especificación HTTP (RFC 9110) exige que toda respuesta
    401 incluya la cabecera WWW-Authenticate indicando cómo
    autenticarse. No hay un valor estándar para claves en cabecera, así
    que se usa "APIKey", el mismo que usa FastAPI internamente.

    El mensaje es deliberadamente genérico: no dice si faltaba la
    cabecera o si era incorrecta, para no dar pistas a quien esté
    probando.
    """
    return HTTPException(
        status_code=401,
        detail="No autorizado",
        headers={"WWW-Authenticate": "APIKey"},
    )
