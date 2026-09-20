"""
Servidor MCP del proyecto: la puerta por la que el agente de IA de n8n
(nodo "MCP Client Tool") descubre y usa las herramientas del backend.

Es un ADAPTADOR, como los de app/api/: aquí no hay SQL ni reglas de
negocio. Cada tool llama a una función de app/services/, la MISMA que usa
la puerta REST equivalente.

Contiene dos cosas:
  1. La autenticación de /mcp (VerificadorSecretoMCP).
  2. Las tools. En este bloque, solo calculate_estimate.

Se monta dentro de FastAPI, en /mcp, desde app/main.py (un solo proceso,
decisión de arquitectura por la RAM de 8 GB).
"""

import secrets

# Annotated (biblioteca estándar, módulo typing) permite pegarle a un tipo
# información extra sin cambiar el tipo en sí. Annotated[int, Field(gt=0)]
# sigue siendo un int, pero con la regla "mayor que 0". fastmcp lee esa
# regla, la valida con Pydantic antes de llamar a la tool y la publica en
# el esquema que ve el agente. Es el equivalente, para un parámetro
# suelto, del Field(gt=0) de EstimateRequest.
from typing import Annotated

from fastmcp import FastMCP

# ToolError: la forma que tiene una tool de fastmcp de decir "esto ha
# fallado por un motivo que el agente debe conocer". fastmcp la convierte
# en una respuesta de error del protocolo MCP con el mensaje que le
# damos. Es el equivalente MCP de HTTPException.
from fastmcp.exceptions import ToolError

# TokenVerifier: la clase base de fastmcp para "comprobar un token
# Bearer". Se hereda de ella para escribir nuestra propia comprobación
# (ver VerificadorSecretoMCP). AccessToken: el objeto que hay que
# devolver cuando el token es válido; fastmcp lo guarda para que las
# tools puedan saber quién llama.
from fastmcp.server.auth import AccessToken, TokenVerifier
from pydantic import Field

from app.config import MCP_SECRET
from app.schemas.estimates import EstimateResponse

# Se importa el MÓDULO del servicio, y no la función suelta, porque la
# tool se llama igual que la función (calculate_estimate). Con dos
# nombres iguales en este archivo, uno taparía al otro. Así se escribe
# estimate_service.calculate_estimate(...) y queda claro cuál es cuál.
from app.services import estimate_service

# ---------------------------------------------------------------------
# Comprobación de arranque (fail fast)
# ---------------------------------------------------------------------
# Mismo patrón que WEBHOOK_SECRET en app/api/security.py, y por los
# mismos motivos:
#   - A nivel de módulo, se ejecuta al IMPORTAR el archivo. main.py
#     importa este módulo al arrancar, así que si falta el secreto uvicorn
#     se detiene ANTES de abrir el puerto. Sin esto, el servidor
#     arrancaría con /mcp rechazando a todo el mundo, y el primer síntoma
#     sería un agente que falla en producción sin motivo aparente.
#   - .strip() trata un valor de solo espacios como ausente.
#   - Aquí y no en config.py: solo se le exige a quien lo usa.
if MCP_SECRET is None or not MCP_SECRET.strip():
    raise RuntimeError(
        "MCP_SECRET no está definido (o está vacío) en el .env. "
        "Es obligatorio: autentica al agente de IA en la puerta /mcp. "
        "Debe ser DISTINTO de WEBHOOK_SECRET. Genera uno con:  "
        "python -c \"import secrets; print(secrets.token_urlsafe(32))\"  "
        "y añádelo al .env como MCP_SECRET=<valor>. Ver .env.example."
    )

# A bytes una sola vez, al arrancar, como _SECRETO_ESPERADO en
# security.py. El motivo de comparar en bytes está más abajo.
_SECRETO_MCP = MCP_SECRET.encode("utf-8")


class VerificadorSecretoMCP(TokenVerifier):
    """
    Comprueba el token Bearer de cada petición a /mcp contra MCP_SECRET.

    QUÉ ES "class X(Y)": HERENCIA
    Esta clase HEREDA de TokenVerifier: recibe todo lo que TokenVerifier
    ya sabe hacer (leer la cabecera Authorization, responder 401 con la
    cabecera WWW-Authenticate: Bearer que exige el protocolo...) y solo
    redefine el único método que TokenVerifier deja sin hacer:
    verify_token. fastmcp llama a ese método con el token de cada
    petición y decide según lo que devuelva:
      - un AccessToken -> token válido, la petición pasa;
      - None           -> token inválido, fastmcp responde 401.

    POR QUÉ NO SE USA StaticTokenVerifier, que ya trae fastmcp
    fastmcp 4.0.3 incluye StaticTokenVerifier, que hace casi lo mismo con
    una línea. Se descartó tras leer su código fuente en el venv:
      - Busca el token en un diccionario (self.tokens.get(token)). Eso no
        es una comparación en tiempo constante, y el proyecto ya fijó
        su estándar en app/api/security.py: secrets.compare_digest.
        Siendo honestos, el riesgo práctico de esa búsqueda es bajo
        (compara primero el hash, no carácter a carácter). Pero tener dos
        criterios de seguridad distintos para dos secretos del mismo
        sistema no tiene justificación.
      - Su propio docstring avisa: "Never use this in production -
        tokens are stored in plain text!".
    Aviso honesto: ESTA clase también guarda el secreto en claro en
    memoria, igual que security.py con WEBHOOK_SECRET. Es la limitación
    aceptada de cualquier secreto compartido en N0. La solución de
    producción es un proveedor OAuth/JWT (fastmcp trae JWTVerifier),
    donde el servidor solo guarda una clave PÚBLICA para verificar
    firmas y nunca conoce los tokens válidos.
    """

    # async def: fastmcp llama a verify_token desde su código asíncrono,
    # así que tiene que ser una corrutina. Aquí no hay nada bloqueante
    # (comparar dos cadenas cortas es instantáneo), por el mismo motivo
    # por el que verificar_webhook_secret también es async def.
    async def verify_token(self, token: str) -> AccessToken | None:
        # POR QUÉ compare_digest Y POR QUÉ EN BYTES
        #
        # Es EXACTAMENTE el mismo razonamiento que en app/api/security.py:
        #   - "==" se detiene en el primer carácter distinto, así que el
        #     tiempo de respuesta revela cuántos caracteres se acertaron
        #     (ataque de tiempo). compare_digest tarda siempre lo mismo.
        #   - compare_digest con dos str lanza TypeError si hay
        #     caracteres no ASCII. Un "Bearer ñ" enviado desde fuera
        #     acabaría en un 500 en vez de un 401. Con bytes, cualquier
        #     contenido es simplemente un token incorrecto más.
        # La diferencia con security.py está en cómo se rechaza. Allí se
        # lanza HTTPException(401), porque es una dependencia de FastAPI.
        # Aquí se devuelve None y es fastmcp quien construye el 401
        # siguiendo el protocolo MCP.
        if not secrets.compare_digest(token.encode("utf-8"), _SECRETO_MCP):
            return None

        # Token válido. AccessToken exige un client_id y una lista de
        # scopes (permisos). Solo hay un cliente legítimo, el agente de
        # n8n, y ningún permiso diferenciado en N0: lista vacía.
        #
        # Se guarda token="" y NO el secreto real. fastmcp conserva este
        # objeto durante la petición, y no tiene sentido dejar otra copia
        # del secreto en memoria si nadie la va a leer.
        return AccessToken(token="", client_id="n8n-agente", scopes=[])


# Instancia del servidor MCP. auth= activa la autenticación: TODAS las
# peticiones a /mcp pasan por VerificadorSecretoMCP antes de llegar a
# cualquier tool, incluida list_tools (la pregunta "¿qué herramientas
# tienes?"). Comprobado con una prueba real antes de implementar: con
# el servidor montado dentro de FastAPI, auth= se aplica, y sin
# cabecera la respuesta es 401.
mcp = FastMCP(
    "Reformas Integrales Amedida — MCP Server",
    auth=VerificadorSecretoMCP(),
)


# ---------------------------------------------------------------------
# Tool: calculate_estimate
# ---------------------------------------------------------------------
# @mcp.tool es un DECORADOR (ver la explicación en app/api/estimates.py)
# que REGISTRA la función de debajo como herramienta MCP. A partir de
# ella, fastmcp construye lo que ve el agente:
#   - el nombre de la tool: el nombre de la función, calculate_estimate;
#   - la descripción: el DOCSTRING de la función. Ese texto lo lee el LLM
#     para decidir cuándo y cómo usarla. Por eso está escrito para el
#     agente, sin explicaciones de código (esas van en comentarios #, que
#     el agente no ve), y sin mencionar tarifas, fórmulas ni reglas: el
#     agente no debe conocerlas (Adenda 1.1a);
#   - el esquema de entrada, a partir de los parámetros y sus tipos;
#   - el esquema de salida, a partir del tipo de retorno EstimateResponse.
#
# def y no async def: la función llama a psycopg2, que BLOQUEA. Se
# comprobó con una prueba real que fastmcp 4.0.3 ejecuta las tools def en
# un hilo aparte ("AnyIO worker thread"), igual que FastAPI con sus
# endpoints def, así que no congela el servidor.
@mcp.tool
def calculate_estimate(
    oportunidad_id: Annotated[int, Field(gt=0, description="Id de la oportunidad del cliente")],
) -> EstimateResponse:
    """
    Calcula el presupuesto orientativo de una oportunidad a partir de su
    identificador. Devuelve un rango en euros
    (importe_min_con_iva - importe_max_con_iva) con el IVA ya incluido:
    es el precio final que se le puede comunicar al cliente tal cual, sin
    sumarle nada.

    Solo necesita oportunidad_id. Todos los datos de la reforma (tipo,
    metros, acabados, cambios estructurales) los obtiene el sistema por
    su cuenta: no los pidas al cliente para esta herramienta ni intentes
    pasarlos.

    Llamarla varias veces para la misma oportunidad es seguro: devuelve
    siempre el mismo presupuesto (creado=false a partir de la segunda).

    Cómo interpretar la respuesta:
    - requiere_aprobacion=false: se puede comunicar el rango al cliente.
    - requiere_aprobacion=true: una persona del equipo debe revisarlo
      antes; motivo_gate indica el motivo.
    - importe_min_con_iva e importe_max_con_iva llegan a null cuando la reforma incluye
      cambios estructurales (motivo_gate 'cambios_estructurales' o
      'ambos'): la cifra depende de un informe técnico. NO estimes ni
      inventes ningún importe; comunica que un técnico lo revisará.
    - status='requiere_revision': no se ha podido calcular; el equipo lo
      revisará. No hay importe que comunicar.
    """
    # La tool solo traduce: llama a la MISMA función que POST
    # /calculate-estimate y convierte los errores de negocio en ToolError.
    try:
        respuesta = estimate_service.calculate_estimate(oportunidad_id)
    except estimate_service.OportunidadNoEncontrada:
        # "raise ... from None" corta el enlace con la excepción original.
        # Sin from None, Python adjuntaría el error interno como "causa",
        # y ese detalle podría acabar en el mensaje que ve el agente.
        # El agente solo necesita saber qué ha pasado, no el traceback.
        raise ToolError("No existe ninguna oportunidad con ese id.") from None
    except estimate_service.EstadoNoPermiteCalculo:
        raise ToolError(
            "Esta oportunidad no está en un estado que permita calcular un presupuesto."
        ) from None

    # Diferencia deliberada con la puerta REST (decisión 5 del plan): el
    # agente recibe la versión con los importes ocultos cuando hay
    # cambios estructurales. La regla vive en services/ y aquí solo se
    # aplica.
    return estimate_service.ocultar_importes_para_agente(respuesta)
