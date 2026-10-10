"""
Lógica de negocio de GET /oportunidades/{oportunidad_id}/ficha: la ficha
completa de CUALQUIER oportunidad, con o sin Gate (D26.2; plan:
docs/Plan_Endpoint_Ficha_Oportunidad.txt).

SOLO LECTURA, con tres barreras (plan, 2.9):
  1. get_db_connection(), que termina con rollback y nunca hace commit;
  2. la PRIMERA orden es SQL_MODO_TRANSACCION (REPEATABLE READ, READ
     ONLY): Postgres rechaza cualquier escritura, y las cinco consultas
     ven la MISMA foto de los datos, así que la ficha no se contradice
     aunque alguien escriba en medio;
  3. ningún FOR UPDATE.
No escribe nada, tampoco en logs (P7: la auditoría de lecturas queda
para D26.5).

El contacto, la reforma, las fotos y el presupuesto se construyen con las
MISMAS funciones que GET /gate-avisos (datos_oportunidad.py).

Como todo services/, este archivo NO importa fastapi ni fastmcp. Si la
oportunidad no existe, lanza OportunidadNoEncontrada; el adaptador
app/api/ficha_oportunidad.py la traduce a 404.
"""

# datetime: para anotar el tipo de las fechas.
from datetime import datetime

# La conexión de SOLO LECTURA del pool (rollback al salir del "with").
from app.db.connection import get_db_connection

# El esquema de la respuesta y sus piezas propias.
from app.schemas.ficha_oportunidad import (
    EventoEstado,
    EventoHistorial,
    FichaOportunidadResponse,
    LlamadaFicha,
    OtraOportunidad,
    PresupuestoFicha,
    TipoLlamada,
    VisitaFicha,
)

# Los estados de la oportunidad y los resultados de una llamada del Gate.
from app.schemas.common import EstadoOportunidad
from app.schemas.gate_decisions import DecisionGate

# Lo compartido con GET /gate-avisos (Bloque B1): las 17 columnas del caso
# y las funciones que construyen sus piezas.
from app.services.datos_oportunidad import (
    COLUMNAS_CASO,
    a_madrid,
    campos_presupuesto,
    caso_desde_fila,
    columnas_caso_sql,
    contacto_desde,
    fotos_desde,
    reforma_desde,
)

# Las acciones de log que cambian el estado, importadas de los servicios
# que las ESCRIBEN (no copiadas): si uno cambiara el nombre de su acción,
# la ficha lo seguiría sin tocarla.
from app.services.estimate_service import ACCION_CALCULADO
from app.services.followup_service import ACCION_SEGUIMIENTO_ABIERTO
from app.services.gate_decisions_service import ACCION_DECISION_REGISTRADA
from app.services.visits_service import ACCION_SOLICITADA

# El modo de la transacción, el MISMO texto que usa GET /llamadas-del-dia
# (P9): importado, no copiado.
from app.services.listado_llamadas_service import SQL_MODO_TRANSACCION

# RechazoNegocio: la base común de los rechazos de negocio (motivo y
# mensaje).
from app.services.reglas_visita import RechazoNegocio


# ======================================================================
# Excepción
# ======================================================================


class OportunidadNoEncontrada(RechazoNegocio):
    """No existe ninguna oportunidad con ese id (-> 404)."""


# ======================================================================
# La tabla acción -> estado (plan, 5.1)
# ======================================================================

# Las CUATRO acciones de log que cambian el estado de una oportunidad. Es
# la lista cerrada de la consulta 4: los logs que no son cambio de estado
# (visita_sustituida, presupuesto_requiere_revision,
# visita_configuracion_incompleta, error_no_controlado) no entran.
ACCIONES_HISTORIAL = (
    ACCION_CALCULADO,
    ACCION_SOLICITADA,
    ACCION_DECISION_REGISTRADA,
    ACCION_SEGUIMIENTO_ABIERTO,
)

# Acciones que dejan SIEMPRE el mismo estado.
ESTADO_FIJO = {
    ACCION_SOLICITADA: EstadoOportunidad.VISITA_AGENDADA,
    ACCION_SEGUIMIENTO_ABIERTO: EstadoOportunidad.SEGUIMIENTO_PENDIENTE,
}

# presupuesto_calculado: el estado se lee de detalle ->> 'estado', y solo
# vale uno de estos dos (los que escribe estimate_service).
ESTADOS_TRAS_CALCULO = {
    EstadoOportunidad.PRESUPUESTO_ENVIADO.value: EstadoOportunidad.PRESUPUESTO_ENVIADO,
    EstadoOportunidad.PENDIENTE_APROBACION.value: EstadoOportunidad.PENDIENTE_APROBACION,
}

# gate_decision_registrada: el estado depende de detalle ->> 'decision'.
ESTADO_POR_DECISION = {
    DecisionGate.VISITA_ACORDADA.value: EstadoOportunidad.VISITA_AGENDADA,
    DecisionGate.DESCARTAR.value: EstadoOportunidad.PERDIDA,
}


# ======================================================================
# Funciones puras (sin base de datos): se prueban directamente
# ======================================================================


def estado_tras(accion: str, estado_detalle: str | None, decision_detalle: str | None) -> EstadoOportunidad | None:
    """
    El estado en que dejó la oportunidad un log de cambio (tabla de 5.1).

    accion:           logs.accion.
    estado_detalle:   logs.detalle ->> 'estado' (o None).
    decision_detalle: logs.detalle ->> 'decision' (o None).

    Devuelve None si el log no se puede interpretar: una acción que no es
    de cambio, o un detalle sin la clave esperada o con un valor
    desconocido. Nunca adivina un estado.
    """
    # presupuesto_calculado: lo dice su detalle ('estado').
    if accion == ACCION_CALCULADO:
        return ESTADOS_TRAS_CALCULO.get(estado_detalle)
    # gate_decision_registrada: depende de la decisión de su detalle.
    if accion == ACCION_DECISION_REGISTRADA:
        return ESTADO_POR_DECISION.get(decision_detalle)
    # Las de estado fijo; cualquier otra acción, None.
    return ESTADO_FIJO.get(accion)


def construir_historial(
    fecha_alta: datetime, logs: list, estado_actual: str
) -> tuple[list[EventoHistorial], bool]:
    """
    El historial y si cuadra (plan, 5.3, opción a).

    fecha_alta:    oportunidades.created_at (el alta no tiene log, C6).
    logs:          filas (accion, created_at, estado_detalle,
                   decision_detalle), YA ordenadas por la consulta 4
                   (created_at, id); aquí se respeta ese orden.
    estado_actual: oportunidades.estado.

    Devuelve (eventos, cuadra). cuadra es True solo si todos los logs se
    pudieron interpretar y el último evento deja el estado actual. Un log
    que no se puede interpretar no sale, y cuadra es False. NUNCA se añade
    un evento inventado para que cuadre.
    """
    # El primer evento es siempre el alta, en 'nueva' (único camino de
    # alta: POST /leads).
    eventos = [EventoHistorial(evento=EventoEstado.ALTA, estado=EstadoOportunidad.NUEVA, fecha=a_madrid(fecha_alta))]
    # Pasa a False en cuanto un log no se pueda interpretar.
    todos_interpretados = True
    # Cada log de cambio, en el orden recibido.
    for accion, fecha, estado_detalle, decision_detalle in logs:
        estado = estado_tras(accion, estado_detalle, decision_detalle)
        # No interpretable: no sale, y el historial deja de cuadrar.
        if estado is None:
            todos_interpretados = False
            continue
        # EventoEstado(accion): el nombre del evento es el de la acción.
        eventos.append(EventoHistorial(evento=EventoEstado(accion), estado=estado, fecha=a_madrid(fecha)))
    # EstadoOportunidad es (str, Enum): se compara directamente con el
    # texto de la columna.
    cuadra = todos_interpretados and eventos[-1].estado == estado_actual
    return eventos, cuadra


# ======================================================================
# Las consultas (plan, sección 3)
# ======================================================================

# Consulta 1, la cabecera: las 17 columnas del caso y, detrás, el id y
# requiere_aprobacion del presupuesto, el alta y el cliente_id (solo para
# la consulta 5; nunca se devuelve). Se monta al llamarla, como en
# aviso_gate_service. Diferencia con gate-avisos: LEFT JOIN con
# presupuestos, porque aquí una oportunidad sin presupuesto también tiene
# ficha (presupuesto null). Sin JOIN con clientes: su ficha no se lee.
def sql_cabecera() -> str:
    """El texto de la consulta 1 (las columnas compartidas, montadas ahora)."""
    return (
        "SELECT "
        + columnas_caso_sql()
        + """,
               p.id,
               p.requiere_aprobacion,
               o.created_at,
               l.cliente_id
        FROM oportunidades o
        JOIN leads l              ON l.id = o.lead_id
        LEFT JOIN presupuestos p  ON p.oportunidad_id = o.id
        LEFT JOIN umbrales_gate u ON u.tipo_reforma = o.tipo_reforma
        WHERE o.id = %(oportunidad_id)s;
        """
    )


# Consulta 2, las visitas: TODAS (sin filtro de estado), lo más antiguo
# primero. No lee v.id.
SQL_VISITAS = """
    SELECT v.estado, v.fecha_propuesta, v.created_at, v.texto_cliente
    FROM visitas v
    WHERE v.oportunidad_id = %(oportunidad_id)s
    ORDER BY v.created_at, v.id;
"""

# Consulta 3, las llamadas registradas: hoy, decisiones_gate (como mucho
# una). LEFT JOIN con visitas: un descarte no tiene visita. No lee d.id ni
# d.visita_id.
SQL_LLAMADAS = """
    SELECT d.decision, d.motivo, d.informe, d.created_at, v.fecha_propuesta
    FROM decisiones_gate d
    LEFT JOIN visitas v ON v.id = d.visita_id
    WHERE d.oportunidad_id = %(oportunidad_id)s
    ORDER BY d.created_at, d.id;
"""

# Consulta 4, el historial: solo los logs de la oportunidad
# (entity_type 'oportunidad'; los del manejador global son 'sistema') y
# solo las acciones de cambio ("= ANY": igual a alguna de la lista). Del
# detalle se leen DOS claves sueltas con "->>" (C2): nunca el detalle
# entero, que puede llevar texto_cliente.
SQL_HISTORIAL = """
    SELECT g.accion, g.created_at,
           g.detalle ->> 'estado',
           g.detalle ->> 'decision'
    FROM logs g
    WHERE g.entity_type = 'oportunidad'
      AND g.entity_id = %(oportunidad_id)s
      AND g.accion = ANY(%(acciones)s)
    ORDER BY g.created_at, g.id;
"""

# Consulta 5, las OTRAS oportunidades del mismo cliente (la ficha de
# clientes es única por email): sin la propia, sin ningún dato personal.
SQL_OTRAS = """
    SELECT o2.id, o2.tipo_reforma, o2.estado, l2.created_at
    FROM oportunidades o2
    JOIN leads l2 ON l2.id = o2.lead_id
    WHERE l2.cliente_id = %(cliente_id)s
      AND o2.id <> %(oportunidad_id)s
    ORDER BY l2.created_at, o2.id;
"""


def leer_ficha(cursor, oportunidad_id: int) -> FichaOportunidadResponse:
    """
    Las cinco consultas, con el cursor de quien llama y DENTRO de su
    transacción (no abre, no cierra y no fija el modo: eso lo hace
    obtener_ficha). Recibe el cursor, como leer_listado, para que las
    pruebas puedan ejecutarla dentro de una transacción suya.

    Lanza OportunidadNoEncontrada si no existe la oportunidad.
    """
    # Los parámetros de las consultas: los valores van SIEMPRE aparte.
    parametros = {"oportunidad_id": oportunidad_id}

    # 1. La cabecera.
    # SQL: consulta 1 (ver sql_cabecera).
    cursor.execute(sql_cabecera(), parametros)
    # Como mucho una fila (presupuestos.oportunidad_id es UNIQUE).
    fila = cursor.fetchone()
    # Sin fila: la oportunidad no existe (404). También un id que no cabe
    # en la columna: Postgres lo compara sin error y da 0 filas.
    if fila is None:
        raise OportunidadNoEncontrada(
            "oportunidad_no_encontrada",
            f"No existe ninguna oportunidad con id {oportunidad_id}.",
        )
    # Las 17 primeras columnas, con sus nombres; las 4 últimas, aparte.
    n_caso = len(COLUMNAS_CASO)
    caso = caso_desde_fila(fila[:n_caso])
    presupuesto_id, requiere_aprobacion, fecha_alta, cliente_id = fila[n_caso:]

    # 2. Las visitas.
    # SQL: consulta 2 (SQL_VISITAS).
    cursor.execute(SQL_VISITAS, parametros)
    # Una VisitaFicha por fila, con las fechas en hora de Madrid.
    visitas = [
        VisitaFicha(
            estado_visita=estado,
            fecha_visita=a_madrid(fecha_visita),
            fecha_solicitud_visita=a_madrid(creada),
            texto_cliente=texto,
        )
        for estado, fecha_visita, creada, texto in cursor.fetchall()
    ]

    # 3. Las llamadas registradas.
    # SQL: consulta 3 (SQL_LLAMADAS).
    cursor.execute(SQL_LLAMADAS, parametros)
    # Hoy todas son del Gate (P4); el informe sale entero (D26.2).
    llamadas = [
        LlamadaFicha(
            tipo_llamada=TipoLlamada.GATE,
            resultado=decision,
            motivo=motivo,
            fecha_registro=a_madrid(creada),
            fecha_visita=a_madrid(fecha_visita),
            informe=informe,
        )
        for decision, motivo, informe, creada, fecha_visita in cursor.fetchall()
    ]

    # 4. El historial. list(...): psycopg2 convierte la lista de Python en
    # un array de Postgres para el "= ANY".
    # SQL: consulta 4 (SQL_HISTORIAL).
    cursor.execute(SQL_HISTORIAL, {**parametros, "acciones": list(ACCIONES_HISTORIAL)})
    # Eventos y "cuadra", con la función pura.
    historial, cuadra = construir_historial(fecha_alta, cursor.fetchall(), caso["estado"])

    # 5. Las otras oportunidades del mismo cliente.
    # SQL: consulta 5 (SQL_OTRAS).
    cursor.execute(SQL_OTRAS, {**parametros, "cliente_id": cliente_id})
    # Una OtraOportunidad por fila; sin contacto.
    otras = [
        OtraOportunidad(
            oportunidad_id=otra_id,
            tipo_reforma=tipo,
            estado_oportunidad=estado,
            fecha_solicitud=a_madrid(solicitada),
        )
        for otra_id, tipo, estado, solicitada in cursor.fetchall()
    ]

    # La respuesta completa. Pydantic comprueba cada campo con el esquema,
    # y extra="forbid" impide añadir ninguno que no esté declarado.
    return FichaOportunidadResponse(
        oportunidad_id=oportunidad_id,
        estado_oportunidad=caso["estado"],
        fecha_solicitud=a_madrid(caso["fecha_solicitud"]),
        # Las piezas compartidas con gate-avisos.
        contacto=contacto_desde(caso),
        reforma=reforma_desde(caso),
        fotos=fotos_desde(caso),
        # Sin presupuesto, null. Con él, los 8 campos compartidos ("**"
        # reparte el diccionario como argumentos con nombre) y si hubo Gate.
        presupuesto=(
            None
            if presupuesto_id is None
            else PresupuestoFicha(**campos_presupuesto(caso), requiere_aprobacion=requiere_aprobacion)
        ),
        visitas=visitas,
        llamadas=llamadas,
        historial=historial,
        historial_cuadra=cuadra,
        otras_oportunidades_mismo_email=otras,
    )


# ======================================================================
# La operación completa
# ======================================================================


def obtener_ficha(oportunidad_id: int) -> FichaOportunidadResponse:
    """
    Devuelve la ficha de la oportunidad, en una transacción de solo
    lectura con una sola foto de los datos.

    Excepción que puede lanzar (el adaptador la traduce a HTTP):
      OportunidadNoEncontrada  404  oportunidad_no_encontrada
    """
    # get_db_connection(): conexión de solo lectura; al salir del "with"
    # hace rollback y la devuelve al pool, también si hay una excepción.
    with get_db_connection() as conn:
        # El cursor se cierra SIEMPRE al salir de este bloque.
        with conn.cursor() as cursor:
            # PRIMERA orden de la transacción: REPEATABLE READ (una sola
            # foto para las cinco consultas) y READ ONLY (Postgres rechaza
            # cualquier escritura). Tiene que ir antes de cualquier otra.
            # SQL: SQL_MODO_TRANSACCION.
            cursor.execute(SQL_MODO_TRANSACCION)
            # Las cinco consultas, en esa misma transacción.
            return leer_ficha(cursor, oportunidad_id)
