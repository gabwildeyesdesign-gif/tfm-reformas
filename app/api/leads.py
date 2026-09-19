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
from fastapi import APIRouter, Depends

from app.api.security import verificar_webhook_secret
from app.schemas.leads import LeadCreate, LeadCreateResponse
from app.services.leads_service import create_lead

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
# devolvería 200 (OK genérico).
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
def post_leads(data: LeadCreate) -> LeadCreateResponse:
    """
    Alta de un lead nuevo. Disparado por el webhook de n8n desde el
    formulario web.

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
    # Una sola línea: delegar. Si esto creciera, sería señal de que
    # lógica de negocio se está filtrando a la capa equivocada.
    return create_lead(data)
