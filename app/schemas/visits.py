"""
Esquemas de datos de POST /visits: la solicitud de visita técnica que el
Agente 2 registra desde el chat (plan: docs/Plan_Endpoint_Visits.txt).

Aquí solo se comprueba la FORMA de los datos. Las reglas de negocio (que
la fecha sea futura, de lunes a viernes, y que la visita quepa en una
franja) dependen de valores de reglas_negocio y del reloj de la base de
datos, así que viven en app/services/visits_service.py.
"""

# re: expresiones regulares, para exigir el formato exacto de fecha y hora.
import re
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import MAX_LEAD_TOKEN, MAX_TEXTO_VISITA, MIN_LEAD_TOKEN

# Formatos exactos que admite el contrato. ^ y $ obligan a que el texto
# ENTERO cumpla el patrón (no basta con que lo contenga).
#   \d{4}-\d{2}-\d{2}      -> "2026-10-23"
#   ([01]\d|2[0-3]):[0-5]\d -> de "00:00" a "23:59"; "9:00" no vale, "09:00" sí
PATRON_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PATRON_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


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
    #
    # Por qué hacen falta (comprobado con ejecución real el 2026-09-26):
    # sin ellos, Pydantic acepta en fecha "2026-10-01T00:00:00" y números
    # enteros, y en hora "10:00:30.5", el número 36000 y "10:00Z". Este
    # último es el peligroso: la Z significa UTC, así que la visita
    # quedaría dos horas desplazada respecto a lo que el cliente pidió.
    # El contrato dice YYYY-MM-DD y HH:MM, y solo eso se admite.

    @field_validator("fecha", mode="before")
    @classmethod
    def fecha_en_formato_exacto(cls, valor):
        # isinstance comprueba el tipo: si no es texto, o si es texto con
        # otra forma, se rechaza. El ValueError se convierte en un 422 que
        # nombra el campo.
        if not isinstance(valor, str) or not PATRON_FECHA.match(valor):
            raise ValueError("la fecha debe tener el formato YYYY-MM-DD, por ejemplo 2026-10-23")
        # Se devuelve el texto tal cual: Pydantic lo convierte después a
        # date, y ahí rechaza los días imposibles como "2026-02-30".
        return valor

    @field_validator("hora", mode="before")
    @classmethod
    def hora_en_formato_exacto(cls, valor):
        if not isinstance(valor, str) or not PATRON_HORA.match(valor):
            raise ValueError("la hora debe tener el formato HH:MM (24 horas), por ejemplo 08:30 o 17:00")
        return valor

    @field_validator("texto_cliente", mode="before")
    @classmethod
    def texto_sin_espacios_en_los_extremos(cls, valor):
        # .strip() quita espacios, tabuladores y saltos de línea del
        # principio y del final. Un texto hecho solo de espacios queda
        # vacío y lo rechaza min_length=1. Si no es texto, se deja pasar
        # tal cual para que Pydantic dé su propio error de tipo.
        return valor.strip() if isinstance(valor, str) else valor


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
