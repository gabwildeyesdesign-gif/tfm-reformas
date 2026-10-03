"""
Endpoint HTTP del aviso del Gate: GET /gate-avisos/{oportunidad_id} (solo
REST, sin tool MCP; plan: docs/Plan_Endpoint_Aviso_Gate.txt).

Lo llaman WF2 (el aviso a administración, por email) y el formulario de
POST /gate-decisions, nunca un agente de IA: devuelve importes y datos
personales de un caso con Gate (D18.5.1). Por eso usa la llave del Gate
(X-Gate-Secret), que la herramienta del Agente 2 no tiene.

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
Traduce HTTP -> llamada a services/ -> HTTP, y decide qué código HTTP
corresponde a cada rechazo.
"""

# Annotated: añade información a un tipo sin cambiarlo. Annotated[int,
# Path(gt=0)] sigue siendo un int, pero con la regla "mayor que 0".
from typing import Annotated

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (declara
# una dependencia que se ejecuta antes del endpoint), HTTPException (corta y
# responde con un código de error) y Path (reglas para un parámetro que va
# DENTRO de la ruta, como el {oportunidad_id}).
from fastapi import APIRouter, Depends, HTTPException, Path

# La dependencia que comprueba la cabecera X-Gate-Secret (la misma de POST
# /gate-decisions). NO verificar_webhook_secret: esa llave la tiene el
# Agente 2 (pre-decisión 2).
from app.api.security import verificar_gate_secret

# El esquema de la respuesta: lo único que puede salir.
from app.schemas.aviso_gate import AvisoGateResponse

# La función del servicio y sus dos familias de rechazo.
from app.services.aviso_gate_service import (
    EstadoNoPermiteAviso,
    OportunidadNoEncontrada,
    obtener_aviso,
)

# La base común de todos los rechazos de negocio (guarda motivo y mensaje).
from app.services.reglas_visita import RechazoNegocio

# APIRouter: un "mini-FastAPI" con las rutas de este archivo. main.py lo
# engancha a la aplicación con include_router. tags agrupa la ruta en /docs
# bajo "gate", junto a POST /gate-decisions.
router = APIRouter(tags=["gate"])

# Qué código HTTP corresponde a cada familia de rechazo (plan, 1.6). No hay
# 503: este endpoint no usa reglas_negocio.
#   404: no existe la oportunidad.
#   409: no hay caso de Gate que avisar (sin_presupuesto, sin_gate).
CODIGO_HTTP = {
    OportunidadNoEncontrada: 404,
    EstadoNoPermiteAviso: 409,
}


# @router.get(...) es un DECORADOR: registra la función de debajo como la
# que atiende GET /gate-avisos/{oportunidad_id}. Las llaves {} marcan un
# trozo variable de la ruta, que llega a la función como parámetro.
#   response_model: FastAPI construye el JSON SOLO con los campos de
#     AvisoGateResponse (segunda barrera de la minimización).
#   dependencies=[Depends(verificar_gate_secret)]: la llave del Gate. Se
#     ejecuta ANTES de validar el parámetro de la ruta, así que sin la
#     cabecera correcta la respuesta es 401 aunque el id no sea un número.
@router.get(
    "/gate-avisos/{oportunidad_id}",
    response_model=AvisoGateResponse,
    dependencies=[Depends(verificar_gate_secret)],
)
# def y no async def: obtener_aviso hace llamadas BLOQUEANTES a Postgres
# (psycopg2), y FastAPI ejecuta las funciones def en su pool de hilos para
# no congelar el servidor.
def get_gate_aviso(
    # El {oportunidad_id} de la ruta. Path(gt=0): entero mayor que 0; "0",
    # "-1" o "abc" dan 422 sin llegar a esta función.
    oportunidad_id: Annotated[int, Path(gt=0)],
) -> AvisoGateResponse:
    """
    Devuelve los datos de un caso con Gate para el aviso a administración:
    contacto de la solicitud, datos de la reforma, fotos, importes (con y
    sin IVA), umbral vigente y la decisión ya registrada, si la hay. Nunca
    el informe de la llamada. No escribe nada.
    """
    # Todo el trabajo lo hace services/. Si no hay caso que avisar, lanza
    # un rechazo, que se traduce a HTTP en el "except".
    try:
        return obtener_aviso(oportunidad_id)
    # RechazoNegocio es la base de los dos rechazos de este servicio.
    except RechazoNegocio as error:
        # Mismo formato que /visits y /gate-decisions:
        # {"detail": {"motivo": ..., "mensaje": ...}}. type(error) es la
        # clase concreta; con ella se busca el código en CODIGO_HTTP.
        raise HTTPException(
            status_code=CODIGO_HTTP[type(error)],
            detail={"motivo": error.motivo, "mensaje": error.mensaje},
        )
