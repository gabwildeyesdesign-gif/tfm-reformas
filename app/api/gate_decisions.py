"""
ALIAS de POST /resultados-llamada con el contrato ANTIGUO: POST
/gate-decisions (solo REST, sin tool MCP).

Lo llama el formulario de n8n PUBLICADO con el que administración registra
el resultado de su llamada en un caso de Gate (plan original:
docs/Plan_Endpoint_Gate_Decisions.txt). Desde el Bloque B1 de
docs/Plan_Resultado_Llamadas.txt (2026-10-11), esta ruta es un alias
(D26.6, P2): mismos campos, mismos motivos y mismos códigos que antes, pero
la operación es la MISMA función del servicio que usa la ruta nueva
(registrar_decision traduce los nombres y llama a registrar_resultado). Se
quitará, en otra rama, cuando el formulario use la ruta nueva.

NUNCA lo llama un agente de IA: tiene su propio secreto (X-Gate-Secret,
decisión P4), que la herramienta del Agente 2 no tiene.

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
"""

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (una
# dependencia que se ejecuta antes del endpoint) y Response (la respuesta
# HTTP, para poder cambiarle el código de estado).
from fastapi import APIRouter, Depends, Response

# La traducción de un rechazo a HTTP, la MISMA de la ruta nueva: así las dos
# rutas responden igual a cada rechazo.
from app.api.resultados_llamada import http_de_rechazo
# La dependencia que comprueba la cabecera X-Gate-Secret (app/api/security.py).
from app.api.security import verificar_gate_secret
# Los esquemas ANTIGUOS de la entrada y de la salida: el contrato que lee el
# formulario publicado no cambia.
from app.schemas.gate_decisions import GateDecisionCreate, GateDecisionResponse
# La entrada con el contrato antiguo del servicio (traduce y llama a
# registrar_resultado).
from app.services.resultados_llamada_service import registrar_decision
# La base común de todos los rechazos de negocio.
from app.services.reglas_visita import RechazoNegocio

# APIRouter: un "mini-FastAPI" con las rutas de este archivo. main.py lo
# engancha a la aplicación con include_router. tags agrupa las rutas en
# /docs bajo el título "gate".
router = APIRouter(tags=["gate"])


# @router.post(...) es un DECORADOR: registra la función de debajo como la
# que atiende POST /gate-decisions. Los mismos parámetros que antes de B1:
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
# def y no async def: el servicio hace llamadas BLOQUEANTES a Postgres
# (psycopg2), y FastAPI ejecuta las funciones def en su pool de hilos.
#   data: FastAPI lee el cuerpo JSON y lo valida con GateDecisionCreate;
#     si no cumple, responde 422 sin llegar a llamar a esta función.
#   response: el objeto de la respuesta HTTP, para poder cambiar su código.
def post_gate_decisions(data: GateDecisionCreate, response: Response) -> GateDecisionResponse:
    """
    Alias de POST /resultados-llamada con el contrato antiguo: registra la
    decisión de administración tras la llamada al cliente en un caso de
    Gate, visita_acordada (con fecha y hora) o descartar (con motivo). La
    decisión es definitiva. Nunca devuelve importes.
    """
    # Se delega TODO el trabajo en services/, por la entrada antigua. Si algo
    # no se permite, services/ lanza un rechazo, que se traduce a HTTP.
    try:
        resultado = registrar_decision(data)
    # RechazoNegocio es la base de TODOS los rechazos (los propios y los
    # compartidos de reglas_visita.py); http_de_rechazo da el código y el
    # detalle {"motivo", "mensaje"} (y "faltan" en el 503).
    except RechazoNegocio as error:
        raise http_de_rechazo(error)

    # Repetición exacta de la decisión ya registrada: no se ha creado nada.
    # resultado.creado lo decide services/; aquí solo se cambia el código
    # HTTP del 201 por defecto a 200.
    if not resultado.creado:
        response.status_code = 200
    # FastAPI filtra este objeto con response_model y lo envía como JSON.
    return resultado
