"""
Endpoint HTTP del seguimiento de 48 h: POST /create-followup-task (solo REST,
sin tool MCP; plan: docs/Plan_Endpoint_Create_Followup_Task.txt).

Lo llama WF3 (n8n) por cada elemento del apartado seguimientos_por_abrir de
GET /llamadas-del-dia. NUNCA lo llama un agente de IA: cambia el estado de
una oportunidad desde una herramienta de administración, así que usa la
llave del Gate (X-Gate-Secret), que la herramienta del Agente 2 no tiene
(plan, 2.3).

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
Traduce HTTP -> llamada a services/ -> HTTP, y decide qué código HTTP
corresponde a cada rechazo.
"""

# sys: para escribir la línea [AVISO] del 503 en la salida de errores.
import sys

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (una
# dependencia que se ejecuta antes del endpoint), HTTPException (corta y
# responde con un código de error) y Response (para cambiar el código).
from fastapi import APIRouter, Depends, HTTPException, Response

# La dependencia que comprueba la cabecera X-Gate-Secret (la de
# /gate-decisions y /llamadas-del-dia). NO verificar_webhook_secret.
from app.api.security import verificar_gate_secret

# Los esquemas de la entrada y de la salida.
from app.schemas.followup_tasks import FollowupTaskCreate, FollowupTaskResponse

# La función del servicio y sus rechazos propios (404 y 409).
from app.services.followup_service import (
    OportunidadNoEncontrada,
    SeguimientoNoPermitido,
    abrir_seguimiento,
)

# Los rechazos compartidos: la base común y el de la configuración (503).
from app.services.reglas_visita import ConfiguracionIncompleta, RechazoNegocio

# APIRouter: un "mini-FastAPI" con las rutas de este archivo. main.py lo
# engancha con include_router. tags agrupa la ruta en /docs bajo
# "administracion", junto a la lista diaria.
router = APIRouter(tags=["administracion"])

# Qué código HTTP corresponde a cada familia de rechazo (plan, 2.8).
#   404: no existe la oportunidad.
#   409: no cumple uno de los seis criterios (el motivo dice cuál).
#   503: falta o no es válida la regla de 48 h.
CODIGO_HTTP = {
    OportunidadNoEncontrada: 404,
    SeguimientoNoPermitido: 409,
    ConfiguracionIncompleta: 503,
}


# @router.post(...) registra la función de debajo como la que atiende
# POST /create-followup-task.
#   response_model: el JSON se construye SOLO con los campos de
#     FollowupTaskResponse.
#   status_code=201: abrir el seguimiento crea un log. Si ya estaba
#     abierto, se cambia a 200 dentro de la función.
#   dependencies: la llave del Gate, ANTES de validar el cuerpo (sin la
#     cabecera correcta, 401 aunque el cuerpo esté mal).
@router.post(
    "/create-followup-task",
    response_model=FollowupTaskResponse,
    status_code=201,
    dependencies=[Depends(verificar_gate_secret)],
)
# def y no async def: el servicio hace llamadas BLOQUEANTES a Postgres, y
# FastAPI ejecuta las funciones def en su pool de hilos.
#   data: el cuerpo JSON validado con FollowupTaskCreate (si no cumple,
#     FastAPI responde 422 sin llamar a esta función).
#   response: la respuesta HTTP, para poder cambiar su código.
def post_create_followup_task(data: FollowupTaskCreate, response: Response) -> FollowupTaskResponse:
    """
    Abre el seguimiento de 48 h de una oportunidad sin Gate que no ha
    pedido visita: pasa de 'presupuesto_enviado' a 'seguimiento_pendiente'
    y deja un log. No llama ni avisa a nadie.
    """
    # Todo el trabajo lo hace services/.
    try:
        resultado = abrir_seguimiento(data)
    # El 503 va primero: además de responder, deja la traza en stderr
    # (P4: sin log, como GET /llamadas-del-dia con la misma regla).
    except ConfiguracionIncompleta as error:
        # Una línea en la consola del uvicorn; nombra la clave, nunca un
        # secreto.
        print(
            f"[AVISO] POST /create-followup-task: configuración incompleta en reglas_negocio: {error.faltan}",
            file=sys.stderr,
        )
        # Mismo formato que los demás 503: motivo, mensaje y faltan.
        raise HTTPException(
            status_code=503,
            detail={"motivo": error.motivo, "mensaje": error.mensaje, "faltan": error.faltan},
        )
    # El resto de rechazos (404 y 409), con el formato
    # {"detail": {"motivo": ..., "mensaje": ...}}.
    except RechazoNegocio as error:
        # type(error) es la clase concreta; con ella se busca el código.
        raise HTTPException(
            status_code=CODIGO_HTTP[type(error)],
            detail={"motivo": error.motivo, "mensaje": error.mensaje},
        )

    # Ya estaba abierto: no se ha escrito nada; 200 en vez de 201.
    if not resultado.creado:
        response.status_code = 200
    # FastAPI filtra este objeto con response_model y lo envía como JSON.
    return resultado
