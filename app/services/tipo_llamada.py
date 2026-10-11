"""
Qué tipo de llamada corresponde a una oportunidad, deducido por el backend
(pre-decisión 2 del plan docs/Plan_Resultado_Llamadas.txt, sección 2.2; P10).

Lo usan dos sitios, que no pueden contradecirse:
  - POST /resultados-llamada (resultados_llamada_service.py): para saber qué
    resultados admite y, si no admite ninguno, el 409 que corresponde;
  - la ficha (ficha_oportunidad_service.py): su llamada_registrable (P9),
    para que el formulario de n8n sepa qué páginas enseñar sin copiar la
    deducción.

Orden de la deducción:
  1. tiene una visita 'solicitada'           -> confirmacion_visita
  2. 'pendiente_aprobacion'                  -> gate
  3. 'seguimiento_pendiente'                 -> seguimiento (apartado c)
  4. 'presupuesto_enviado' y cumple los seis
     criterios de b) (condicion_seguimiento) -> seguimiento (apartado b)
  5. cualquier otro caso                     -> ninguno (estado_no_permitido)

SOLO LEE (ningún FOR UPDATE ni escritura): vale dentro de la transacción de
quien llama, también de la de solo lectura de la ficha. Quien escribe
(el endpoint) ya tiene bloqueada la fila de la oportunidad.

Como todo services/, este archivo NO importa fastapi ni fastmcp.
"""

# datetime: el tipo de "ahora" y de la fecha de la visita pedida.
from datetime import datetime
# NamedTuple: una tupla con nombre en cada campo (se lee res.tipo).
from typing import NamedTuple

# Los tipos y los resultados de cada uno (esquema del contrato nuevo).
from app.schemas.resultados_llamada import RESULTADOS_POR_TIPO, ResultadoLlamada, TipoLlamada
# Los criterios de b), su regla de 48 h y su validación: los MISMOS que el
# listado y POST /create-followup-task (sin copiarlos).
from app.services.condicion_seguimiento import (
    CLAVE_SEGUIMIENTO,
    ESTADO_POR_ABRIR,
    columnas_criterios,
    parametros_condicion,
    primer_criterio_incumplido,
    problema_horas,
)
# 'seguimiento_pendiente', donde ya está definido (no se copia).
from app.services.followup_service import ESTADO_ABIERTO
# 'solicitada', donde ya está definido (no se copia).
from app.services.visits_service import VISITA_SOLICITADA

# El estado de los casos con Gate.
ESTADO_GATE = "pendiente_aprobacion"


# Lo que devuelve la deducción (ver su docstring).
class TipoDeducido(NamedTuple):
    """
    El resultado de la deducción.
      tipo:          el tipo de llamada, o None si no corresponde ninguna.
      motivo_no:     si tipo es None, el código del 409 (estado_no_permitido
                     o el criterio de b) que falla) o configuracion_incompleta.
      faltan:        en configuracion_incompleta, la lista de problemas.
      visita_id:     la visita 'solicitada' (solo en confirmacion_visita).
      fecha_pedida:  su fecha_propuesta (solo en confirmacion_visita).
    """

    tipo: TipoLlamada | None
    motivo_no: str | None = None
    # Con configuracion_incompleta: los problemas de la regla.
    faltan: list[str] | None = None
    # Solo en confirmacion_visita: la visita pedida y su fecha.
    visita_id: int | None = None
    fecha_pedida: datetime | None = None


def resultados_de(tipo: TipoLlamada) -> tuple[ResultadoLlamada, ...]:
    """Los resultados que admite ese tipo de llamada (RESULTADOS_POR_TIPO)."""
    # Una búsqueda en el diccionario del esquema.
    return RESULTADOS_POR_TIPO[tipo]


def deducir_tipo_llamada(cursor, oportunidad_id: int, estado: str, ahora: datetime) -> TipoDeducido:
    """
    El tipo de llamada de la oportunidad, en el orden del docstring del
    módulo, con el cursor de quien llama y dentro de su transacción.

    estado: oportunidades.estado, ya leído por quien llama.
    ahora:  el now() de Postgres de esa transacción (plazo de b).
    """
    # 1. ¿Tiene una visita 'solicitada'? Como mucho una (índice parcial de
    #    una visita activa por oportunidad).
    # SQL: id y fecha de la visita solicitada de la oportunidad, si la hay.
    cursor.execute(
        "SELECT id, fecha_propuesta FROM visitas WHERE oportunidad_id = %s AND estado = %s;",
        (oportunidad_id, VISITA_SOLICITADA),
    )
    # La fila, o None.
    solicitada = cursor.fetchone()
    # Con visita pedida: la llamada es para confirmarla.
    if solicitada is not None:
        return TipoDeducido(TipoLlamada.CONFIRMACION_VISITA, visita_id=solicitada[0], fecha_pedida=solicitada[1])

    # 2. y 3. Gate y seguimiento abierto: basta con el estado.
    if estado == ESTADO_GATE:
        return TipoDeducido(TipoLlamada.GATE)
    if estado == ESTADO_ABIERTO:
        return TipoDeducido(TipoLlamada.SEGUIMIENTO)

    # 5 (antes que 4). Cualquier estado que no sea 'presupuesto_enviado'
    #    no tiene llamada.
    if estado != ESTADO_POR_ABRIR:
        return TipoDeducido(None, motivo_no="estado_no_permitido")

    # 4. 'presupuesto_enviado': solo si cumple los seis criterios de b).
    # SQL: la regla de 48 h, leída en ESTA llamada (nunca en el código).
    cursor.execute("SELECT valor FROM reglas_negocio WHERE clave = %s;", (CLAVE_SEGUIMIENTO,))
    # La fila, o None si la regla no existe; y su valor, o None.
    fila_regla = cursor.fetchone()
    valor = fila_regla[0] if fila_regla is not None else None
    # La MISMA validación que el listado y create-followup-task.
    problema = problema_horas(CLAVE_SEGUIMIENTO, valor)
    # Regla rota: no se puede saber; quien llama decide (el endpoint, 503).
    if problema is not None:
        return TipoDeducido(None, motivo_no="configuracion_incompleta", faltan=[problema])
    # SQL: los seis criterios COMPARTIDOS como columnas de sí o no (el mismo
    # texto que el WHERE del listado). LEFT JOIN: sin presupuesto, p.* NULL.
    cursor.execute(
        "SELECT " + columnas_criterios() + " "
        "FROM oportunidades o "
        "LEFT JOIN presupuestos p ON p.oportunidad_id = o.id "
        "WHERE o.id = %(oportunidad_id)s;",
        parametros_condicion(ahora, int(valor)) | {"oportunidad_id": oportunidad_id},
    )
    # El motivo del primer criterio que falla, o None si se cumplen todos.
    falla = primer_criterio_incumplido(cursor.fetchone())
    # Alguno falla: no sale en b), así que no hay llamada (409 con su motivo).
    if falla is not None:
        return TipoDeducido(None, motivo_no=falla)
    # Cumple b): es un seguimiento por abrir.
    return TipoDeducido(TipoLlamada.SEGUIMIENTO)
