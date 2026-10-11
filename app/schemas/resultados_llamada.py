"""
Esquemas de datos de POST /resultados-llamada: el resultado de la llamada de
administración a un cliente (plan: docs/Plan_Resultado_Llamadas.txt,
secciones 2.3 y 2.5, con sus notas fechadas).

Nombres nuevos respecto a POST /gate-decisions (que conserva los suyos en
app/schemas/gate_decisions.py, como alias):
  decision        -> resultado
  decision_id     -> llamada_id
  fecha_propuesta -> fecha_visita

Bloque B2 (2026-10-11): tres tipos de llamada y cinco resultados.
  gate, seguimiento:    visita_acordada, descartar
  confirmacion_visita:  confirmar_hora, otra_hora, cliente_cancela
Y un motivo más en el catálogo, 'sin_decision' (D29.3), que el alias NO
acepta (su Enum MotivoDescarte no cambia).

Aquí solo se comprueba la FORMA de los datos. Que el resultado sea del tipo
de llamada de esa oportunidad (que se sabe con la base de datos) lo decide
app/services/resultados_llamada_service.py.
"""

# date (un día), time (una hora del día) y datetime (día y hora con su
# desfase), de la librería estándar. Enum: listas cerradas de valores.
from datetime import date, datetime, time
from enum import Enum

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


class TipoLlamada(str, Enum):
    """
    Los tres tipos de llamada (D25.9). Los mismos valores que el CHECK
    chk_decisiones_gate_tipo_llamada (paso12): si se añade uno aquí, allí
    también. Lo usan también la ficha y su llamada_registrable.
    """

    # Caso con Gate ('pendiente_aprobacion').
    GATE = "gate"
    # Seguimiento de 48 h (apartados b y c de la lista diaria).
    SEGUIMIENTO = "seguimiento"
    # Confirmación de una visita pedida desde el chat (apartados d y e).
    CONFIRMACION_VISITA = "confirmacion_visita"


class ResultadoLlamada(str, Enum):
    """Los cinco resultados (P5; CHECK chk_decisiones_gate_decision, paso12)."""

    # Gate y seguimiento.
    VISITA_ACORDADA = "visita_acordada"
    DESCARTAR = "descartar"
    # Confirmación de una visita pedida desde el chat (D25.10, D25.11).
    CONFIRMAR_HORA = "confirmar_hora"
    OTRA_HORA = "otra_hora"
    CLIENTE_CANCELA = "cliente_cancela"


class MotivoResultado(str, Enum):
    """El catálogo de motivos (CHECK chk_decisiones_gate_motivo, paso12)."""

    # Los cinco de siempre (paso10).
    PRECIO = "precio"
    PLAZO = "plazo"
    # Sin respuesta tras varios intentos (D24.3: el sistema no los cuenta).
    NO_CONTESTA = "no_contesta"
    PROYECTO_NO_VIABLE = "proyecto_no_viable"
    OTRO = "otro"
    # El cliente sigue sin decidirse en la última llamada (D29.3).
    SIN_DECISION = "sin_decision"


# Qué resultados tiene cada tipo de llamada (D25.10: Gate y seguimiento
# comparten páginas). Lo usan el servicio (409 resultado_no_admitido) y la
# ficha (llamada_registrable); es la regla del CHECK
# chk_decisiones_gate_tipo_resultado, en Python.
RESULTADOS_POR_TIPO = {
    TipoLlamada.GATE: (ResultadoLlamada.VISITA_ACORDADA, ResultadoLlamada.DESCARTAR),
    TipoLlamada.SEGUIMIENTO: (ResultadoLlamada.VISITA_ACORDADA, ResultadoLlamada.DESCARTAR),
    TipoLlamada.CONFIRMACION_VISITA: (ResultadoLlamada.CONFIRMAR_HORA, ResultadoLlamada.OTRA_HORA,
                                      ResultadoLlamada.CLIENTE_CANCELA),
}

# Qué acompaña a cada resultado (plan 2.3; CHECK chk_decisiones_gate_coherencia):
#   CON_FECHA: llevan fecha y hora (la visita que queda confirmada).
CON_FECHA = (ResultadoLlamada.VISITA_ACORDADA, ResultadoLlamada.CONFIRMAR_HORA, ResultadoLlamada.OTRA_HORA)
#   CON_MOTIVO: llevan motivo (la oportunidad se pierde).
CON_MOTIVO = (ResultadoLlamada.DESCARTAR, ResultadoLlamada.CLIENTE_CANCELA)
#   Motivos que NO valen al cancelar: quien cancela, contestó y decidió (C6, D29.3).
MOTIVOS_NO_EN_CANCELACION = (MotivoResultado.NO_CONTESTA, MotivoResultado.SIN_DECISION)


class ResultadoLlamadaCreate(BaseModel):
    """
    Cuerpo de POST /resultados-llamada. Lo envía el formulario de n8n que
    rellena administración, no un LLM: por eso SÍ lleva oportunidad_id.
    """

    # extra="forbid": un campo no declarado (por ejemplo "tipo") da 422.
    model_config = ConfigDict(extra="forbid")

    # Entero mayor que 0: los id SERIAL de Postgres empiezan en 1.
    oportunidad_id: int = Field(gt=0)

    # Obligatorio. Un valor fuera del Enum da 422.
    resultado: ResultadoLlamada

    # Pueden faltar (None); si son obligatorios o están prohibidos lo
    # decide el validador de modelo del final, según el resultado.
    fecha: date | None = None
    hora: time | None = None

    # Solo con descartar y cliente_cancela. Un valor fuera del Enum da 422.
    motivo: MotivoResultado | None = None

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
    # el CHECK chk_decisiones_gate_coherencia, adelantada a la puerta para
    # dar un 422 claro antes de tocar la base de datos.
    @model_validator(mode="after")
    def comprobar_combinacion(self) -> Self:
        # El texto del resultado, para los mensajes ("con resultado=...").
        r = self.resultado.value
        # Los que dejan una visita confirmada: fecha Y hora, y sin motivo.
        if self.resultado in CON_FECHA:
            # Sin fecha o sin hora: 422.
            if self.fecha is None or self.hora is None:
                raise ValueError(f"con resultado={r}, fecha y hora son obligatorias")
            # Con motivo: 422 (no se pierde nada).
            if self.motivo is not None:
                raise ValueError(f"con resultado={r} no se admite motivo")
        # Los que pierden la oportunidad (descartar, cliente_cancela).
        else:
            # Sin motivo: 422.
            if self.motivo is None:
                raise ValueError(f"con resultado={r}, motivo es obligatorio")
            # Con fecha o con hora: 422 (no queda ninguna visita).
            if self.fecha is not None or self.hora is not None:
                raise ValueError(f"con resultado={r} no se admiten fecha ni hora")
            # Al cancelar, ni 'no_contesta' ni 'sin_decision' (C6, D29.3).
            if self.resultado == ResultadoLlamada.CLIENTE_CANCELA and self.motivo in MOTIVOS_NO_EN_CANCELACION:
                raise ValueError(f"con resultado={r} no se admite motivo={self.motivo.value}: quien cancela, ha decidido")
        # Un validador "after" devuelve el propio objeto.
        return self


class ResultadoLlamadaResponse(BaseModel):
    """
    Respuesta de POST /resultados-llamada (plan, 2.5). NINGÚN campo de
    importe, a propósito (segunda barrera, como en /gate-decisions y /visits).
    """

    # Id de la fila de decisiones_gate (la tabla conserva su nombre, P3 = b).
    llamada_id: int
    # La oportunidad de la petición.
    oportunidad_id: int
    # El tipo de llamada que dedujo el backend (o el de la fila, en una
    # repetición).
    tipo_llamada: TipoLlamada
    # El resultado REGISTRADO (en una repetición, el que ya estaba).
    resultado: ResultadoLlamada
    # El motivo registrado, o None.
    motivo: MotivoResultado | None
    # Estado de la oportunidad tras el registro.
    estado_oportunidad: str
    # La visita que queda CONFIRMADA, o None (descartar, cliente_cancela).
    visita_id: int | None
    # Su inicio en hora de Madrid con su desfase, o None.
    fecha_visita: datetime | None
    # La visita 'solicitada' cancelada (otra_hora, cliente_cancela), o None.
    visita_cancelada_id: int | None
    # True si se escribió algo (201); False en una repetición (200).
    creado: bool
