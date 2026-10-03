"""
Endpoint HTTP de visitas: POST /visits (solo REST, sin tool MCP).

Lo llama la herramienta HTTP del Agente 2 en n8n cuando el cliente pide
una visita técnica en el chat. Como todo app/api/, es un ADAPTADOR: no
tiene lógica de negocio ni SQL. Traduce HTTP -> llamada a services/ ->
HTTP, y decide qué código HTTP corresponde a cada rechazo.
"""

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (declara
# una dependencia que se ejecuta antes del endpoint), HTTPException (corta
# y responde con un código de error) y Response (la respuesta HTTP, para
# poder cambiarle el código de estado).
from fastapi import APIRouter, Depends, HTTPException, Response

# La dependencia que comprueba la cabecera X-Webhook-Secret (la misma que
# POST /leads).
from app.api.security import verificar_webhook_secret
# Los esquemas Pydantic de la entrada y de la salida de este endpoint.
from app.schemas.visits import VisitaCreate, VisitaResponse
# Los rechazos COMPARTIDOS con /gate-decisions (FechaNoValida,
# ConfiguracionIncompleta) y su base común RechazoNegocio viven en
# reglas_visita.py desde el 2026-09-30; los PROPIOS de /visits, en
# visits_service.py.
from app.services.reglas_visita import ConfiguracionIncompleta, FechaNoValida, RechazoNegocio
from app.services.visits_service import (
    EstadoNoPermiteVisita,
    LeadNoEncontrado,
    solicitar_visita,
)

# APIRouter: un "mini-FastAPI" con las rutas de este archivo. main.py lo
# engancha a la aplicación con include_router. tags agrupa las rutas en
# /docs bajo el título "visitas".
router = APIRouter(tags=["visitas"])

# Qué código HTTP corresponde a cada familia de rechazo de services/.
# Es la tabla de la sección 6 del plan, en un solo sitio.
#   404: no hay lead con ese lead_token.
#   409: el estado del lead no lo permite (sin presupuesto, Gate, estado,
#        visita ya confirmada).
#   422: la fecha incumple una regla de negocio (pasada, fin de semana,
#        fuera de franja). Distinto del 422 de Pydantic (formato), que
#        FastAPI genera por su cuenta con otra forma (ver la Adenda).
#   503: falta configuración en reglas_negocio (decisión P2).
CODIGO_HTTP = {
    LeadNoEncontrado: 404,
    EstadoNoPermiteVisita: 409,
    FechaNoValida: 422,
    ConfiguracionIncompleta: 503,
}


# status_code=201 por defecto: una solicitud nueva (o una sustitución)
# CREA una visita. En una repetición se cambia a 200 dentro de la función,
# igual que POST /leads.
#
# dependencies=[Depends(verificar_webhook_secret)]: la misma comprobación
# del secreto que POST /leads. Se ejecuta ANTES de validar el cuerpo, así
# que sin la cabecera correcta la respuesta es 401 aunque el cuerpo esté
# mal.
@router.post(
    "/visits",
    response_model=VisitaResponse,
    status_code=201,
    dependencies=[Depends(verificar_webhook_secret)],
)
# def y no async def: solicitar_visita hace llamadas BLOQUEANTES a Postgres,
# y FastAPI ejecuta las funciones def en su pool de hilos.
#   data: el cuerpo JSON, ya validado con VisitaCreate (si no cumple, 422
#     sin llegar a esta función).
#   response: la respuesta HTTP, para poder cambiar su código a 200.
def post_visits(data: VisitaCreate, response: Response) -> VisitaResponse:
    """
    Registra la SOLICITUD de visita técnica de un cliente (no es una
    reserva: administración llama para confirmar). Nunca devuelve importes.
    """
    # Todo el trabajo lo hace services/; si algo no se permite, lanza una
    # excepción de rechazo que se traduce a HTTP en el "except".
    try:
        resultado = solicitar_visita(data)
    # RechazoNegocio es la base de TODOS: los propios de /visits (que
    # heredan de VisitaRechazada) y los compartidos de reglas_visita.py.
    # Capturar solo VisitaRechazada dejaría escapar un 422 o un 503 como 500.
    except RechazoNegocio as error:
        # type(error) es la clase concreta (FechaNoValida, etc.); con ella
        # se busca el código en la tabla. detail es un diccionario, así que
        # el cuerpo de la respuesta será {"detail": {"motivo": ..., ...}}:
        # el motivo es estable (para n8n y las pruebas) y el mensaje es el
        # texto que el agente le explica al cliente.
        detalle = {"motivo": error.motivo, "mensaje": error.mensaje}
        # getattr(objeto, nombre, por_defecto): solo ConfiguracionIncompleta
        # tiene el atributo "faltan"; las demás no lo añaden.
        faltan = getattr(error, "faltan", None)
        # Solo el 503 trae "faltan": se añade al detalle para ver qué falta.
        if faltan is not None:
            detalle["faltan"] = faltan
        # Se corta la petición con el código de la tabla y el detalle.
        raise HTTPException(status_code=CODIGO_HTTP[type(error)], detail=detalle)

    # Repetición con la misma fecha: no se ha creado nada, así que 200.
    if not resultado.creado:
        response.status_code = 200
    # FastAPI filtra este objeto con response_model y lo envía como JSON.
    return resultado
