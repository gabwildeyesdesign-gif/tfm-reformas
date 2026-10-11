"""
Esquemas de datos de POST /resultados-llamada: el resultado de la llamada de
administración a un cliente (plan: docs/Plan_Resultado_Llamadas.txt,
secciones 2.3 y 2.5).

Bloque B1 (2026-10-11): mismas reglas que POST /gate-decisions
(app/schemas/gate_decisions.py), con los nombres NUEVOS:
  decision        -> resultado
  decision_id     -> llamada_id
  fecha_propuesta -> fecha_visita
Hoy solo admite los dos resultados del Gate; los tres de la confirmación de
visitas y el campo tipo_llamada llegan en el Bloque B2.

Aquí solo se comprueba la FORMA de los datos. Las reglas que dependen de la
base de datos viven en app/services/resultados_llamada_service.py.
"""

# date (un día), time (una hora del día) y datetime (día y hora con su
# desfase), de la librería estándar.
from datetime import date, datetime, time

# Self: el tipo "una instancia de esta misma clase" (lo devuelve el
# validador de modelo).
from typing import Self

# Las piezas de Pydantic: el modelo base, su configuración, Field (límites
# de un campo) y los dos tipos de validador.
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Los MISMOS validadores de formato que POST /visits y POST /gate-decisions
# (compartidos en common.py) y el límite del informe.
from app.schemas.common import (
    MAX_INFORME_GATE,
    fecha_en_formato_exacto,
    hora_en_formato_exacto,
    texto_sin_espacios_en_los_extremos,
)

# Los Enum de los resultados y de los motivos: en B1 son los del Gate, sin
# copiarlos (siguen en gate_decisions.py, donde también los usan GET
# /gate-avisos y la ficha). B2 creará los del contrato ampliado.
from app.schemas.gate_decisions import DecisionGate, MotivoDescarte


class ResultadoLlamadaCreate(BaseModel):
    """
    Cuerpo de POST /resultados-llamada. Lo envía el formulario de n8n que
    rellena administración, no un LLM: por eso SÍ lleva oportunidad_id.
    """

    # extra="forbid": un campo no declarado da 422 en vez de ignorarse.
    model_config = ConfigDict(extra="forbid")

    # Entero mayor que 0: los id SERIAL de Postgres empiezan en 1.
    oportunidad_id: int = Field(gt=0)

    # Obligatorio. Un valor fuera del Enum da 422.
    resultado: DecisionGate

    # Pueden faltar (None); si son obligatorios o están prohibidos lo
    # decide el validador de modelo del final, según el resultado.
    fecha: date | None = None
    hora: time | None = None

    # Solo con descartar. Un valor fuera del Enum da 422.
    motivo: MotivoDescarte | None = None

    # Siempre obligatorio, de 1 a MAX_INFORME_GATE caracteres contados
    # DESPUÉS de recortar los extremos (lo hace el validador de abajo).
    informe: str = Field(min_length=1, max_length=MAX_INFORME_GATE)

    # mode="before": se ejecutan sobre lo que llegó en el JSON, antes de
    # convertirlo al tipo del campo. @classmethod: Pydantic 2 los llama con
    # la clase, antes de que exista el objeto.

    # Formato YYYY-MM-DD del campo "fecha" (None pasa: es opcional).
    @field_validator("fecha", mode="before")
    @classmethod
    def validar_fecha(cls, valor):
        # Sin fecha, nada que comprobar; con fecha, el validador compartido.
        if valor is None:
            return None
        return fecha_en_formato_exacto(valor)

    # Formato HH:MM del campo "hora" (mismo criterio).
    @field_validator("hora", mode="before")
    @classmethod
    def validar_hora(cls, valor):
        # Sin hora, nada; con hora, el validador compartido.
        if valor is None:
            return None
        return hora_en_formato_exacto(valor)

    # Recorta los extremos del informe antes de medirlo (se guarda así: el
    # CHECK de la base de datos exige informe = btrim(informe)).
    @field_validator("informe", mode="before")
    @classmethod
    def recortar_informe(cls, valor):
        # El recorte compartido (strip).
        return texto_sin_espacios_en_los_extremos(valor)

    # mode="after": mira el objeto ENTERO ya construido. La misma regla que
    # GateDecisionCreate y que el CHECK chk_decisiones_gate_coherencia,
    # adelantada a la puerta para dar un 422 claro.
    @model_validator(mode="after")
    def comprobar_combinacion(self) -> Self:
        # Una visita acordada necesita fecha Y hora, y no lleva motivo.
        if self.resultado == DecisionGate.VISITA_ACORDADA:
            # Sin fecha o sin hora: 422.
            if self.fecha is None or self.hora is None:
                raise ValueError("con resultado=visita_acordada, fecha y hora son obligatorias")
            # Con motivo: 422 (no se ha descartado nada).
            if self.motivo is not None:
                raise ValueError("con resultado=visita_acordada no se admite motivo")
        # Solo queda descartar: necesita motivo, y no lleva fecha ni hora.
        else:
            # Sin motivo: 422.
            if self.motivo is None:
                raise ValueError("con resultado=descartar, motivo es obligatorio")
            # Con fecha o con hora: 422 (un descarte no tiene visita).
            if self.fecha is not None or self.hora is not None:
                raise ValueError("con resultado=descartar no se admiten fecha ni hora")
        # Un validador "after" devuelve el propio objeto.
        return self


class ResultadoLlamadaResponse(BaseModel):
    """
    Respuesta de POST /resultados-llamada. NINGÚN campo de importe, a
    propósito (segunda barrera, como en /gate-decisions y /visits).
    """

    # Id de la fila de decisiones_gate (la tabla conserva su nombre, P3 = b).
    llamada_id: int
    # La oportunidad de la petición.
    oportunidad_id: int
    # El resultado REGISTRADO (en una repetición, el que ya estaba).
    resultado: DecisionGate
    # El motivo registrado, o None.
    motivo: MotivoDescarte | None
    # Estado de la oportunidad tras el registro.
    estado_oportunidad: str
    # La visita creada, o None.
    visita_id: int | None
    # Su inicio en hora de Madrid con su desfase, o None.
    fecha_visita: datetime | None
    # True si se escribió algo (201); False en una repetición (200).
    creado: bool
