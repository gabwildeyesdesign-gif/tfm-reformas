"""
Endpoint HTTP de la lista diaria de llamadas: GET /llamadas-del-dia (solo
REST, sin tool MCP; plan: docs/Plan_Endpoint_Listado_WF3.txt).

Lo llama WF3 (n8n) cada día laborable a las 9:00, nunca un agente de IA:
devuelve nombres y teléfonos de todos los clientes pendientes. Por eso usa
la llave del Gate (X-Gate-Secret), que la herramienta del Agente 2 no
tiene (plan, 1.3).

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
Traduce HTTP -> llamada a services/ -> HTTP.
"""

# sys: para escribir la línea [AVISO] en la salida de errores (stderr).
import sys

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (declara
# una dependencia que se ejecuta antes del endpoint) y HTTPException (corta
# y responde con un código de error).
from fastapi import APIRouter, Depends, HTTPException

# La dependencia que comprueba la cabecera X-Gate-Secret (la misma de POST
# /gate-decisions y GET /gate-avisos). NO verificar_webhook_secret: esa
# llave la tiene el Agente 2 (pre-decisión 2).
from app.api.security import verificar_gate_secret

# El esquema de la respuesta: lo único que puede salir.
from app.schemas.listado_llamadas import ListadoLlamadasResponse

# La función del servicio.
from app.services.listado_llamadas_service import obtener_listado

# El único rechazo posible: falta o no es válida una regla (-> 503).
from app.services.reglas_visita import ConfiguracionIncompleta

# APIRouter: un "mini-FastAPI" con las rutas de este archivo. main.py lo
# engancha con include_router. tags agrupa la ruta en /docs bajo
# "administracion".
router = APIRouter(tags=["administracion"])


# @router.get(...) es un DECORADOR: registra la función de debajo como la
# que atiende GET /llamadas-del-dia.
#   response_model: FastAPI construye el JSON SOLO con los campos de
#     ListadoLlamadasResponse (segunda barrera de la minimización).
#   dependencies=[Depends(verificar_gate_secret)]: la llave del Gate. Se
#     ejecuta ANTES que la función, así que sin la cabecera correcta la
#     respuesta es 401 aunque la configuración esté rota (plan, 1.8).
@router.get(
    "/llamadas-del-dia",
    response_model=ListadoLlamadasResponse,
    dependencies=[Depends(verificar_gate_secret)],
)
# def y no async def: obtener_listado hace llamadas BLOQUEANTES a Postgres
# (psycopg2), y FastAPI ejecuta las funciones def en su pool de hilos para
# no congelar el servidor. Sin parámetros: el endpoint no tiene entrada.
def get_llamadas_del_dia() -> ListadoLlamadasResponse:
    """
    Devuelve la lista de llamadas de este momento: Gates sin decisión
    registrada, seguimientos por abrir y abiertos, visitas sin confirmar y
    visitas del próximo día laborable. Nunca emails ni importes. No
    escribe nada.
    """
    # Todo el trabajo lo hace services/.
    try:
        return obtener_listado()
    # Falta o no es válida una regla de reglas_negocio.
    except ConfiguracionIncompleta as error:
        # La traza para administración (P3 del plan): una línea en stderr,
        # que queda en la consola del uvicorn. NO se escribe en logs: el
        # endpoint es de solo lectura. Nombra las claves, nunca un secreto.
        print(
            f"[AVISO] GET /llamadas-del-dia: configuración incompleta en reglas_negocio: {error.faltan}",
            file=sys.stderr,
        )
        # 503 con el mismo formato que /visits y /gate-decisions:
        # {"detail": {"motivo": ..., "mensaje": ..., "faltan": [...]}}.
        raise HTTPException(
            status_code=503,
            detail={"motivo": error.motivo, "mensaje": error.mensaje, "faltan": error.faltan},
        )
