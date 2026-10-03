"""
Esquemas de datos de POST /gate-decisions: el resultado de la llamada de
administración a un cliente cuyo presupuesto activó el Gate (plan:
docs/Plan_Endpoint_Gate_Decisions.txt, secciones 1.3 y 1.5).

Aquí solo se comprueba la FORMA de los datos: tipos, valores permitidos,
longitudes y qué campos acompañan a cada decisión. Las reglas que dependen
de la base de datos (que la oportunidad exista, que tenga Gate, que la fecha
sea futura y caiga en una franja...) viven en
app/services/gate_decisions_service.py.
"""

# Los tipos de fecha y hora de la librería estándar: date (un día), time
# (una hora del día) y datetime (día y hora juntos, con su desfase).
from datetime import date, datetime, time
# Enum: para los conjuntos CERRADOS de valores (DecisionGate, MotivoDescarte).
from enum import Enum

# Self (Python 3.11+): el tipo "una instancia de esta misma clase". Lo usa
# el validador de modelo, que devuelve el propio objeto ya comprobado.
from typing import Self

# model_validator: un validador que mira el objeto ENTERO, no un campo
# suelto. Hace falta porque las reglas de la sección 1.3 relacionan varios
# campos ("con descartar, motivo obligatorio y fecha prohibida").
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Los MISMOS validadores de formato que usa VisitaCreate (POST /visits),
# compartidos en common.py: fecha y hora aceptan exactamente los mismos
# formatos en los dos endpoints (plan, 2.3).
from app.schemas.common import (
    MAX_INFORME_GATE,
    fecha_en_formato_exacto,
    hora_en_formato_exacto,
    texto_sin_espacios_en_los_extremos,
)


class DecisionGate(str, Enum):
    """
    Las dos decisiones posibles tras la llamada (plan, 1.3).

    Mismo patrón que TipoReforma en common.py: (str, Enum) hace que cada
    valor sea también un texto normal, que psycopg2 envía a Postgres tal
    cual. Los valores coinciden con el CHECK chk_decisiones_gate_decision
    (migración paso10): si se añade uno aquí, hay que añadirlo también allí.
    """

    # El cliente sigue adelante: sin ver la obra no hay presupuesto
    # definitivo, así que "seguir" equivale a acordar una visita.
    VISITA_ACORDADA = "visita_acordada"
    # El cliente no sigue (o no se le localiza): la oportunidad se pierde.
    DESCARTAR = "descartar"


class MotivoDescarte(str, Enum):
    """
    Por qué se descarta (solo con decision = descartar). Valores copiados
    del CHECK chk_decisiones_gate_motivo (migración paso10).
    """

    # A la izquierda el nombre en Python; a la derecha el valor que viaja en
    # el JSON y se guarda en decisiones_gate.motivo.
    PRECIO = "precio"
    PLAZO = "plazo"
    NO_CONTESTA = "no_contesta"
    PROYECTO_NO_VIABLE = "proyecto_no_viable"
    OTRO = "otro"


class GateDecisionCreate(BaseModel):
    """
    Cuerpo de POST /gate-decisions.

    Aquí SÍ se acepta un identificador (oportunidad_id), a diferencia de
    POST /visits: quien llama es un formulario de n8n que rellena una
    persona de administración, no un LLM (plan, 1.1).
    """

    # extra="forbid": un campo no declarado (por ejemplo presupuesto_id)
    # da 422 en vez de ignorarse en silencio.
    model_config = ConfigDict(extra="forbid")

    # Entero mayor que 0: los id SERIAL de Postgres empiezan en 1.
    oportunidad_id: int = Field(gt=0)

    # Obligatorio. Un valor fuera de DecisionGate ("continuar") da 422.
    decision: DecisionGate

    # "date | None = None": el campo puede faltar (y entonces vale None).
    # Si es obligatorio o está prohibido depende de la decisión; eso lo
    # decide el validador de modelo del final.
    fecha: date | None = None

    # Hora de INICIO de la visita, en hora de Madrid. Misma regla.
    hora: time | None = None

    # Solo con descartar. Un valor fuera de MotivoDescarte ("caro") da 422.
    motivo: MotivoDescarte | None = None

    # SIEMPRE obligatorio: todo contacto queda registrado para la auditoría
    # anual. De 1 a MAX_INFORME_GATE caracteres, contados DESPUÉS de quitar
    # los espacios de los extremos (lo hace el validador de abajo): un
    # informe de solo espacios queda vacío y lo rechaza min_length=1.
    informe: str = Field(min_length=1, max_length=MAX_INFORME_GATE)

    # ------------------------------------------------------------------
    # Validadores de formato (un campo cada uno)
    # ------------------------------------------------------------------
    # mode="before": se ejecutan ANTES de que Pydantic convierta el valor
    # al tipo del campo, sobre lo que llegó en el JSON. @classmethod es
    # obligatorio para @field_validator en Pydantic 2: el validador se
    # ejecuta antes de que exista el objeto, así que recibe la clase (cls)
    # y no una instancia (self).

    # Engancha la comprobación de formato YYYY-MM-DD al campo "fecha".
    @field_validator("fecha", mode="before")
    @classmethod
    def validar_fecha(cls, valor):
        # Diferencia con VisitaCreate: aquí la fecha es OPCIONAL. Si llega
        # "fecha": null, se deja pasar el None; si no, el mismo validador
        # compartido que /visits. (Si el campo no viene, Pydantic no llama
        # a este validador: usa directamente el valor por defecto, None.)
        if valor is None:
            return None
        return fecha_en_formato_exacto(valor)

    # Engancha la comprobación de formato HH:MM al campo "hora".
    @field_validator("hora", mode="before")
    @classmethod
    def validar_hora(cls, valor):
        # Mismo criterio que la fecha: None pasa, lo demás, formato HH:MM.
        if valor is None:
            return None
        return hora_en_formato_exacto(valor)

    # Recorta los espacios del "informe" antes de medir su longitud.
    @field_validator("informe", mode="before")
    @classmethod
    def recortar_informe(cls, valor):
        # Quita espacios, tabuladores y saltos de línea de los extremos. Se
        # guarda YA recortado: el CHECK de la base de datos
        # (informe = btrim(informe)) lo exige.
        return texto_sin_espacios_en_los_extremos(valor)

    # ------------------------------------------------------------------
    # Validador de modelo: qué campos acompañan a cada decisión
    # ------------------------------------------------------------------
    # mode="after": se ejecuta DESPUÉS de validar cada campo por separado,
    # sobre el objeto ya construido (self). Si lanza ValueError, Pydantic
    # lo convierte en un 422 igual que los demás, antes de tocar la base de
    # datos. Es la misma regla que el CHECK chk_decisiones_gate_coherencia,
    # adelantada a la puerta para dar un 422 claro en vez de un 500.

    # "-> Self": devuelve el propio objeto ya comprobado (ver el import).
    @model_validator(mode="after")
    def comprobar_combinacion(self) -> Self:
        if self.decision == DecisionGate.VISITA_ACORDADA:
            # Una visita acordada necesita cuándo: fecha Y hora.
            if self.fecha is None or self.hora is None:
                raise ValueError("con decision=visita_acordada, fecha y hora son obligatorias")
            # Y no tiene motivo de descarte: no se ha descartado nada.
            if self.motivo is not None:
                raise ValueError("con decision=visita_acordada no se admite motivo")
        else:
            # Solo queda DESCARTAR (el Enum no admite otro valor). Un
            # descarte necesita saber por qué...
            if self.motivo is None:
                raise ValueError("con decision=descartar, motivo es obligatorio")
            # ...y no tiene visita: una fecha o una hora aquí indican un
            # error del formulario, y se rechaza en vez de ignorarse.
            if self.fecha is not None or self.hora is not None:
                raise ValueError("con decision=descartar no se admiten fecha ni hora")
        # Un validador de modelo "after" tiene que devolver el objeto.
        return self


class GateDecisionResponse(BaseModel):
    """
    Respuesta de POST /gate-decisions (plan, 1.5).

    NO TIENE NINGÚN CAMPO DE IMPORTE, a propósito. Es la segunda de dos
    barreras, como en POST /visits y GET /leads/session: la consulta de
    services/ no lee los importes, y FastAPI construye el JSON SOLO con los
    campos declarados aquí. En un Gate nunca se envían cifras.
    """

    # Id de la fila de decisiones_gate: la recién creada o, en una
    # repetición, la que ya existía.
    decision_id: int

    # La oportunidad sobre la que se ha decidido (la misma de la petición).
    oportunidad_id: int

    # La decisión REGISTRADA (en una repetición, la que ya estaba).
    decision: DecisionGate

    # El motivo registrado, o None si la decisión fue visita_acordada.
    motivo: MotivoDescarte | None

    # Estado de la oportunidad tras la decisión: 'visita_agendada' o
    # 'perdida' (en una repetición, el que tenga en ese momento).
    estado_oportunidad: str

    # La visita creada al acordarla, o None si se descartó.
    visita_id: int | None

    # Inicio de esa visita en hora de Madrid, con su desfase
    # ("2026-10-23T08:30:00+02:00"), o None si se descartó.
    fecha_propuesta: datetime | None

    # True si esta llamada ha escrito algo; False si es una repetición
    # exacta de la decisión ya registrada. El adaptador responde 201 o 200
    # según este campo.
    creado: bool
