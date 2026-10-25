"""
Esquemas de datos de POST /cambios-visita: administración cambia o cancela una
visita YA confirmada, por teléfono (D46; plan docs/Plan_Cambio_Visita_D46.txt,
sección 3, con el veredicto del tutor al PASO 0).

  cambiar   la visita pasa a otra fecha y hora (sin límite de veces, D46.2);
            la oportunidad sigue en 'visita_agendada'.
  cancelar  la visita se cancela y la oportunidad pasa a 'perdida' (D46.3),
            con un motivo del catálogo de cliente_cancela.
Las dos llevan informe (obligatorio) y la fecha y hora de la visita que el
operador VE en la ficha (fecha_actual y hora_actual, P3): si ya no es la
visita confirmada (otro operador la cambió), el servicio da 409
visita_cambiada.

direccion_obra NO existe aquí (D46: es de la oportunidad, no de la visita):
extra="forbid" la rechaza con 422. Y ninguna respuesta la devuelve (D42.5).

Aquí solo se comprueba la FORMA de los datos. Que la visita exista, sea
futura y que la hora nueva esté libre lo decide
app/services/cambios_visita_service.py.
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

# Los MISMOS validadores de formato que POST /visits y POST
# /resultados-llamada (compartidos en common.py) y el límite del informe.
from app.schemas.common import (
    MAX_INFORME_GATE,
    fecha_en_formato_exacto,
    hora_en_formato_exacto,
    texto_sin_espacios_en_los_extremos,
)
# El catálogo de motivos y los que no valen al cancelar: los de POST
# /resultados-llamada (D46.3: el catálogo de cliente_cancela), importados.
from app.schemas.resultados_llamada import MOTIVOS_NO_EN_CANCELACION, MotivoResultado


class AccionCambioVisita(str, Enum):
    """
    Las dos acciones (D46). Los mismos valores que el CHECK
    chk_cambios_visita_accion (paso18): si se añade una aquí, allí también.
    """

    # La visita pasa a otra fecha y hora.
    CAMBIAR = "cambiar"
    # La visita se cancela y la oportunidad se pierde.
    CANCELAR = "cancelar"


class CambioVisitaCreate(BaseModel):
    """
    Cuerpo de POST /cambios-visita. Lo envía la pantalla de llamadas de n8n
    que maneja administración, no un LLM: por eso SÍ lleva oportunidad_id.
    La visita se identifica por la oportunidad (como mucho una activa) y por
    la fecha que se ve en la ficha, no por su id: la ficha no devuelve ids
    de visita (H-D46-3).
    """

    # extra="forbid": un campo no declarado (por ejemplo "direccion_obra" o
    # "visita_id") da 422.
    model_config = ConfigDict(extra="forbid")

    # Entero mayor que 0: los id SERIAL de Postgres empiezan en 1.
    oportunidad_id: int = Field(gt=0)

    # Obligatoria. Un valor fuera del Enum da 422.
    accion: AccionCambioVisita

    # La visita que el operador VE en la ficha (visita_modificable.fecha_visita),
    # en hora de Madrid. Obligatorias en las dos acciones (P3).
    fecha_actual: date
    hora_actual: time

    # La fecha y hora NUEVAS: obligatorias al cambiar, prohibidas al cancelar
    # (lo decide el validador de modelo del final).
    fecha: date | None = None
    hora: time | None = None

    # Solo al cancelar; un valor fuera del Enum da 422.
    motivo: MotivoResultado | None = None

    # Siempre obligatorio, de 1 a MAX_INFORME_GATE caracteres contados
    # DESPUÉS de recortar los extremos (lo hace el validador de abajo).
    informe: str = Field(min_length=1, max_length=MAX_INFORME_GATE)

    # mode="before": se ejecutan sobre lo que llegó en el JSON, antes de
    # convertirlo al tipo del campo. @classmethod: Pydantic 2 los llama con
    # la clase, antes de que exista el objeto.

    # Formato YYYY-MM-DD de las dos fechas (None pasa en "fecha": es opcional;
    # en fecha_actual, None da después el 422 de Pydantic por obligatoria).
    @field_validator("fecha_actual", "fecha", mode="before")
    @classmethod
    def validar_fecha(cls, valor):
        # Sin fecha, nada que comprobar; con fecha, el validador compartido.
        if valor is None:
            return None
        return fecha_en_formato_exacto(valor)

    # Formato HH:MM de las dos horas (mismo criterio).
    @field_validator("hora_actual", "hora", mode="before")
    @classmethod
    def validar_hora(cls, valor):
        # Sin hora, nada; con hora, el validador compartido.
        if valor is None:
            return None
        return hora_en_formato_exacto(valor)

    # Recorta los extremos del informe antes de medirlo (se guarda así: el
    # CHECK chk_cambios_visita_informe exige informe = btrim(informe)).
    @field_validator("informe", mode="before")
    @classmethod
    def recortar_informe(cls, valor):
        # H-D42-1: psycopg2 rechaza un texto con el carácter nulo U+0000
        # ANTES de enviarlo, con un ValueError (no un psycopg2.Error): sin
        # esto sería un 500. Aquí, 422 (ruta nueva: la deuda del informe de
        # /resultados-llamada no se toca en esta rama).
        if isinstance(valor, str) and "\x00" in valor:
            raise ValueError("no se admite el carácter nulo (U+0000)")
        # El recorte compartido (strip).
        return texto_sin_espacios_en_los_extremos(valor)

    # mode="after": mira el objeto ENTERO ya construido. La misma regla que
    # el CHECK chk_cambios_visita_coherencia, adelantada a la puerta para dar
    # un 422 claro antes de tocar la base de datos.
    @model_validator(mode="after")
    def comprobar_combinacion(self) -> Self:
        # El texto de la acción, para los mensajes ("con accion=...").
        a = self.accion.value
        # Cambiar: fecha Y hora nuevas, y sin motivo.
        if self.accion == AccionCambioVisita.CAMBIAR:
            # Sin fecha o sin hora: 422.
            if self.fecha is None or self.hora is None:
                raise ValueError(f"con accion={a}, fecha y hora son obligatorias")
            # Con motivo: 422 (la oportunidad no se pierde).
            if self.motivo is not None:
                raise ValueError(f"con accion={a} no se admite motivo")
        # Cancelar: motivo, y sin fecha ni hora nuevas.
        else:
            # Sin motivo: 422 (D46.3: obligatorio).
            if self.motivo is None:
                raise ValueError(f"con accion={a}, motivo es obligatorio")
            # Con fecha o con hora: 422 (no queda ninguna visita).
            if self.fecha is not None or self.hora is not None:
                raise ValueError(f"con accion={a} no se admiten fecha ni hora")
            # Ni 'no_contesta' ni 'sin_decision': quien cancela, contestó y
            # decidió (D46.3, el catálogo de cliente_cancela).
            if self.motivo in MOTIVOS_NO_EN_CANCELACION:
                raise ValueError(f"con accion={a} no se admite motivo={self.motivo.value}: quien cancela, ha decidido")
        # Un validador "after" devuelve el propio objeto.
        return self


class CambioVisitaResponse(BaseModel):
    """
    Respuesta de POST /cambios-visita (plan, sección 3). Los nombres de las
    visitas son los de ResultadoLlamadaResponse, para que n8n reutilice el
    reparto a WF5 (borrar la cancelada, guardar la nueva). Ningún importe ni
    la dirección de la obra (D42.5): segunda barrera.

    Los ids de visita SÍ van aquí: WF5 los usa como id del evento de Google
    Calendar (precisión 4 del tutor). Nunca en la ficha (H-D46-3).
    """

    # Id de la fila de cambios_visita.
    cambio_id: int
    # La oportunidad de la petición.
    oportunidad_id: int
    # La acción REGISTRADA (en una repetición, la que ya estaba).
    accion: AccionCambioVisita
    # El motivo de la cancelación, o None al cambiar.
    motivo: MotivoResultado | None
    # Estado de la oportunidad tras el cambio: 'visita_agendada' o 'perdida'.
    estado_oportunidad: str
    # La visita NUEVA, ya confirmada; None al cancelar.
    visita_id: int | None
    # Su inicio en hora de Madrid con su desfase; None al cancelar.
    fecha_visita: datetime | None
    # Su fin (fecha_visita + duracion_visita_min, D33), mismo formato; None
    # al cancelar. Para el evento de WF5.
    fecha_fin_visita: datetime | None
    # La visita confirmada que ha quedado cancelada (siempre).
    visita_cancelada_id: int
    # True si se escribió algo (201); False en una repetición (200).
    creado: bool
