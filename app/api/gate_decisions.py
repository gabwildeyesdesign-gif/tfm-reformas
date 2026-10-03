"""
Endpoint HTTP de las decisiones del Gate: POST /gate-decisions (solo REST,
sin tool MCP).

Lo llama el formulario de n8n con el que administración registra el
resultado de su llamada a un cliente cuyo presupuesto activó el Gate (plan:
docs/Plan_Endpoint_Gate_Decisions.txt). NUNCA lo llama un agente de IA: por
eso tiene su propio secreto (X-Gate-Secret, decisión P4), que la
herramienta del Agente 2 no tiene.

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
Traduce HTTP -> llamada a services/ -> HTTP, y decide qué código HTTP
corresponde a cada rechazo.
"""

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (declara
# una dependencia que se ejecuta antes del endpoint), HTTPException (corta
# y responde con un código de error) y Response (la respuesta HTTP, para
# poder cambiarle el código de estado).
from fastapi import APIRouter, Depends, HTTPException, Response

# La dependencia que comprueba la cabecera X-Gate-Secret (app/api/security.py).
from app.api.security import verificar_gate_secret
# Los esquemas Pydantic de la entrada (lo que se valida del cuerpo JSON) y
# de la salida (lo único que puede salir en la respuesta).
from app.schemas.gate_decisions import GateDecisionCreate, GateDecisionResponse

# Los rechazos COMPARTIDOS con /visits (FechaNoValida, ConfiguracionIncompleta)
# y su base común RechazoNegocio viven en reglas_visita.py; los PROPIOS de
# este endpoint, en gate_decisions_service.py.
from app.services.gate_decisions_service import (
    EstadoNoPermiteDecision,
    OportunidadNoEncontrada,
    registrar_decision,
)
from app.services.reglas_visita import ConfiguracionIncompleta, FechaNoValida, RechazoNegocio

# APIRouter: un "mini-FastAPI" con las rutas de este archivo. main.py lo
# engancha a la aplicación con include_router. tags agrupa las rutas en
# /docs bajo el título "gate".
router = APIRouter(tags=["gate"])

# Qué código HTTP corresponde a cada familia de rechazo de services/. Es la
# tabla de la sección 1.8 del plan, en un solo sitio.
#   404: no existe la oportunidad.
#   409: la oportunidad no admite esta decisión (sin presupuesto, sin Gate,
#        decisión distinta ya registrada, estado, visita activa).
#   422: la fecha incumple una regla de negocio (pasada, fin de semana,
#        fuera de franja). Distinto del 422 de Pydantic (formato), que
#        FastAPI genera por su cuenta con otra forma.
#   503: falta configuración de visitas en reglas_negocio.
CODIGO_HTTP = {
    OportunidadNoEncontrada: 404,
    EstadoNoPermiteDecision: 409,
    FechaNoValida: 422,
    ConfiguracionIncompleta: 503,
}


# @router.post(...) es un DECORADOR: registra la función de debajo como la
# que atiende POST /gate-decisions.
#   response_model: FastAPI construye el JSON de respuesta SOLO con los
#     campos de GateDecisionResponse (segunda barrera contra importes).
#   status_code=201: una decisión nueva CREA una fila. En una repetición
#     se cambia a 200 dentro de la función, igual que /visits y /leads.
#   dependencies=[Depends(verificar_gate_secret)]: la comprobación de
#     X-Gate-Secret. Se ejecuta ANTES de validar el cuerpo, así que sin la
#     cabecera correcta la respuesta es 401 aunque el cuerpo esté mal. NO es
#     verificar_webhook_secret: la credencial del Agente 2 no abre esta
#     puerta.
@router.post(
    "/gate-decisions",
    response_model=GateDecisionResponse,
    status_code=201,
    dependencies=[Depends(verificar_gate_secret)],
)
# def y no async def: registrar_decision hace llamadas BLOQUEANTES a
# Postgres (psycopg2), y FastAPI ejecuta las funciones def en su pool de
# hilos para no congelar el servidor.
#   data: FastAPI lee el cuerpo JSON y lo valida con GateDecisionCreate;
#     si no cumple, responde 422 sin llegar a llamar a esta función.
#   response: el objeto de la respuesta HTTP, para poder cambiar su código.
def post_gate_decisions(data: GateDecisionCreate, response: Response) -> GateDecisionResponse:
    """
    Registra la decisión de administración tras la llamada al cliente en un
    caso de Gate: visita_acordada (con fecha y hora) o descartar (con
    motivo). La decisión es definitiva. Nunca devuelve importes.
    """
    # Se delega TODO el trabajo en services/ (comprobaciones, escritura en
    # la base de datos). Si algo no se permite, services/ lanza una de sus
    # excepciones de rechazo, que se traducen a HTTP en el "except".
    try:
        resultado = registrar_decision(data)
    # RechazoNegocio es la base de TODOS los rechazos: los propios (que
    # heredan de DecisionGateRechazada) y los compartidos de
    # reglas_visita.py. Capturar solo los propios dejaría escapar un 422 de
    # fecha o un 503 como 500.
    except RechazoNegocio as error:
        # Mismo formato que /visits (plan, 1.7):
        # {"detail": {"motivo": ..., "mensaje": ...}}, y "faltan" en el 503.
        detalle = {"motivo": error.motivo, "mensaje": error.mensaje}
        # getattr(objeto, nombre, por_defecto): solo ConfiguracionIncompleta
        # tiene el atributo "faltan"; las demás no lo añaden.
        faltan = getattr(error, "faltan", None)
        # Si el rechazo trae la lista de claves de configuración que faltan
        # (solo el 503), se añade al detalle para que se vea qué falta.
        if faltan is not None:
            detalle["faltan"] = faltan
        # type(error) es la clase concreta; con ella se busca el código en
        # la tabla CODIGO_HTTP.
        raise HTTPException(status_code=CODIGO_HTTP[type(error)], detail=detalle)

    # Repetición exacta de la decisión ya registrada: no se ha creado nada.
    # resultado.creado lo decide services/; aquí solo se cambia el código
    # HTTP del 201 por defecto a 200.
    if not resultado.creado:
        response.status_code = 200
    # FastAPI filtra este objeto con response_model y lo envía como JSON.
    return resultado
