"""
Endpoint HTTP de la ficha: GET /oportunidades/{oportunidad_id}/ficha (solo
REST, sin tool MCP; plan: docs/Plan_Endpoint_Ficha_Oportunidad.txt).

La abre administración (desde una página protegida de n8n) antes de llamar
a un cliente, tenga Gate o no. Nunca un agente de IA: devuelve email,
importes e informes (D18.5). Por eso usa la llave de administración
(X-Gate-Secret), que la herramienta del Agente 2 no tiene.

La llave va en el ROUTER, no en la ruta (P8): cualquier ruta que se añada
mañana a este archivo, bajo el prefijo /oportunidades, la hereda sin que
nadie tenga que acordarse de ponerla.

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
"""

# Annotated[int, Path(gt=0)]: un int con la regla "mayor que 0".
from typing import Annotated

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (una
# dependencia que se ejecuta antes del endpoint), HTTPException (corta y
# responde con un código de error) y Path (reglas para el {oportunidad_id}).
from fastapi import APIRouter, Depends, HTTPException, Path

# La dependencia que comprueba X-Gate-Secret (la de /gate-decisions,
# /gate-avisos, /llamadas-del-dia y /create-followup-task). NO
# verificar_webhook_secret: esa llave la tiene el Agente 2.
from app.api.security import verificar_gate_secret

# El esquema de la respuesta: lo único que puede salir.
from app.schemas.ficha_oportunidad import FichaOportunidadResponse

# La función del servicio y su único rechazo.
from app.services.ficha_oportunidad_service import OportunidadNoEncontrada, obtener_ficha

# La base común de todos los rechazos de negocio (motivo y mensaje).
from app.services.reglas_visita import RechazoNegocio

# El router de este archivo:
#   prefix: todas sus rutas empiezan por /oportunidades.
#   tags: las agrupa en /docs bajo "oportunidades".
#   dependencies: la llave de administración para TODAS sus rutas (P8). Se
#     ejecuta antes de validar el parámetro de la ruta, así que sin la
#     cabecera correcta la respuesta es 401 aunque el id no sea un número.
router = APIRouter(
    prefix="/oportunidades",
    tags=["oportunidades"],
    dependencies=[Depends(verificar_gate_secret)],
)

# Qué código HTTP corresponde a cada rechazo (plan, 2.7). No hay 409 (la
# ficha no depende del estado ni del Gate) ni 503 (no lee reglas_negocio).
CODIGO_HTTP = {
    OportunidadNoEncontrada: 404,
}


# Registra la función de debajo como la que atiende
# GET /oportunidades/{oportunidad_id}/ficha (el prefijo se añade solo).
#   response_model: FastAPI construye el JSON SOLO con los campos de
#     FichaOportunidadResponse (segunda barrera de la minimización).
@router.get("/{oportunidad_id}/ficha", response_model=FichaOportunidadResponse)
# def y no async def: obtener_ficha hace llamadas BLOQUEANTES a Postgres, y
# FastAPI ejecuta las funciones def en su pool de hilos.
def get_ficha_oportunidad(
    # Path(gt=0): entero mayor que 0; "0", "-1" o "abc" dan 422 sin llegar
    # a esta función.
    oportunidad_id: Annotated[int, Path(gt=0)],
) -> FichaOportunidadResponse:
    """
    Devuelve la ficha completa de una oportunidad, con o sin Gate: contacto
    de la solicitud, reforma, fotos, presupuesto (con y sin IVA, si hubo
    Gate), visitas, llamadas registradas con su informe, historial de
    estados y otras oportunidades del mismo email. No escribe nada.
    """
    # Todo el trabajo lo hace services/; si la oportunidad no existe, lanza
    # un rechazo, que se traduce a HTTP en el "except".
    try:
        return obtener_ficha(oportunidad_id)
    # Mismo formato que los demás endpoints:
    # {"detail": {"motivo": ..., "mensaje": ...}}.
    except RechazoNegocio as error:
        raise HTTPException(
            status_code=CODIGO_HTTP[type(error)],
            detail={"motivo": error.motivo, "mensaje": error.mensaje},
        )
