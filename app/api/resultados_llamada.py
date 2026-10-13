"""
Endpoint HTTP del resultado de las llamadas de administración: POST
/resultados-llamada (solo REST, sin tool MCP). Plan:
docs/Plan_Resultado_Llamadas.txt.

Lo llama el formulario de n8n con el que administración registra el
resultado de una llamada, nunca un agente de IA: por eso lleva la llave de
administración (X-Gate-Secret, que conserva su nombre, D25.9).

Bloque B1 (2026-10-11): misma operación que POST /gate-decisions (que queda
como alias en app/api/gate_decisions.py) con los nombres nuevos. Bloque B2
(el mismo día): los tres tipos de llamada (gate, seguimiento y confirmación
de visita) y sus cinco resultados; el tipo lo deduce el backend.

Como todo app/api/, es un ADAPTADOR: sin lógica de negocio ni SQL. Aquí
vive también la traducción de un rechazo a HTTP, que comparte el alias.
"""

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (una
# dependencia que se ejecuta antes del endpoint), HTTPException (corta y
# responde con un código de error) y Response (para cambiar el código).
from fastapi import APIRouter, Depends, HTTPException, Response

# La dependencia que comprueba la cabecera X-Gate-Secret.
from app.api.security import verificar_gate_secret
# Los esquemas NUEVOS de entrada y de salida.
from app.schemas.resultados_llamada import ResultadoLlamadaCreate, ResultadoLlamadaResponse
# Los rechazos propios del servicio y la función que se llama.
from app.services.resultados_llamada_service import (
    EstadoNoPermiteResultado,
    OportunidadNoEncontrada,
    registrar_resultado,
)
# Los rechazos compartidos con /visits y su base común.
from app.services.reglas_visita import ConfiguracionIncompleta, FechaNoValida, HuecoOcupado, RechazoNegocio

# El router de este archivo; main.py lo engancha con include_router. tags
# agrupa la ruta en /docs.
router = APIRouter(tags=["resultados-llamada"])

# Qué código HTTP corresponde a cada familia de rechazo (la tabla de la
# Adenda 5.5, la misma que tenía app/api/gate_decisions.py):
#   404 no existe la oportunidad; 409 la oportunidad no admite ese
#   resultado; 422 la fecha incumple una regla de negocio (distinto del 422
#   de Pydantic); 503 falta configuración de visitas.
#   409 también, desde D31: el hueco lo ocupa otra visita (hueco_ocupado),
#   en la ruta nueva y en el alias (P16 y P17).
CODIGO_HTTP = {
    OportunidadNoEncontrada: 404,
    EstadoNoPermiteResultado: 409,
    FechaNoValida: 422,
    ConfiguracionIncompleta: 503,
    HuecoOcupado: 409,
}


def http_de_rechazo(error: RechazoNegocio) -> HTTPException:
    """
    Convierte un rechazo de services/ en la HTTPException que hay que
    lanzar, con el formato de siempre: {"detail": {"motivo": ...,
    "mensaje": ...}} y, en el 503, también "faltan". La usan la ruta nueva
    y el alias, para que respondan exactamente igual.
    """
    # El motivo (código estable) y el mensaje (texto para administración).
    detalle = {"motivo": error.motivo, "mensaje": error.mensaje}
    # Solo ConfiguracionIncompleta tiene "faltan"; getattr da None en las
    # demás, y entonces no se añade.
    faltan = getattr(error, "faltan", None)
    if faltan is not None:
        detalle["faltan"] = faltan
    # D31: solo HuecoOcupado tiene "huecos_libres"; se añade si existe.
    huecos = getattr(error, "huecos_libres", None)
    # Lista (quizá vacía) de horas de inicio libres de ese día.
    if huecos is not None:
        detalle["huecos_libres"] = huecos
    # D32 (P2): solo el 422 fuera_de_rejilla tiene "horas_validas" (la
    # rejilla por franja); FechaNoValida la tiene a None en los demás motivos.
    validas = getattr(error, "horas_validas", None)
    # Se añade al detalle solo si no es None.
    if validas is not None:
        # {"manana": [...], "tarde": [...]}.
        detalle["horas_validas"] = validas
    # type(error): la clase concreta, para buscar su código en la tabla.
    return HTTPException(status_code=CODIGO_HTTP[type(error)], detail=detalle)


# El decorador registra la función de debajo como la que atiende POST
# /resultados-llamada:
#   response_model: el JSON sale SOLO con los campos de
#     ResultadoLlamadaResponse (segunda barrera contra importes).
#   status_code=201 por defecto; una repetición lo cambia a 200.
#   dependencies: X-Gate-Secret, ANTES de validar el cuerpo (401 aunque el
#     cuerpo esté mal). Nunca la llave del Agente 2.
@router.post(
    "/resultados-llamada",
    response_model=ResultadoLlamadaResponse,
    status_code=201,
    dependencies=[Depends(verificar_gate_secret)],
)
# def y no async def: el servicio hace llamadas BLOQUEANTES a Postgres, y
# FastAPI ejecuta las funciones def en su pool de hilos.
#   data: el cuerpo ya validado con ResultadoLlamadaCreate (si no cumple,
#     FastAPI responde 422 sin llamar a esta función).
#   response: la respuesta HTTP, para poder cambiar su código.
def post_resultados_llamada(data: ResultadoLlamadaCreate, response: Response) -> ResultadoLlamadaResponse:
    """
    Registra el resultado de la llamada de administración: visita_acordada
    o descartar (Gate y seguimiento), y confirmar_hora, otra_hora o
    cliente_cancela (confirmación de una visita pedida desde el chat). El
    resultado es definitivo. Nunca devuelve importes.
    """
    # Todo el trabajo lo hace services/; un rechazo se traduce a HTTP.
    try:
        resultado = registrar_resultado(data)
    # RechazoNegocio incluye los propios y los compartidos (fecha, 503).
    except RechazoNegocio as error:
        raise http_de_rechazo(error)
    # Repetición exacta: no se ha creado nada, 200 en vez de 201.
    if not resultado.creado:
        response.status_code = 200
    # FastAPI lo filtra con response_model y lo envía como JSON.
    return resultado
