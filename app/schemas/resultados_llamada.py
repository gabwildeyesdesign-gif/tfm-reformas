"""
Esquemas de datos de POST /resultados-llamada: el resultado de la llamada de
administración a un cliente (plan: docs/Plan_Resultado_Llamadas.txt,
secciones 2.3 y 2.5, con sus notas fechadas).

Nombres nuevos respecto a POST /gate-decisions (el alias con el contrato
antiguo; cambio 2026-10-22: quitado en el bloque 5, versión anterior en
aba39c4):
  decision        -> resultado
  decision_id     -> llamada_id
  fecha_propuesta -> fecha_visita

Bloque B2 (2026-10-11): tres tipos de llamada y cinco resultados.
  gate, seguimiento:    visita_acordada, descartar
  confirmacion_visita:  confirmar_hora, otra_hora, cliente_cancela
Y un motivo más en el catálogo, 'sin_decision' (D29.3), que el alias no
aceptaba.

D42 (2026-10-22, rama feat/n0-direccion-obra): direccion_obra, opcional, solo
con los resultados que dejan visita (CON_FECHA). Es CONFIDENCIAL (D42.5): no
sale en ninguna respuesta de esta ruta, solo en la ficha.

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

# Los MISMOS validadores de formato que POST /visits
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

# D42.3 (2026-10-22): los límites de la dirección de la obra. Los MISMOS que
# los CHECK de la tabla direcciones_obra (migración paso16): si se cambia
# uno aquí, hay que cambiarlo allí con otra migración (defensa doble).
MAX_CALLE_NUMERO = 150
MAX_MUNICIPIO = 100
# Código postal español: 5 cifras, y las dos primeras (la provincia) de 01 a
# 52. [0-9] y no \d: \d aceptaría también cifras de otros alfabetos.
PATRON_CODIGO_POSTAL = r"^(0[1-9]|[1-4][0-9]|5[0-2])[0-9]{3}$"


class DireccionObra(BaseModel):
    """
    La dirección de la obra (D42). CONFIDENCIAL: solo entra por POST
    /resultados-llamada y solo sale por la ficha (D42.5). La misma clase
    sirve para la entrada y para la ficha (app/schemas/ficha_oportunidad.py).
    """

    # extra="forbid": una clave no declarada (por ejemplo "piso") da 422.
    model_config = ConfigDict(extra="forbid")

    # Calle y número (y piso, si se escribe aquí), de 1 a 150 caracteres
    # contados DESPUÉS de recortar los extremos.
    calle_numero: str = Field(min_length=1, max_length=MAX_CALLE_NUMERO)
    # strict=True: SOLO texto. Un número (28013 sin comillas) da 422 en vez
    # de convertirse; un código como 01001 perdería el cero de delante.
    codigo_postal: str = Field(strict=True, pattern=PATRON_CODIGO_POSTAL)
    # El municipio, de 1 a 100 caracteres recortados.
    municipio: str = Field(min_length=1, max_length=MAX_MUNICIPIO)

    # Recorta los extremos de los tres textos antes de medirlos (se guardan
    # así: los CHECK exigen texto = btrim(texto)). Si no es texto, el
    # recorte compartido lo deja pasar tal cual y Pydantic da su 422 de tipo.
    @field_validator("calle_numero", "codigo_postal", "municipio", mode="before")
    @classmethod
    def recortar(cls, valor):
        # El recorte compartido (strip).
        return texto_sin_espacios_en_los_extremos(valor)


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

    # D42 (2026-10-22): la dirección de la obra. OPCIONAL en la API (P1 = A
    # del veredicto del tutor: es obligatoria solo en el formulario de n8n,
    # D42.2). Solo con los resultados que dejan una visita (CON_FECHA); en
    # los demás, un objeto da 422 (validador de abajo). null = no enviarla.
    direccion_obra: DireccionObra | None = None

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
            # D42: con dirección de la obra, 422 (no hay visita a la que ir);
            # ignorarla haría creer que se guardó un dato que se tira.
            if self.direccion_obra is not None:
                raise ValueError(f"con resultado={r} no se admite direccion_obra: no queda ninguna visita")
            # Al cancelar, ni 'no_contesta' ni 'sin_decision' (C6, D29.3).
            if self.resultado == ResultadoLlamada.CLIENTE_CANCELA and self.motivo in MOTIVOS_NO_EN_CANCELACION:
                raise ValueError(f"con resultado={r} no se admite motivo={self.motivo.value}: quien cancela, ha decidido")
        # Un validador "after" devuelve el propio objeto.
        return self


class ResultadoLlamadaResponse(BaseModel):
    """
    Respuesta de POST /resultados-llamada (plan, 2.5). NINGÚN campo de
    importe, a propósito (segunda barrera, como en /visits).
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
    # D33 (2026-10-14): su FIN, fecha_visita + duracion_visita_min, con el
    # mismo formato y desfase; None cuando fecha_visita es None (descartar,
    # cliente_cancela). Para el evento de Google Calendar de WF5 (n8n).
    fecha_fin_visita: datetime | None
    # La visita 'solicitada' cancelada (otra_hora, cliente_cancela), o None.
    visita_cancelada_id: int | None
    # True si se escribió algo (201); False en una repetición (200).
    creado: bool
