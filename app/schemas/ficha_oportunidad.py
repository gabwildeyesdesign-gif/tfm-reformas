"""
Esquema de la respuesta de GET /oportunidades/{oportunidad_id}/ficha: la
ficha completa de CUALQUIER oportunidad, con o sin Gate, para que
administración sepa qué pidió el cliente antes de llamarle (D26.2; plan:
docs/Plan_Endpoint_Ficha_Oportunidad.txt, sección 2.5).

Solo hay esquema de SALIDA: el único dato de entrada, oportunidad_id, va
en la ruta y lo valida el adaptador con Path(gt=0).

Las piezas comunes con GET /gate-avisos (contacto, reforma y presupuesto)
se REUTILIZAN de app/schemas/aviso_gate.py, sin copiarlas: los dos
endpoints las construyen con las mismas funciones
(app/services/datos_oportunidad.py).

Minimización: todos los modelos llevan extra="forbid" (un campo no
declarado es un error, no se ignora). Lo que NO sale está en la sección
2.6 del plan (lead_token, ids internos, logs.detalle, la ficha de
clientes...).
"""

# datetime: día y hora con su desfase. Todas las fechas salen en hora de
# Madrid ("2026-10-23T10:15:00+02:00").
from datetime import datetime

# Enum: para los conjuntos cerrados de valores (tipo de llamada y evento).
from enum import Enum

# BaseModel: la base de todo esquema. ConfigDict: su configuración
# (extra="forbid"). Field: descripción de un campo para /docs.
from pydantic import BaseModel, ConfigDict, Field

# Las piezas compartidas con GET /gate-avisos, reutilizadas tal cual.
from app.schemas.aviso_gate import ContactoAviso, PresupuestoAviso, ReformaAviso

# Las listas cerradas que ya existen: estados de la oportunidad y de la
# visita (los de los CHECK), tipo de reforma y número máximo de fotos.
from app.schemas.common import MAX_FOTOS_LEAD, EstadoOportunidad, EstadoVisita, TipoReforma

# Tipo, resultado y motivo de una llamada: los del contrato de POST
# /resultados-llamada (Bloque B2 de docs/Plan_Resultado_Llamadas.txt). Antes
# de B2, TipoLlamada se definía aquí con un solo valor ("gate") y el
# resultado y el motivo eran los Enum del Gate; desde B2 se importan, sin
# copiarlos, con los tres tipos, los cinco resultados y 'sin_decision'.
from app.schemas.resultados_llamada import MotivoResultado, ResultadoLlamada, TipoLlamada


class EventoEstado(str, Enum):
    """
    Qué cambió el estado de la oportunidad (plan, 5.1). Son los nombres de
    logs.accion, para poder rastrear cada evento hasta su log; "alta" no
    tiene log (sale de oportunidades.created_at, C6).
    """

    # La oportunidad se crea en 'nueva' (POST /leads).
    ALTA = "alta"
    # POST /calculate-estimate: 'presupuesto_enviado' o 'pendiente_aprobacion'.
    PRESUPUESTO_CALCULADO = "presupuesto_calculado"
    # POST /visits: 'visita_agendada'.
    VISITA_SOLICITADA = "visita_solicitada"
    # POST /gate-decisions: 'visita_agendada' o 'perdida'.
    GATE_DECISION_REGISTRADA = "gate_decision_registrada"
    # POST /create-followup-task: 'seguimiento_pendiente'.
    SEGUIMIENTO_ABIERTO = "seguimiento_abierto"
    # POST /resultados-llamada, seguimiento o confirmación de visita (B2,
    # L-b): 'visita_agendada' o 'perdida', según detalle->>'estado'.
    LLAMADA_REGISTRADA = "llamada_registrada"


class PresupuestoFicha(PresupuestoAviso):
    """
    Los 8 campos de PresupuestoAviso (heredados: se escriben una sola vez,
    allí) más "si hubo Gate". Hereda también extra="forbid".
    """

    # presupuestos.requiere_aprobacion: true si el presupuesto tuvo Gate.
    # En gate-avisos no hacía falta (allí siempre es true).
    requiere_aprobacion: bool


class VisitaFicha(BaseModel):
    """Una visita de la oportunidad, en cualquier estado (también canceladas)."""

    model_config = ConfigDict(extra="forbid")

    # visitas.estado: uno de los 4 del CHECK.
    estado_visita: EstadoVisita
    # visitas.fecha_propuesta: inicio de la visita, en hora de Madrid.
    fecha_visita: datetime
    # visitas.created_at: cuándo se pidió o se acordó, en hora de Madrid.
    fecha_solicitud_visita: datetime
    # visitas.texto_cliente, tal cual (resumen del Agente 2, D26.7; en una
    # visita acordada por el Gate, el texto fijo del sistema).
    texto_cliente: str


class LlamadaFicha(BaseModel):
    """
    El resultado de una llamada ya registrada, CON su informe (D26.2). Es
    la gran diferencia con GET /gate-avisos, que nunca devuelve el informe.
    """

    model_config = ConfigDict(extra="forbid")

    # decisiones_gate.tipo_llamada (paso12): gate, seguimiento o
    # confirmacion_visita.
    tipo_llamada: TipoLlamada
    # decisiones_gate.decision: uno de los cinco resultados.
    resultado: ResultadoLlamada
    # decisiones_gate.motivo: con descartar y cliente_cancela; si no, null.
    motivo: MotivoResultado | None
    # decisiones_gate.created_at, en hora de Madrid.
    fecha_registro: datetime
    # Inicio de la visita que quedó CONFIRMADA, en hora de Madrid; null en
    # descartar y cliente_cancela.
    fecha_visita: datetime | None
    # Inicio de la visita pedida que se CANCELÓ (otra_hora y cliente_cancela;
    # visita_cancelada_id de paso12), en hora de Madrid; null en los demás.
    # Se da la fecha y no el id: la ficha no devuelve ids de visita.
    fecha_visita_cancelada: datetime | None
    # decisiones_gate.informe, entero.
    informe: str


class LlamadaRegistrable(BaseModel):
    """
    La llamada que se puede registrar AHORA para esta oportunidad (P9 del
    plan de resultados de llamadas): su tipo, deducido por la MISMA función
    que POST /resultados-llamada (app/services/tipo_llamada.py), y los
    resultados que admite. Con ella, el formulario de n8n sabe qué páginas
    enseñar sin copiar la deducción.
    """

    model_config = ConfigDict(extra="forbid")

    # gate, seguimiento o confirmacion_visita.
    tipo_llamada: TipoLlamada
    # Los resultados de ese tipo, en su orden.
    resultados: list[ResultadoLlamada]


class EventoHistorial(BaseModel):
    """Un cambio de estado de la oportunidad (plan, sección 5)."""

    model_config = ConfigDict(extra="forbid")

    # Qué lo cambió (alta o la acción de su log).
    evento: EventoEstado
    # El estado en que dejó la oportunidad.
    estado: EstadoOportunidad
    # Cuándo, en hora de Madrid (logs.created_at; el alta,
    # oportunidades.created_at).
    fecha: datetime


class OtraOportunidad(BaseModel):
    """
    Otra oportunidad del MISMO email (P6, opción a): lo justo para
    reconocer a un cliente que repite y abrir su ficha. Sin contacto.
    """

    model_config = ConfigDict(extra="forbid")

    # Para abrir su propia ficha.
    oportunidad_id: int
    # oportunidades.tipo_reforma (la columna admite NULL).
    tipo_reforma: TipoReforma | None
    # oportunidades.estado.
    estado_oportunidad: EstadoOportunidad
    # leads.created_at de ESA solicitud, en hora de Madrid.
    fecha_solicitud: datetime


class FichaOportunidadResponse(BaseModel):
    """Respuesta 200 de GET /oportunidades/{oportunidad_id}/ficha."""

    model_config = ConfigDict(extra="forbid")

    # El id pedido.
    oportunidad_id: int
    # oportunidades.estado: uno de los 8 del CHECK.
    estado_oportunidad: EstadoOportunidad
    # leads.created_at, en hora de Madrid: cuándo pidió presupuesto.
    fecha_solicitud: datetime
    # Contacto de la solicitud (nunca la ficha de clientes) y la reforma:
    # los mismos objetos que GET /gate-avisos.
    contacto: ContactoAviso
    reforma: ReformaAviso
    # El resumen del Agente 1 (leads.resumen_solicitud, D28.1), o null. La
    # misma clave y en el mismo sitio que en GET /gate-avisos: en la raíz,
    # no en "reforma" (ver aviso_gate.py; P9 del plan del resumen).
    resumen_solicitud: str | None = Field(description="Resumen del asistente sobre lo que quiere el cliente (o null)")
    # Las rutas de las fotos tal como se guardaron (P5, opción a).
    fotos: list[str] = Field(description=f"Rutas de las fotos del lead (0 a {MAX_FOTOS_LEAD})")
    # null si la oportunidad todavía no tiene presupuesto.
    presupuesto: PresupuestoFicha | None
    # Todas las visitas, lo más antiguo primero; [] si no hay.
    visitas: list[VisitaFicha]
    # Las llamadas ya registradas, lo más antiguo primero; [] si no hay.
    llamadas: list[LlamadaFicha]
    # La llamada que se puede registrar ahora (P9), o null: si ya hay un
    # resultado (es definitivo), si no hay presupuesto o si el estado no
    # tiene ninguna llamada.
    llamada_registrable: LlamadaRegistrable | None
    # Los cambios de estado registrados; siempre empieza por el alta.
    historial: list[EventoHistorial]
    # true si el estado actual es el que dejó el último evento; false si
    # no (por ejemplo, 'ganada' puesta a mano en N0). Nunca se inventa un
    # evento para que cuadre (plan, 5.3).
    historial_cuadra: bool
    # Las OTRAS oportunidades con el mismo email (no verificado); [] si no
    # hay. Nunca la propia.
    otras_oportunidades_mismo_email: list[OtraOportunidad]
