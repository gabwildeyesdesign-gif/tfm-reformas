"""
Esquemas de datos de POST /visits: la solicitud de visita técnica que el
Agente 2 registra desde el chat (plan: docs/Plan_Endpoint_Visits.txt).

Aquí solo se comprueba la FORMA de los datos. Las reglas de negocio (que
la fecha sea futura, de lunes a viernes, y que la visita quepa en una
franja) dependen de valores de reglas_negocio y del reloj de la base de
datos, así que viven en app/services/visits_service.py.
"""

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import (
    MAX_LEAD_TOKEN,
    MAX_TEXTO_VISITA,
    MIN_LEAD_TOKEN,
    fecha_en_formato_exacto,
    hora_en_formato_exacto,
    texto_sin_espacios_en_los_extremos,
)


class VisitaCreate(BaseModel):
    """
    Cuerpo de POST /visits.

    NUNCA lleva oportunidad_id (ni ningún otro identificador): un LLM no
    debe aportar identificadores (mismo motivo que la Adenda 1.1a). El
    backend encuentra la oportunidad a partir de lead_token, que añade n8n
    (es el sessionId del chat), no el agente.
    """

    # extra="forbid": un campo que no esté declarado aquí (por ejemplo
    # oportunidad_id) da 422 en vez de ignorarse en silencio.
    model_config = ConfigDict(extra="forbid")

    # Mismas reglas que en POST /leads: las constantes de common.py.
    lead_token: str = Field(min_length=MIN_LEAD_TOKEN, max_length=MAX_LEAD_TOKEN)

    # Día de la visita, en el calendario de Madrid.
    fecha: date

    # Hora de INICIO de la visita, en hora de Madrid.
    hora: time

    # Lo que dijo el cliente, tal cual, para que administración lo lea
    # antes de llamar. Longitud: de 1 a MAX_TEXTO_VISITA caracteres,
    # contados DESPUÉS de quitar los espacios de los extremos.
    texto_cliente: str = Field(min_length=1, max_length=MAX_TEXTO_VISITA)

    # ------------------------------------------------------------------
    # Validadores de formato
    # ------------------------------------------------------------------
    # mode="before": se ejecutan ANTES de que Pydantic convierta el valor
    # al tipo del campo (date, time), sobre lo que llegó en el JSON.
    # La lógica vive en app/schemas/common.py (compartida con
    # GateDecisionCreate desde el 2026-09-30); aquí solo se engancha cada
    # comprobación a su campo. El motivo de cada una está explicado allí.

    @field_validator("fecha", mode="before")
    @classmethod
    def validar_fecha(cls, valor):
        return fecha_en_formato_exacto(valor)

    @field_validator("hora", mode="before")
    @classmethod
    def validar_hora(cls, valor):
        return hora_en_formato_exacto(valor)

    @field_validator("texto_cliente", mode="before")
    @classmethod
    def recortar_texto_cliente(cls, valor):
        return texto_sin_espacios_en_los_extremos(valor)


class VisitaResponse(BaseModel):
    """
    Respuesta de POST /visits.

    NO TIENE NINGÚN CAMPO DE IMPORTE, a propósito (D18.5). Es la segunda
    de dos barreras, como en GET /leads/session: la consulta de services/
    no lee los importes, y FastAPI construye el JSON SOLO con los campos
    declarados aquí.
    """

    # Id de la visita: la recién creada o, en una repetición, la existente.
    visita_id: int

    # Estado de esa visita: 'solicitada' al crearla. En una repetición es
    # el estado que tenga (podría ser 'confirmada' si administración ya la
    # confirmó con esa misma fecha).
    estado: str

    # Inicio de la visita en hora de Madrid, con su desfase. En el JSON
    # sale como "2026-10-23T08:30:00+02:00", así el agente no tiene que
    # hacer cuentas de husos horarios.
    fecha_propuesta: datetime

    # Id de la visita 'solicitada' que esta ha sustituido (y que ha
    # quedado 'cancelada'), o None si no sustituye a ninguna.
    sustituye_a: int | None

    # True si esta llamada ha escrito algo; False si es una repetición con
    # la misma fecha y se ha devuelto la visita existente sin tocar nada.
    # El adaptador responde 201 o 200 según este campo.
    creado: bool
