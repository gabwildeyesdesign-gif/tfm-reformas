"""
Esquemas de GET /huecos-libres y de la tool MCP consultar_huecos_libres
(D32, plan docs/Plan_Horario_Visitas_D32.txt, secciones 3 y 11).

La disponibilidad de visitas AL OFRECER: las horas de inicio libres, por
franja, para que administración (formulario de n8n) y el Agente 2 solo
ofrezcan horas que se pueden reservar. Dos modos:
  - días: los próximos dias_oferta_visita días laborables, desde HOY, con al
    menos una hora libre (P4 y P6 de la sección 11).
  - un día: las horas libres de una fecha concreta, recalculadas en ese
    momento.

NUNCA datos de otras visitas (ni ids, ni oportunidad, ni cliente, ni a qué
hora empieza o acaba una ocupada): solo horas libres. Todos los modelos
llevan extra="forbid": un campo que no esté declarado aquí no puede colarse
en la respuesta.
"""

# date: un día; datetime: un instante con su desfase.
from datetime import date, datetime

# Literal: un texto que solo puede tomar los valores que se escriben.
from typing import Literal

# De Pydantic: BaseModel (la clase base), ConfigDict (la configuración) y
# field_validator (validar un campo concreto).
from pydantic import BaseModel, ConfigDict, field_validator

# La comprobación de formato YYYY-MM-DD compartida con POST /visits.
from app.schemas.common import fecha_en_formato_exacto

# Por qué un día no tiene horas (P7 de la sección 11): 200 con el motivo,
# nunca un 422 (el 422 es solo para la FORMA de la fecha). D45 (2026-10-23)
# añade antelacion_insuficiente: el día es anterior al primer día permitido
# del canal.
MotivoSinHoras = Literal["fecha_pasada", "antelacion_insuficiente", "fin_de_semana", "dia_completo"]


class ConsultaHuecos(BaseModel):
    """
    Los parámetros de la query de GET /huecos-libres. Sin fecha: modo días;
    con fecha: modo un día. Cualquier otro parámetro da 422 (extra="forbid").
    """

    # Ningún parámetro de más.
    model_config = ConfigDict(extra="forbid")

    # La fecha del modo un día (YYYY-MM-DD), o None para el modo días.
    fecha: date | None = None

    # Antes de convertirla a date: solo el formato exacto (como en POST
    # /visits). Pydantic rechaza después los días imposibles (2026-02-30).
    @field_validator("fecha", mode="before")
    # @classmethod: el validador recibe la clase, no un objeto.
    @classmethod
    def validar_fecha(cls, valor):
        # None (sin parámetro) pasa tal cual; un texto, con el formato exacto.
        return valor if valor is None else fecha_en_formato_exacto(valor)


class DiaConHuecos(BaseModel):
    """Un día con al menos una hora libre (modo días), para el Agente 2."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # El día, YYYY-MM-DD (hora de Madrid).
    fecha: date
    # El día de la semana en español y en minúscula ("lunes" ... "viernes").
    dia_semana: str
    # Las horas libres de la mañana ("HH:MM"), quizá vacía.
    manana: list[str]
    # Las horas libres de la tarde ("HH:MM"), quizá vacía.
    tarde: list[str]
    # Cuántas horas libres tiene el día (mañana + tarde; siempre >= 1).
    num_horas_libres: int


class DiaConHuecosFormulario(DiaConHuecos):
    """El mismo día, con el texto EXACTO de la opción del desplegable de n8n."""

    # "Jueves 15/10 — mañana: 09:45 · 11:00 — tarde: 18:15" (plan, 3.3). La
    # construye el backend: el desplegable de n8n devuelve el texto de la
    # opción, y n8n busca el día por él.
    etiqueta: str


class HuecosDiasResponse(BaseModel):
    """Modo días de GET /huecos-libres (con la etiqueta de cada día)."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # El modo, para que n8n y las pruebas no tengan que deducirlo.
    modo: Literal["dias"]
    # Cuándo se calculó (now() de Postgres, hora de Madrid).
    generado_en: datetime
    # Los días con hueco, en orden; lista vacía si no hay ninguno.
    dias: list[DiaConHuecosFormulario]


class HuecosDiasAgente(BaseModel):
    """Modo días de la tool consultar_huecos_libres (sin etiqueta, P9)."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # El modo.
    modo: Literal["dias"]
    # Cuándo se calculó.
    generado_en: datetime
    # Los días con hueco, en orden.
    dias: list[DiaConHuecos]


class HuecosDiaResponse(BaseModel):
    """Modo un día (el mismo en GET /huecos-libres y en la tool)."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # El modo.
    modo: Literal["dia"]
    # Cuándo se calculó.
    generado_en: datetime
    # El día pedido.
    fecha: date
    # Su día de la semana en español y en minúscula (también "sábado" o "domingo").
    dia_semana: str
    # Las horas libres de la mañana.
    manana: list[str]
    # Las horas libres de la tarde.
    tarde: list[str]
    # manana + tarde, en orden: las opciones de radio de la página "Hora".
    horas: list[str]
    # None si hay horas; si no, por qué (P7).
    sin_horas_motivo: MotivoSinHoras | None
