"""
Endpoint HTTP POST /calculate-estimate: la puerta REST del cálculo de
presupuestos.

Es un ADAPTADOR delgado, con el mismo patrón que app/api/leads.py: recibe
la petición HTTP, llama a la función de services/ y traduce el resultado,
o sus errores, al idioma HTTP. Aquí no hay SQL ni ninguna regla de
negocio.

Para qué existe esta puerta (Adenda, punto 1): en producción, quien
calcula presupuestos es el agente de IA por la puerta MCP. Esta puerta
REST sirve para pruebas y administración manual por personas de
confianza. Por eso, a diferencia de la tool MCP, devuelve SIEMPRE los
importes, también cuando hay cambios estructurales.

Y por eso mismo va protegida con el mismo secreto que POST /leads
(X-Webhook-Secret). Si quedara abierta, cualquiera podría escribir
presupuestos, y ocultarle los importes al agente por MCP no serviría de
nada, porque bastaría con pedirlos por aquí.
"""

# APIRouter: un "mini-FastAPI" donde se agrupan los endpoints de un
# dominio. main.py lo engancha a la aplicación con include_router. Se usa
# en vez de declarar el endpoint directamente en main.py para mantener un
# archivo por dominio, igual que leads.py.
#
# Depends: le dice a FastAPI "antes de ejecutar el endpoint, ejecuta esta
# otra función". Aquí, la comprobación del secreto.
#
# HTTPException: la forma de cortar la petición y responder con un código
# de error concreto.
from fastapi import APIRouter, Depends, HTTPException

from app.api.security import verificar_webhook_secret
from app.schemas.estimates import EstimateRequest, EstimateResponse
from app.services.estimate_service import (
    EstadoNoPermiteCalculo,
    OportunidadNoEncontrada,
    calculate_estimate,
)

# tags agrupa el endpoint bajo el título "estimates" en la página /docs.
router = APIRouter(tags=["estimates"])


# QUÉ ES UN DECORADOR
#
# Una línea que empieza por @ justo encima de una función es un
# decorador: una función que recibe la función de debajo y hace algo con
# ella. @router.post(...) no cambia lo que hace post_calculate_estimate:
# la REGISTRA en el router y le dice a FastAPI "cuando llegue un POST a
# /calculate-estimate, llama a esta función".
#
# Los parámetros del decorador:
#   - response_model=EstimateResponse: FastAPI valida la respuesta contra
#     ese modelo y lo usa para documentarla en /docs.
#   - status_code=200 y no 201 (decisión P8 del plan): es una llamada del
#     tipo "calcula", que además puede devolver un presupuesto que ya
#     existía. Para saber si se ha creado ahora está el campo creado de
#     la respuesta. Un 201 ("creado") mentiría en los reintentos.
#   - dependencies=[Depends(verificar_webhook_secret)]: la misma
#     comprobación del secreto que POST /leads, reutilizada tal cual, sin
#     ningún cambio. Se ejecuta ANTES que la función, así que sin secreto
#     la respuesta es 401 y ni siquiera se valida el cuerpo ni se toca la
#     base de datos.
@router.post(
    "/calculate-estimate",
    response_model=EstimateResponse,
    status_code=200,
    dependencies=[Depends(verificar_webhook_secret)],
)
def post_calculate_estimate(data: EstimateRequest) -> EstimateResponse:
    """
    Calcula (o recupera) el presupuesto de una oportunidad.

    Como en post_leads, la anotación "data: EstimateRequest" hace que
    FastAPI valide el cuerpo con Pydantic ANTES de llamar a la función.
    Si falta oportunidad_id, si no es un entero positivo o si el cuerpo
    trae campos de más (por ejemplo m2), responde 422 por su cuenta y
    esta función no llega a ejecutarse.

    Es def y no async def por el mismo motivo que post_leads: el servicio
    hace llamadas BLOQUEANTES a Postgres con psycopg2. FastAPI ejecuta
    las funciones def en su pool de hilos, así que una petición que
    espera a Supabase no congela las demás.

    Traducción de los errores de negocio a HTTP:
      - OportunidadNoEncontrada -> 404 Not Found: el recurso pedido no
        existe.
      - EstadoNoPermiteCalculo  -> 409 Conflict: la petición es correcta,
        pero choca con el estado actual del recurso. Reintentarla no la
        arreglaría, así que es un 4xx: n8n falla rápido y no reintenta
        (política de la Adenda).
    Cualquier otro error inesperado no se captura aquí: llega al
    manejador global de app/main.py, que lo registra en logs y responde
    500.
    """
    # try/except: se intenta ejecutar el bloque try; si salta una de las
    # excepciones nombradas en un except, se ejecuta ese bloque en lugar
    # de dejar que el error suba.
    try:
        return calculate_estimate(data.oportunidad_id)
    except OportunidadNoEncontrada:
        # El mensaje es corto y no incluye detalles internos (el texto de
        # la excepción podría cambiar y acabar revelando algo que no
        # debe). El id ya lo conoce quien lo ha enviado.
        raise HTTPException(status_code=404, detail="Oportunidad no encontrada")
    except EstadoNoPermiteCalculo:
        raise HTTPException(
            status_code=409,
            detail="La oportunidad no está en un estado que permita calcular un presupuesto",
        )
