"""
Endpoint HTTP de la disponibilidad de visitas: GET /huecos-libres (D32, plan
docs/Plan_Horario_Visitas_D32.txt, secciones 3 y 4). Solo REST; el Agente 2
tiene la misma consulta como tool MCP (consultar_huecos_libres), que llama a
las mismas funciones del servicio.

Lo llama el formulario de n8n con el que administración registra el
resultado de una llamada: la página "Día" (sin parámetros: modo días) y la
página "Hora" (?fecha=AAAA-MM-DD: modo un día, recalculado al abrirla).

La llave de administración (X-Gate-Secret) va en el ROUTER, como en la
ficha (P8 de su plan): cualquier ruta que se añada a este archivo la hereda.
Sin escribir nada: 200, 401, 422 de forma o 503 (sin log, con [AVISO]).

Como todo app/api/, es un ADAPTADOR: no tiene lógica de negocio ni SQL.
"""

# sys: para escribir el [AVISO] del 503 en stderr.
import sys

# Annotated: pegar a un tipo información extra (aquí, que viene de la query).
from typing import Annotated

# De FastAPI: APIRouter, Depends, HTTPException y Query (leer un modelo de
# Pydantic desde los parámetros de la query).
from fastapi import APIRouter, Depends, HTTPException, Query

# La dependencia que comprueba X-Gate-Secret. NO verificar_webhook_secret:
# esa llave la tiene el Agente 2.
from app.api.security import verificar_gate_secret

# Los esquemas de la query y de las dos respuestas.
from app.schemas.huecos_libres import ConsultaHuecos, HuecosDiaResponse, HuecosDiasResponse

# Las dos funciones del servicio.
from app.services.huecos_libres_service import consultar_dia, consultar_dias

# El único rechazo posible: falta o no es válida una regla (-> 503).
from app.services.reglas_visita import ConfiguracionIncompleta

# El router de este archivo, con la llave de administración para TODAS sus
# rutas: se ejecuta antes de validar la query, así que sin la cabecera
# correcta la respuesta es 401 aunque la fecha esté mal escrita.
router = APIRouter(tags=["administracion"], dependencies=[Depends(verificar_gate_secret)])


# Registra la función de debajo como la que atiende GET /huecos-libres.
#   response_model: uno de los dos modelos (FastAPI construye el JSON solo
#     con sus campos: segunda barrera contra datos de otras visitas).
@router.get("/huecos-libres", response_model=HuecosDiasResponse | HuecosDiaResponse)
# def y no async def: el servicio hace llamadas BLOQUEANTES a Postgres, y
# FastAPI ejecuta las funciones def en su pool de hilos.
def get_huecos_libres(
    # La query como un modelo de Pydantic: ?fecha=AAAA-MM-DD o nada; un
    # parámetro de más o una fecha mal escrita dan 422 sin llegar aquí.
    consulta: Annotated[ConsultaHuecos, Query()],
) -> HuecosDiasResponse | HuecosDiaResponse:
    """
    Sin fecha: los próximos días laborables (desde hoy) con al menos una
    hora libre, cada uno con sus horas por franja y la etiqueta del
    desplegable. Con fecha: las horas libres de ese día, por franja, y el
    motivo si no tiene ninguna. Nunca datos de otras visitas. No escribe nada.
    """
    # Todo el trabajo lo hace services/.
    try:
        # Sin fecha: modo días.
        if consulta.fecha is None:
            # Los días con hueco.
            return consultar_dias()
        # Con fecha: modo un día.
        return consultar_dia(consulta.fecha)
    # Falta o no es válida una regla de reglas_negocio.
    except ConfiguracionIncompleta as error:
        # La traza para administración: una línea en stderr (consola del
        # uvicorn). NO se escribe en logs: el endpoint es de solo lectura.
        print(f"[AVISO] GET /huecos-libres: configuración incompleta en reglas_negocio: {error.faltan}",
              file=sys.stderr)
        # 503 con el formato de siempre.
        raise HTTPException(
            status_code=503,
            detail={"motivo": error.motivo, "mensaje": error.mensaje, "faltan": error.faltan},
        )
