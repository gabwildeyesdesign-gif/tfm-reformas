"""
Endpoints HTTP para la gestión de leads.

Este archivo es un ADAPTADOR, no lógica de negocio. Su único trabajo es
traducir entre el mundo HTTP (una petición con un cuerpo JSON) y el
mundo del dominio (una llamada a una función de app/services/). Aquí no
se escribe SQL, no se toma ninguna decisión de negocio y no se conoce la
base de datos.

La razón de que sea tan delgado es estructural: la misma función de
services/ la usa la puerta MCP, así que toda lógica que se colara aquí
dejaría de existir para el agente de IA — y el cálculo podría divergir
entre las dos puertas.
"""

# APIRouter es una "mini aplicación" de FastAPI: agrupa endpoints
# relacionados en un archivo aparte para no tener main.py con cincuenta
# funciones dentro. Luego se engancha a la aplicación principal con
# app.include_router().
# Response: el objeto de la respuesta HTTP que FastAPI está preparando.
# Si una función de endpoint declara un parámetro de tipo Response,
# FastAPI se lo pasa, y la función puede cambiarle el código de estado
# antes de que se envíe (lo usa post_leads en las repeticiones).
# Path: describe un parámetro que viene DENTRO de la ruta (el
# {lead_token} de /leads/session/{lead_token}) y permite ponerle reglas,
# como la longitud mínima y máxima.
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.api.security import verificar_webhook_secret
from app.schemas.common import MAX_LEAD_TOKEN, MIN_LEAD_TOKEN
from app.schemas.leads import LeadCreate, LeadCreateResponse, LeadSessionResponse
from app.services.leads_service import create_lead, get_lead_session

# tags=["leads"] agrupa estos endpoints bajo un epígrafe "leads" en la
# documentación automática de /docs. Es solo presentación.
router = APIRouter(tags=["leads"])


# @router.post(...) es un DECORADOR: una función que envuelve a otra para
# añadirle comportamiento sin tocar su código. Este le dice a FastAPI
# "cuando llegue una petición POST a /leads, llama a la función de
# debajo". La función en sí no sabe nada de HTTP.
#
# response_model=LeadCreateResponse hace dos cosas: valida que lo que
# devolvemos encaja con el modelo (si services/ devolviera algo raro,
# saltaría un error del servidor en vez de enviar basura al cliente) y
# documenta la forma de la respuesta en /docs.
#
# status_code=201 es el código HTTP de "Created", la semántica correcta
# cuando una petición crea un recurso nuevo. Por defecto FastAPI
# devolvería 200 (OK genérico). Es el código POR DEFECTO de este
# endpoint: post_leads lo cambia a 200 cuando la llamada es una
# repetición y no ha creado nada (ver dentro de la función).
#
# dependencies=[Depends(verificar_webhook_secret)] le dice a FastAPI que
# ejecute esa comprobación ANTES de la función del endpoint. Si el
# secreto de la cabecera X-Webhook-Secret falta o no coincide, la
# dependencia lanza un 401 y post_leads no llega a ejecutarse (ni se
# valida el cuerpo, ni se toca la base de datos). Va en el decorador y
# no como parámetro de la función porque post_leads no necesita el
# valor del secreto para nada, solo que la comprobación haya pasado.
@router.post(
    "/leads",
    response_model=LeadCreateResponse,
    status_code=201,
    dependencies=[Depends(verificar_webhook_secret)],
)
def post_leads(data: LeadCreate, response: Response) -> LeadCreateResponse:
    """
    Alta de un lead nuevo. La llama n8n cuando el Agente 1 del chat web
    ha recogido y confirmado con el cliente todos los datos.

    El parámetro 'data' está anotado con el tipo LeadCreate, y eso es lo
    que hace toda la magia: FastAPI ve esa anotación, entiende que el
    cuerpo de la petición es un JSON con esa forma, lo valida con
    Pydantic y solo entonces llama a esta función. Si el JSON no encaja
    (falta un campo, el email no es válido, m2 es negativo), FastAPI
    responde 422 por su cuenta y esta función NO LLEGA A EJECUTARSE
    NUNCA — por lo tanto tampoco se toca la base de datos.

    Es una función síncrona normal (def, no async def) a propósito:
    FastAPI la ejecuta en su pool de hilos, que es exactamente el
    escenario para el que se eligió ThreadedConnectionPool en
    app/db/connection.py.
    """
    # Delegar: toda la lógica (incluida la idempotencia por lead_token)
    # vive en services/. Si esto creciera, sería señal de que lógica de
    # negocio se está filtrando a la capa equivocada.
    resultado = create_lead(data)

    # Traducir el resultado a HTTP, que sí es trabajo de este adaptador:
    # si la llamada es una REPETICIÓN (mismo lead_token, creado=False), no
    # se ha creado nada, y un 201 "Created" mentiría. Se responde 200, el
    # mismo código que devuelve POST /calculate-estimate en su repetición
    # (decisión P8 de su plan: "un 201 mentiría en los reintentos"). Así
    # los dos endpoints de la misma API responden igual a un reintento.
    # Un alta real sigue siendo 201, porque POST /leads sí crea un recurso.
    if not resultado.creado:
        response.status_code = 200
    return resultado


# GET /leads/session/{lead_token}: el router de n8n lo llama al inicio de
# cada turno del chat para decidir a qué agente va el mensaje (Agente 1,
# captura, si aún no hay lead; Agente 2, asesor, si ya existe).
#
# {lead_token} entre llaves en la ruta es un PARÁMETRO DE RUTA: FastAPI
# toma ese trozo de la URL y se lo pasa a la función en el argumento del
# mismo nombre.
#
# response_model=LeadSessionResponse es aquí, además, una BARRERA DE
# SEGURIDAD: FastAPI construye el JSON solo con los campos de ese modelo,
# y el modelo no tiene ningún campo de importe (D18.5). Aunque services/
# leyera los importes, no podrían salir por aquí.
#
# Sin status_code: el código por defecto de FastAPI es 200, que es el que
# pide el contrato en todos los casos válidos, también cuando el token no
# existe (existe=false no es un error).
#
# Misma protección que POST /leads: sin la cabecera X-Webhook-Secret
# correcta, 401 antes de hacer nada más.
@router.get(
    "/leads/session/{lead_token}",
    response_model=LeadSessionResponse,
    dependencies=[Depends(verificar_webhook_secret)],
)
def get_leads_session(
    # Annotated[str, Path(...)] se lee: "lead_token es un str, y además
    # es un parámetro de ruta con estas reglas". Las reglas son las MISMAS
    # constantes que usa LeadCreate (common.py): un token que POST /leads
    # rechazaría, aquí también da 422, y al revés.
    lead_token: Annotated[
        str, Path(min_length=MIN_LEAD_TOKEN, max_length=MAX_LEAD_TOKEN)
    ],
) -> LeadSessionResponse:
    """
    Estado de la conversación de un lead: si existe, en qué estado está
    su oportunidad y si tiene presupuesto (con Gate o sin él). Nunca
    importes.

    Toda la lógica está en services/ (get_lead_session); este adaptador
    solo traduce entre HTTP y esa función.
    """
    return get_lead_session(lead_token)
