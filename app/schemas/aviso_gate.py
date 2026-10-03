"""
Esquema de la respuesta de GET /gate-avisos/{oportunidad_id}: los datos de
un caso con Gate que necesita administración para llamar al cliente (plan:
docs/Plan_Endpoint_Aviso_Gate.txt, sección 1.5).

Solo hay esquema de SALIDA: el endpoint no tiene cuerpo de entrada (el
único dato de entrada, oportunidad_id, va en la ruta y lo valida el
adaptador con Path(gt=0)).

Minimización (decisión D2 del plan): aquí solo existen los campos que
administración necesita. Todos los modelos llevan extra="forbid": si el
servicio intentara añadir un campo que no está declarado (el informe de
la llamada, lead_token, presupuesto_id...), Pydantic lanzaría un error en
vez de dejarlo pasar. Es la segunda barrera; la primera es que la
consulta ni siquiera lee esos datos.
"""

# datetime: día y hora juntos, con su desfase. Las cuatro fechas del aviso
# salen en hora de Madrid ("2026-10-23T10:15:00+02:00").
from datetime import datetime

# Decimal: número exacto en base 10, sin los errores de float. Pydantic lo
# escribe en el JSON como TEXTO ("9075.00"), así que el importe viaja exacto
# (decisión D4 del plan; mismo criterio que app/schemas/estimates.py).
from decimal import Decimal

# Enum: para el conjunto cerrado de valores de "origen" del contacto.
from enum import Enum

# BaseModel: la clase base de todo esquema de Pydantic. ConfigDict: la
# configuración de un modelo (aquí, extra="forbid"). Field: para añadir una
# descripción a un campo, que aparece en /docs.
from pydantic import BaseModel, ConfigDict, Field

# Las listas cerradas que ya existen en el proyecto, reutilizadas en vez de
# copiadas: tipo de reforma, nivel de acabados y motivo del Gate
# (common.py), y decisión y motivo de descarte (gate_decisions.py). Así el
# aviso solo puede decir valores que el sistema conoce.
from app.schemas.common import MAX_FOTOS_LEAD, MotivoGate, NivelAcabados, TipoReforma
from app.schemas.gate_decisions import DecisionGate, MotivoDescarte


class OrigenContacto(str, Enum):
    """
    De dónde salen el nombre, el email y el teléfono del aviso (P1 del plan).

    (str, Enum): cada valor es también un texto normal, igual que
    TipoReforma en common.py.
    """

    # El lead tiene la clave 'contacto' en datos_estructurados: los datos
    # son los de ESTA solicitud (pre-decisión 5).
    SOLICITUD = "solicitud"
    # El lead es anterior a esa clave (datos de prueba de antes del
    # 2026-09-25): los tres campos salen null. NUNCA se rellenan con la
    # ficha de clientes, que puede ser de otra solicitud (P1).
    NO_DISPONIBLE = "no_disponible"


class ContactoAviso(BaseModel):
    """Contacto de la solicitud: con quién tiene que hablar administración."""

    # extra="forbid": un campo no declarado es un error, no se ignora.
    model_config = ConfigDict(extra="forbid")

    # "str | None": texto o null. Los tres son null con origen no_disponible.
    nombre: str | None
    email: str | None
    telefono: str | None
    # Siempre presente: dice si los tres de arriba son de la solicitud o no
    # existen.
    origen: OrigenContacto


class ReformaAviso(BaseModel):
    """Los datos de la reforma que dieron lugar al presupuesto."""

    model_config = ConfigDict(extra="forbid")

    # De la columna oportunidades.tipo_reforma (admite NULL en la tabla).
    tipo_reforma: TipoReforma | None
    # Metros cuadrados, como Decimal exacto (en el JSON, texto: "8.7").
    m2: Decimal | None
    # De leads.datos_estructurados (no tiene columna propia).
    nivel_acabados: NivelAcabados | None
    # De leads.datos_estructurados, convertido a booleano en SQL.
    incluye_cambios_estructurales: bool | None


class PresupuestoAviso(BaseModel):
    """
    Los importes del presupuesto (decisión D4 del plan): ninguno inventado;
    cada uno sale de una columna o de una operación documentada.
    """

    model_config = ConfigDict(extra="forbid")

    # presupuestos.created_at, en hora de Madrid.
    fecha_presupuesto: datetime
    # El motivo del Gate guardado ('cambios_estructurales',
    # 'importe_superior_umbral' o 'ambos').
    motivo_gate: MotivoGate | None
    # El tipo de IVA usado en ESTE cálculo (no el vigente hoy).
    iva_pct_aplicado: Decimal
    # Columnas de presupuestos, tal cual.
    importe_min_con_iva: Decimal | None
    importe_max_con_iva: Decimal | None
    # DEDUCIDOS del importe con IVA y del IVA aplicado (plan, 2.1); null si
    # su pareja con IVA es null.
    importe_min_sin_iva: Decimal | None
    importe_max_sin_iva: Decimal | None
    # umbrales_gate.umbral de la categoría, leído AHORA. "_vigente" a
    # propósito: puede no ser el que decidió el Gate si el umbral cambió
    # después del cálculo (plan, 2.2 y P3).
    umbral_gate_vigente: Decimal | None


class DecisionAviso(BaseModel):
    """
    La decisión ya registrada con POST /gate-decisions (decisión D3 del
    plan). NO tiene campo para el informe de la llamada, a propósito.
    """

    model_config = ConfigDict(extra="forbid")

    # 'visita_acordada' o 'descartar'.
    decision: DecisionGate
    # decisiones_gate.created_at, en hora de Madrid.
    fecha_decision: datetime
    # Solo con descartar; null con visita_acordada.
    motivo: MotivoDescarte | None
    # Inicio de la visita acordada, en hora de Madrid; null si se descartó.
    fecha_visita: datetime | None


class AvisoGateResponse(BaseModel):
    """
    Respuesta 200 de GET /gate-avisos/{oportunidad_id}.

    Solo existe para oportunidades CON Gate: el servicio rechaza las demás
    (404 / 409) antes de construir este objeto.
    """

    model_config = ConfigDict(extra="forbid")

    # El único identificador que sale: el formulario lo necesita para
    # llamar después a POST /gate-decisions.
    oportunidad_id: int
    # oportunidades.estado: 'pendiente_aprobacion', o 'visita_agendada' /
    # 'perdida' si ya hay decisión.
    estado_oportunidad: str
    # leads.created_at, en hora de Madrid: cuándo pidió presupuesto.
    fecha_solicitud: datetime
    # Los cuatro objetos anidados de arriba.
    contacto: ContactoAviso
    reforma: ReformaAviso
    # Las rutas de las fotos tal como se guardaron (0 a MAX_FOTOS_LEAD).
    # Field(description=...) solo añade la explicación a /docs.
    fotos: list[str] = Field(description=f"Rutas de las fotos del lead (0 a {MAX_FOTOS_LEAD})")
    presupuesto: PresupuestoAviso
    # "DecisionAviso | None": el objeto, o null si todavía no hay decisión.
    decision: DecisionAviso | None
