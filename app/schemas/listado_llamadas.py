"""
Esquema de la respuesta de GET /llamadas-del-dia: la lista diaria de
llamadas que WF3 envía a administración (plan:
docs/Plan_Endpoint_Listado_WF3.txt, sección 1.5).

Solo hay esquema de SALIDA: el endpoint no tiene ninguna entrada (ni
cuerpo, ni parámetros; plan, 1.4).

Minimización (RGPD, pre-decisión 6 del plan): aquí solo existen los campos
que administración necesita para LLAMAR. No hay email, ni importes, ni
ningún id salvo oportunidad_id. Todos los modelos llevan extra="forbid":
si el servicio intentara añadir un campo no declarado, Pydantic lanzaría
un error en vez de dejarlo pasar. Es la segunda barrera; la primera es
que las consultas ni siquiera leen esos datos.

(Nota 2026-10-10, D26.1, bloque 4a-2; plan docs/Plan_Resumen_Lista_Diaria.txt.)
Cada elemento lleva ahora un resumen de lo que pidió el cliente: el
objeto "solicitud" (m2, nivel_acabados, incluye_cambios_estructurales) y,
en los elementos de visita, texto_cliente. Siguen fuera el email y los
importes: la lista ACUMULA a muchos clientes en un solo mensaje (D26,
sección 1.3).
"""

# date: un día sin hora (dia_visitas). datetime: día y hora con su desfase
# (las demás fechas, en hora de Madrid: "2026-10-08T09:00:00+02:00").
from datetime import date, datetime

# Decimal: m2 exacto (en el JSON viaja como TEXTO, "12.35"), nunca float.
from decimal import Decimal

# Enum: para las listas cerradas de valores (motivo y estado de la visita).
from enum import Enum

# BaseModel: la clase base de todo esquema de Pydantic. ConfigDict: su
# configuración (aquí, extra="forbid"). Field: para añadir una descripción
# a un campo, que aparece en /docs.
from pydantic import BaseModel, ConfigDict, Field

# Origen del contacto ("solicitud" o "no_disponible"): la MISMA lista que
# usa GET /gate-avisos, reutilizada y no copiada, porque la regla es la
# misma (P1 del plan del aviso; plan, 1.5).
from app.schemas.aviso_gate import OrigenContacto

# Tipo de reforma: la lista cerrada de los cuatro valores del CHECK.
# Nivel de acabados: 'basico', 'medio' o 'alto' (el mismo Enum que /leads).
from app.schemas.common import NivelAcabados, TipoReforma


class MotivoLlamada(str, Enum):
    """
    Por qué está cada elemento en la lista (D25.8 y P2 del plan): una
    etiqueta fija por apartado, para que n8n pueda juntar los apartados en
    un solo email sin perder el motivo.

    (str, Enum): cada valor es también un texto normal, igual que
    TipoReforma en common.py.
    """

    # Apartado a). En textos y documentos: "Gate sin decisión registrada"
    # (P2). Nunca "cliente pensándoselo": sin registro de intentos de
    # llamada (D24.4), el sistema no sabe si el cliente se lo piensa o si
    # nadie le llamó.
    GATE_SIN_DECISION = "gate_sin_decision"
    # Apartado b): pasaron las horas de la regla sin visita ni contacto.
    SEGUIMIENTO_POR_ABRIR = "seguimiento_por_abrir"
    # Apartado c): oportunidad ya en 'seguimiento_pendiente'.
    SEGUIMIENTO_ABIERTO = "seguimiento_abierto"
    # Apartado d): visita pedida que nadie ha confirmado.
    VISITA_SIN_CONFIRMAR = "visita_sin_confirmar"
    # Apartado e): visita (pedida o confirmada) del próximo día laborable.
    VISITA_PROXIMO_LABORABLE = "visita_proximo_laborable"


class EstadoVisitaListado(str, Enum):
    """Los dos únicos estados de visita que pueden salir en la lista (las activas)."""

    # Pedida por el cliente desde el chat (POST /visits): apartados d) y e).
    SOLICITADA = "solicitada"
    # Acordada por teléfono tras el Gate (POST /gate-decisions): solo e).
    CONFIRMADA = "confirmada"


class ContactoLlamada(BaseModel):
    """
    Con quién hay que hablar: nombre y teléfono de ESTA solicitud (los del
    lead), nunca los de la ficha de clientes. SIN email, a propósito
    (pre-decisión 6): para llamar basta el teléfono.
    """

    # extra="forbid": un campo no declarado (por ejemplo, email) es un
    # error, no se ignora.
    model_config = ConfigDict(extra="forbid")

    # "str | None": texto o null. Los dos son null con origen no_disponible.
    nombre: str | None
    telefono: str | None
    # Siempre presente: dice si los dos de arriba son de la solicitud o no
    # existen (lead anterior a la clave 'contacto').
    origen: OrigenContacto


class SolicitudLlamada(BaseModel):
    """
    Lo que pidió el cliente, en resumen (D26.1): de leads.datos_estructurados.
    El MISMO objeto en los cinco apartados. Cada campo es null si su clave
    falta en la solicitud: nunca se inventa un valor (D4).

    No es ReformaAviso (la "reforma" de la ficha y de gate-avisos): esa trae
    también tipo_reforma, que el elemento ya tiene (P2 y P3 del plan).
    """

    # extra="forbid": un campo no declarado (un email, un importe) es un
    # error, no se ignora.
    model_config = ConfigDict(extra="forbid")

    # Metros cuadrados, Decimal exacto ("12.35" en el JSON).
    m2: Decimal | None
    # 'basico', 'medio' o 'alto'.
    nivel_acabados: NivelAcabados | None
    # Si la reforma incluye cambios estructurales (true/false).
    incluye_cambios_estructurales: bool | None


class LlamadaOportunidad(BaseModel):
    """Un elemento de los apartados a), b) y c): una oportunidad que llamar."""

    model_config = ConfigDict(extra="forbid")

    # El único identificador que sale: el formulario del Gate y
    # create-followup-task trabajan con él.
    oportunidad_id: int
    # gate_sin_decision, seguimiento_por_abrir o seguimiento_abierto.
    motivo: MotivoLlamada
    # oportunidades.tipo_reforma (la columna admite NULL).
    tipo_reforma: TipoReforma | None
    # Qué pidió (D26.1). Siempre presente; sus campos pueden ser null.
    solicitud: SolicitudLlamada
    # Nombre y teléfono de la solicitud.
    contacto: ContactoLlamada
    # presupuestos.created_at, en hora de Madrid: la fecha desde la que se
    # cuenta el plazo (plan, 3.1).
    fecha_presupuesto: datetime


class LlamadaVisita(BaseModel):
    """Un elemento de los apartados d) y e): una visita por la que llamar."""

    model_config = ConfigDict(extra="forbid")

    # La oportunidad de la visita (el id de la visita NO sale).
    oportunidad_id: int
    # visita_sin_confirmar o visita_proximo_laborable.
    motivo: MotivoLlamada
    tipo_reforma: TipoReforma | None
    # Qué pidió (D26.1), el mismo objeto que en a), b) y c).
    solicitud: SolicitudLlamada
    contacto: ContactoLlamada
    # 'solicitada' o 'confirmada'.
    estado_visita: EstadoVisitaListado
    # visitas.fecha_propuesta, en hora de Madrid: cuándo empieza la visita.
    fecha_visita: datetime
    # visitas.created_at, en hora de Madrid: cuándo se pidió (o se acordó).
    fecha_solicitud_visita: datetime
    # visitas.texto_cliente de ESTA visita, tal cual está en la columna (NOT
    # NULL, 1..1000 caracteres): en una del chat, el que envió el Agente 2;
    # en una acordada tras el Gate, el texto fijo del sistema. Sin recorte
    # ni filtro aquí (D26.7, otro bloque). Solo en los elementos de visita.
    texto_cliente: str


class ReglasListado(BaseModel):
    """Los dos plazos usados en ESTA lista, leídos de reglas_negocio (P7)."""

    model_config = ConfigDict(extra="forbid")

    # Horas para recordar un Gate sin decisión registrada (paso10).
    horas_recordatorio_gate: int
    # Horas para abrir un seguimiento (paso11).
    horas_seguimiento_presupuesto: int


class ListadoLlamadasResponse(BaseModel):
    """
    Respuesta 200 de GET /llamadas-del-dia. Las cinco listas existen
    SIEMPRE (vacías: []), nunca null.
    """

    model_config = ConfigDict(extra="forbid")

    # now() de Postgres, en hora de Madrid: el "ahora" de todos los plazos.
    generado_en: datetime
    # El próximo día laborable (YYYY-MM-DD): el día del apartado e).
    dia_visitas: date
    # Los dos plazos aplicados.
    reglas: ReglasListado
    # Los cinco apartados (plan, 1.6). Field(description=...) solo añade la
    # explicación a /docs.
    gates_sin_decision: list[LlamadaOportunidad] = Field(
        description="a) Gate sin decisión registrada, presupuesto de hace más de horas_recordatorio_gate horas"
    )
    seguimientos_por_abrir: list[LlamadaOportunidad] = Field(
        description="b) presupuesto_enviado sin visita ni contacto, de hace más de horas_seguimiento_presupuesto horas"
    )
    # c) sin plazo; d) y e) con elementos de visita (LlamadaVisita).
    seguimientos_abiertos: list[LlamadaOportunidad] = Field(
        description="c) oportunidades en seguimiento_pendiente"
    )
    visitas_sin_confirmar: list[LlamadaVisita] = Field(
        description="d) visitas 'solicitada' que no son del próximo día laborable"
    )
    visitas_proximo_laborable: list[LlamadaVisita] = Field(
        description="e) visitas 'solicitada' o 'confirmada' del próximo día laborable (dia_visitas)"
    )
