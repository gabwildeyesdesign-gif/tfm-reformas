"""
Esquemas de datos de POST /create-followup-task: abrir el seguimiento de
48 h de una oportunidad sin Gate que no ha pedido visita (plan:
docs/Plan_Endpoint_Create_Followup_Task.txt, secciones 2.4 y 2.6).

Aquí solo se comprueba la FORMA de los datos. Las reglas que dependen de la
base de datos (que la oportunidad exista, que cumpla la condición de
seguimiento...) viven en app/services/followup_service.py.
"""

# datetime: el tipo de fecha_apertura (fecha y hora con zona).
from datetime import datetime

# Enum: la base de las listas cerradas de valores.
from enum import Enum

# BaseModel: la base de todos los esquemas de Pydantic.
# ConfigDict: su configuración (aquí, prohibir campos de más).
# Field: restricciones de un campo (aquí, "mayor que 0").
from pydantic import BaseModel, ConfigDict, Field

# Los ocho estados de una oportunidad, copiados del CHECK real de la base de
# datos (common.py). Se reutiliza en el ESQUEMA de la respuesta, como en GET
# /gate-avisos.
from app.schemas.common import EstadoOportunidad


class MotivoSeguimiento(str, Enum):
    """
    Por qué se abre el seguimiento. En N0, UN solo valor (D25.13):
    'sin_decision_post_visita' queda para N1, así que cualquier otro valor,
    también ese, da 422 de Pydantic (P3 del plan, opción a).

    (str, Enum): cada valor es también un texto normal, que psycopg2 envía
    a Postgres tal cual (mismo patrón que DecisionGate).
    """

    # 48 h desde el presupuesto sin pedir visita (Adenda, sección 2).
    SIN_RESPUESTA_VISITA = "sin_respuesta_visita"


class FollowupTaskCreate(BaseModel):
    """
    Cuerpo de POST /create-followup-task.

    Aquí SÍ se acepta un identificador (oportunidad_id): quien llama es un
    workflow de n8n (WF3), no un LLM (plan, 2.4).
    """

    # extra="forbid": un campo que no esté declarado aquí da 422.
    model_config = ConfigDict(extra="forbid")

    # La oportunidad cuyo seguimiento se abre. gt=0: mayor que 0.
    oportunidad_id: int = Field(gt=0)

    # Obligatorio aunque solo tenga un valor: así el contrato no cambia de
    # forma cuando N1 añada el segundo (plan, 2.4).
    motivo: MotivoSeguimiento


class FollowupTaskResponse(BaseModel):
    """
    Respuesta de POST /create-followup-task (plan, 2.6).

    Sin importes ni datos personales: la consulta del servicio no los lee,
    y FastAPI construye el JSON SOLO con los campos declarados aquí.
    """

    # extra="forbid": tampoco el servicio puede añadir un campo no declarado.
    model_config = ConfigDict(extra="forbid")

    # Id del log 'seguimiento_abierto': el nuevo (201) o el original (200).
    # None SOLO si la oportunidad ya estaba en 'seguimiento_pendiente' sin
    # ningún log de apertura (estado puesto a mano; P5 del plan).
    log_id: int | None

    # La oportunidad de la petición.
    oportunidad_id: int

    # El motivo con el que se abrió (en N0 siempre 'sin_respuesta_visita').
    motivo: MotivoSeguimiento

    # Estado de la oportunidad tras la llamada: 'seguimiento_pendiente'. Es
    # el "status" de la Adenda, fila 5, con el nombre de los endpoints
    # recientes (P6).
    estado_oportunidad: EstadoOportunidad

    # Cuándo se abrió el seguimiento (logs.created_at), en hora de Madrid
    # con su desfase. None en el mismo caso que log_id.
    fecha_apertura: datetime | None

    # True si esta llamada ha abierto el seguimiento (201); False si ya
    # estaba abierto y no se ha escrito nada (200).
    creado: bool
