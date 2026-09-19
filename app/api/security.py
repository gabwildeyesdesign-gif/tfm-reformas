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

from app.config import WEBHOOK_SECRET

# Nombre de la cabecera. En una constante para que el endpoint, los
# scripts de verificación y la documentación usen exactamente el mismo
# texto, sin riesgo de que uno escriba "X-Webhook-Token" y otro
# "X-Webhook-Secret".
NOMBRE_CABECERA = "X-Webhook-Secret"


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

# Se convierte el secreto a bytes UNA sola vez, al arrancar, en lugar de
# repetirlo en cada petición. Ver más abajo por qué se compara en bytes.
_SECRETO_ESPERADO = WEBHOOK_SECRET.encode("utf-8")

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
    # Cabecera ausente o vacía: se rechaza sin llegar a comparar.
    if not secreto_recibido:
        raise _no_autorizado()

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
    if not secrets.compare_digest(
        secreto_recibido.encode("utf-8"), _SECRETO_ESPERADO
    ):
        raise _no_autorizado()


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
