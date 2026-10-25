"""
Endpoint HTTP del cambio o la cancelación de una visita YA confirmada: POST
/cambios-visita (solo REST, sin tool MCP; D46, plan
docs/Plan_Cambio_Visita_D46.txt, sección 3).

Lo llama la pantalla de llamadas de n8n con la que administración atiende al
cliente por teléfono, nunca un agente de IA: por eso lleva la llave de
administración (X-Gate-Secret). El Agente 2 no puede cambiar ni cancelar una
visita confirmada (D46.1): su llave (X-Webhook-Secret) da 401.

Como todo app/api/, es un ADAPTADOR: sin lógica de negocio ni SQL. Aquí
vive también la traducción de un rechazo a HTTP.
"""

# De FastAPI: APIRouter (agrupa las rutas de este archivo), Depends (una
# dependencia que se ejecuta antes del endpoint), HTTPException (corta y
# responde con un código de error) y Response (para cambiar el código).
from fastapi import APIRouter, Depends, HTTPException, Response

# La dependencia que comprueba la cabecera X-Gate-Secret.
from app.api.security import verificar_gate_secret
# Los esquemas de entrada y de salida.
from app.schemas.cambios_visita import CambioVisitaCreate, CambioVisitaResponse
# Los rechazos propios del servicio y la función que se llama.
from app.services.cambios_visita_service import OportunidadNoEncontrada, VisitaNoModificable, registrar_cambio
# Los rechazos compartidos con /visits y /resultados-llamada, y su base común.
from app.services.reglas_visita import ConfiguracionIncompleta, FechaNoValida, HuecoOcupado, RechazoNegocio

# El router de este archivo; main.py lo engancha con include_router. tags
# agrupa la ruta en /docs.
router = APIRouter(tags=["cambios-visita"])

# Qué código HTTP corresponde a cada familia de rechazo (plan, sección 3):
#   404 no existe la oportunidad; 409 la visita no se puede tocar
#   (visita_ya_cancelada, sin_visita_confirmada, visita_cambiada,
#   visita_pasada) o el hueco lo ocupa otra visita (hueco_ocupado, D31); 422
#   la fecha nueva incumple una regla de negocio (distinto del 422 de
#   Pydantic); 503 falta configuración de visitas (solo al cambiar).
CODIGO_HTTP = {
    OportunidadNoEncontrada: 404,
    VisitaNoModificable: 409,
    FechaNoValida: 422,
    ConfiguracionIncompleta: 503,
    HuecoOcupado: 409,
}


def http_de_rechazo(error: RechazoNegocio) -> HTTPException:
    """
    Convierte un rechazo de services/ en la HTTPException que hay que
    lanzar, con el formato de siempre: {"detail": {"motivo": ...,
    "mensaje": ...}} y, si existen, "faltan" (503), "huecos_libres" (409
    hueco_ocupado) y "horas_validas" (422 fuera_de_rejilla). El mismo
    formato que POST /resultados-llamada.
    """
    # El motivo (código estable) y el mensaje (texto para administración).
    detalle = {"motivo": error.motivo, "mensaje": error.mensaje}
    # Las tres listas opcionales, cada una solo en su familia: getattr da
    # None en las demás, y entonces no se añade.
    for clave in ("faltan", "huecos_libres", "horas_validas"):
        # El atributo, o None.
        valor = getattr(error, clave, None)
        # Solo si existe.
        if valor is not None:
            # Al detalle, con el mismo nombre.
            detalle[clave] = valor
    # type(error): la clase concreta, para buscar su código en la tabla.
    return HTTPException(status_code=CODIGO_HTTP[type(error)], detail=detalle)


# El decorador registra la función de debajo como la que atiende POST
# /cambios-visita:
#   response_model: el JSON sale SOLO con los campos de CambioVisitaResponse
#     (segunda barrera contra importes y contra la dirección de la obra).
#   status_code=201 por defecto; una repetición lo cambia a 200.
#   dependencies: X-Gate-Secret, ANTES de validar el cuerpo (401 aunque el
#     cuerpo esté mal). Nunca la llave del Agente 2.
@router.post(
    "/cambios-visita",
    response_model=CambioVisitaResponse,
    status_code=201,
    dependencies=[Depends(verificar_gate_secret)],
)
# def y no async def: el servicio hace llamadas BLOQUEANTES a Postgres, y
# FastAPI ejecuta las funciones def en su pool de hilos.
#   data: el cuerpo ya validado con CambioVisitaCreate (si no cumple,
#     FastAPI responde 422 sin llamar a esta función).
#   response: la respuesta HTTP, para poder cambiar su código.
def post_cambios_visita(data: CambioVisitaCreate, response: Response) -> CambioVisitaResponse:
    """
    Cambia (accion=cambiar, con la fecha y hora nuevas) o cancela
    (accion=cancelar, con su motivo) la visita confirmada y futura de la
    oportunidad. Siempre con informe. Nunca devuelve importes ni la
    dirección de la obra.
    """
    # Todo el trabajo lo hace services/; un rechazo se traduce a HTTP.
    try:
        resultado = registrar_cambio(data)
    # RechazoNegocio incluye los propios y los compartidos (fecha, hueco, 503).
    except RechazoNegocio as error:
        raise http_de_rechazo(error)
    # Repetición exacta: no se ha creado nada, 200 en vez de 201.
    if not resultado.creado:
        response.status_code = 200
    # FastAPI lo filtra con response_model y lo envía como JSON.
    return resultado
