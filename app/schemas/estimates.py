"""
Modelos Pydantic de POST /calculate-estimate y de la tool MCP
calculate_estimate.

Como app/schemas/leads.py, este archivo solo describe FORMAS: qué campos
entran, qué campos salen y de qué tipo son. No calcula nada ni habla con
la base de datos. El cálculo vive en app/services/estimate_service.py.

Los MISMOS dos modelos sirven para las dos puertas (REST y MCP). Es
deliberado: el contrato del endpoint en la Adenda (punto 1.1) es uno
solo, y si cada puerta tuviera su propio modelo acabarían divergiendo.
"""

# Decimal es el tipo de la biblioteca estándar de Python para números
# decimales EXACTOS. El float normal guarda los números en binario, y
# muchos decimales sencillos no tienen representación exacta en binario:
# Decimal(8.7) vale en realidad 8.699999999999999289... (comprobado en
# este proyecto). Para dinero eso es inaceptable, porque los errores se
# acumulan y un importe puede salir con un céntimo de diferencia.
# psycopg2 ya entrega las columnas NUMERIC de Postgres como Decimal, así
# que usar Decimal aquí mantiene el mismo tipo de extremo a extremo.
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import MotivoGate


class EstimateRequest(BaseModel):
    """
    Cuerpo de POST /calculate-estimate.

    Tiene UN solo campo a propósito (Adenda, punto 1.1a). Los datos de
    negocio (tipo de reforma, acabados, m², cambios estructurales) NUNCA
    entran por aquí: el servicio los lee de la base de datos a partir de
    oportunidad_id. Así, ni un LLM que alucine ni una persona que se
    equivoque al teclear pueden alterar el precio ni saltarse el Gate
    diciendo "sin cambios estructurales".
    """

    # extra="forbid": si el cuerpo trae CUALQUIER campo que no esté
    # declarado aquí (por ejemplo m2 o incluye_cambios_estructurales),
    # Pydantic lo rechaza con un 422 en vez de ignorarlo en silencio. Sin
    # esto, quien enviara {"oportunidad_id": 5, "m2": 1} creería que su m2
    # se ha usado, cuando en realidad se habría descartado sin avisar. Es
    # la misma configuración que LeadCreate.
    model_config = ConfigDict(extra="forbid")

    # Field(gt=0): "greater than 0". Los id SERIAL de Postgres empiezan en
    # 1, así que 0 o un negativo nunca pueden existir. Rechazarlos aquí
    # da un 422 inmediato sin gastar una consulta a la base de datos.
    oportunidad_id: int = Field(gt=0, description="Id de la oportunidad a presupuestar")


class EstimateResponse(BaseModel):
    """
    Respuesta de POST /calculate-estimate y de la tool MCP.

    Los importes que salen de aquí llevan el IVA INCLUIDO (D14): son el
    precio final que se le puede enseñar a un cliente particular tal
    cual, sin sumarle nada después. El importe sin IVA no se devuelve; se
    usa dentro del cálculo (el Gate HITL se decide con él) y queda
    guardado en el log de auditoría.

    Tres situaciones posibles, que se distinguen por status:

      1. Presupuesto calculado (ahora o en una llamada anterior):
         status es el estado REAL de la oportunidad, leído de la base de
         datos ('presupuesto_enviado', 'pendiente_aprobacion' o el que
         tenga ahora si ya avanzó). Mismo criterio que D4 en POST /leads:
         nunca una etiqueta escrita aparte que pueda desincronizarse.

      2. No se pudo calcular (falta la tarifa o una regla de negocio):
         status = 'requiere_revision', presupuesto_id e importes a None.
         No se ha creado nada y una persona debe revisarlo (Adenda,
         ejemplo de fallback del punto 5).

      3. Solo por la puerta MCP: presupuesto calculado con cambios
         estructurales. Los importes llegan a None a propósito para que
         el agente no los tenga (ver ocultar_importes_para_agente en el
         servicio), pero presupuesto_id existe y motivo_gate explica por
         qué.
    """

    # int | None significa "un entero, o None". El símbolo | entre tipos
    # (Python 3.10+) se lee "o". Es None solo en el caso 2.
    presupuesto_id: int | None
    oportunidad_id: int

    # POR QUÉ SE LLAMAN AHORA *_con_iva (D14)
    #
    # Antes se llamaban importe_min e importe_max a secas, y ese nombre
    # era un defecto real del contrato de la API: no decía si la cifra
    # llevaba IVA o no. Quien integrara este endpoint (n8n, el agente, o
    # una persona leyendo /docs) tenía que adivinarlo, y adivinar mal
    # significa enseñarle al cliente un precio un 21 % por debajo del que
    # acabará pagando. Un nombre ambiguo en un campo de dinero no es un
    # detalle de estilo: es una fuente de error con consecuencias
    # económicas.
    #
    # El nombre dice ahora exactamente qué es: el precio final, con el
    # IVA ya aplicado, que es el único número que tiene sentido mostrarle
    # a un particular. El desglose sin IVA existe dentro del cálculo y
    # queda registrado en el log de auditoría, pero no se devuelve: el
    # cliente no necesita la base imponible para decidir.
    #
    # Decimal y no float (ver el comentario del import). Al convertir la
    # respuesta a JSON, Pydantic escribe un Decimal como TEXTO: "8625.00"
    # y no 8625.0. Se deja así a propósito (decisión P9 del plan): el
    # texto conserva el valor exacto, con sus dos decimales, y nadie
    # fuera del backend necesita hacer cuentas con él. El Gate ya se
    # decide aquí dentro.
    importe_min_con_iva: Decimal | None
    importe_max_con_iva: Decimal | None

    # None cuando no hay Gate. Si lo hay, uno de los tres valores del
    # CHECK real (ver MotivoGate).
    motivo_gate: MotivoGate | None

    # True si hay que esperar la aprobación de una persona. Se guarda
    # también en presupuestos.requiere_aprobacion. Siempre coincide con
    # "motivo_gate no es None", pero va como campo aparte porque es la
    # pregunta que n8n necesita responder con un simple sí/no.
    requiere_aprobacion: bool

    status: str

    # True si el presupuesto se ha creado en ESTA llamada; False si ya
    # existía (reintento de n8n o segunda llamada del agente) o si no se
    # pudo calcular. Es la señal que usa n8n para no enviar dos veces el
    # aviso de aprobación a Telegram (decisión P2 del plan). El backend
    # no envía nada a Telegram; solo puede avisar a n8n de que el
    # presupuesto no es nuevo.
    creado: bool
