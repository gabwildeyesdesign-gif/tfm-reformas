"""
Esquema de la respuesta de la tool MCP get_business_rules (Bloque B3 del
plan del Agente 2, docs/Plan_Agente2_Calendar.txt, sección 3.4; D18.4).

Lo que el Agente 2 puede saber de las reglas de negocio GENERALES, sin
ningún dato de un cliente: cuándo se puede pedir una visita, cuánto cuesta
el informe técnico si hace falta (D18.5.2) y qué incluye cada tipo de
reforma (D18.8). NUNCA precio_m2, margen, IVA ni umbrales del Gate (C4 del
plan): con ellos el agente podría calcular la cifra de un caso con Gate.

Todos los modelos llevan extra="forbid": un campo que no esté declarado
aquí no puede colarse en la respuesta, y fastmcp publica este esquema como
esquema de salida de la tool.
"""

# Decimal: importe exacto, nunca float (D4).
from decimal import Decimal

# De Pydantic: BaseModel (la clase base de todo esquema) y ConfigDict (la
# configuración del modelo).
from pydantic import BaseModel, ConfigDict

# Los valores permitidos de tipo_reforma y nivel_acabados, los mismos que
# los CHECK de tarifas_base.
from app.schemas.common import NivelAcabados, TipoReforma


class FranjaVisita(BaseModel):
    """Una franja del día en la que una visita puede empezar y terminar."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # Hora de inicio de la franja, "HH:MM" en hora de Madrid.
    inicio: str
    # Hora de fin de la franja, "HH:MM": la visita tiene que haber
    # terminado a esa hora.
    fin: str


class ReglasVisita(BaseModel):
    """Cuándo se puede pedir una visita técnica (las reglas de POST /visits)."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # Días en los que hay visitas. Es la regla fija de validar_fecha
    # (app/services/reglas_visita.py), no una fila de reglas_negocio.
    dias: str
    # La franja de la mañana.
    manana: FranjaVisita
    # La franja de la tarde.
    tarde: FranjaVisita
    # Lo que dura una visita, en minutos.
    duracion_min: int


class IncluyeTipico(BaseModel):
    """Lo que incluye de forma orientativa un tipo de reforma con un nivel."""

    # Ningún campo de más (en particular, ningún precio).
    model_config = ConfigDict(extra="forbid")

    # El tipo de reforma de la fila de tarifas_base.
    tipo_reforma: TipoReforma
    # El nivel de acabados de esa fila.
    nivel_acabados: NivelAcabados
    # tarifas_base.incluye_tipico tal cual; la columna admite NULL.
    incluye_tipico: str | None


class BusinessRulesResponse(BaseModel):
    """Respuesta de get_business_rules. Sin ningún dato de un cliente."""

    # Ningún campo de más.
    model_config = ConfigDict(extra="forbid")

    # Las reglas de la visita técnica.
    visita: ReglasVisita
    # Lo que cuesta el informe técnico, CON IVA (D14: todo precio que ve el
    # cliente lleva el IVA incluido). Lo calcula el backend; el tipo de IVA
    # no sale.
    recargo_informe_tecnico_con_iva: Decimal
    # Una entrada por fila de tarifas_base (hoy 12), sin precio_m2.
    incluye_tipico: list[IncluyeTipico]
