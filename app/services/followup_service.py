"""
Lógica de negocio de POST /create-followup-task: abre el seguimiento de 48 h
de una oportunidad (plan: docs/Plan_Endpoint_Create_Followup_Task.txt,
secciones 2 y 4).

Abrir el seguimiento es una ETIQUETA en el expediente: la oportunidad pasa
de 'presupuesto_enviado' a 'seguimiento_pendiente' y queda un log
'seguimiento_abierto'. No llama ni avisa a nadie: la llamada la hace
administración con la lista diaria de WF3 (D25.1, D25.2).

La condición (los seis criterios) NO está escrita aquí: viene de
app/services/condicion_seguimiento.py, la misma que usa el apartado b) de
GET /llamadas-del-dia. Así la lista y este endpoint no pueden
contradecirse (plan, sección 3).

Todo ocurre en UNA transacción (get_transactional_connection), con el
aislamiento por defecto, READ COMMITTED (plan, 2.11): cada consulta ve lo
confirmado hasta ese momento, así que, tras esperar el bloqueo de la fila,
se ve la visita que POST /visits acaba de confirmar. Con REPEATABLE READ,
esa espera acabaría en un error de serialización (un 500).

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando no
se puede abrir el seguimiento, lanza una excepción con un "motivo" (código
estable) y un "mensaje"; el adaptador app/api/followup_tasks.py decide el
código HTTP de cada una.
"""

# Json: le dice a psycopg2 que un diccionario de Python va a una columna
# JSONB (logs.detalle).
from psycopg2.extras import Json

# La conexión de escritura del pool: commit al salir del "with" si todo va
# bien, rollback si se lanza una excepción dentro.
from app.db.connection import get_transactional_connection

# Los esquemas de entrada y de salida.
from app.schemas.followup_tasks import FollowupTaskCreate, FollowupTaskResponse

# La condición COMPARTIDA con el listado (plan, 3.2):
#   CLAVE_SEGUIMIENTO           la regla de 48 h. Al importarla, el nombre
#                               existe también en este módulo, y una prueba
#                               lo puede cambiar aquí en memoria (503).
#   ESTADO_POR_ABRIR            'presupuesto_enviado'.
#   columnas_criterios          los seis criterios como columnas.
#   parametros_condicion        los valores de sus %(...)s.
#   primer_criterio_incumplido  el motivo del primero que falla.
#   problema_horas              la validación de la regla (1..8760).
from app.services.condicion_seguimiento import (
    CLAVE_SEGUIMIENTO,
    ESTADO_POR_ABRIR,
    columnas_criterios,
    parametros_condicion,
    primer_criterio_incumplido,
    problema_horas,
)

# ZONA_MADRID: para escribir las fechas en hora de Madrid.
# ConfiguracionIncompleta: el rechazo de "falta una regla" (-> 503), el
# mismo de /visits, /gate-decisions y el listado.
# RechazoNegocio: la base común de los rechazos con motivo y mensaje.
from app.services.reglas_visita import ZONA_MADRID, ConfiguracionIncompleta, RechazoNegocio

# ======================================================================
# Constantes
# ======================================================================

# Estado al que pasa la oportunidad (y el de la repetición, el 200).
ESTADO_ABIERTO = "seguimiento_pendiente"

# Registro en logs, con el mismo criterio que los demás endpoints: la
# historia de una oportunidad se consulta en un solo sitio.
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_SEGUIMIENTO_ABIERTO = "seguimiento_abierto"

# Mensaje del 503 (para administración, a través de n8n).
MENSAJE_CONFIGURACION = (
    "No se puede abrir el seguimiento: falta o no es válida la regla de plazo "
    "de reglas_negocio (ver 'faltan'). No se ha escrito nada."
)

# Mensaje de cada 409, por motivo (los seis de CRITERIOS_SEGUIMIENTO). Son
# textos para administración; el código estable es el motivo.
MENSAJES_409 = {
    "sin_presupuesto": "La oportunidad todavía no tiene presupuesto: no hay nada que seguir.",
    "con_gate": "El presupuesto de esta oportunidad tiene Gate: se gestiona por el Gate, no por seguimiento.",
    "estado_no_permitido": "La oportunidad no está en 'presupuesto_enviado': solo desde ese estado se abre un seguimiento.",
    "contacto_registrado": "Ya hay un contacto registrado con el cliente (fecha_ultimo_contacto).",
    "visita_existente": "La oportunidad tiene una visita (que no está cancelada).",
    "plazo_no_cumplido": "El presupuesto aún no ha superado el plazo de seguimiento; saldrá en la lista cuando lo supere.",
}


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================
# SeguimientoRechazado es la base de los rechazos PROPIOS de este endpoint
# y hereda de RechazoNegocio (motivo y mensaje). ConfiguracionIncompleta
# (503) es la compartida de reglas_visita.py. El adaptador captura
# RechazoNegocio, que incluye a todos.


class SeguimientoRechazado(RechazoNegocio):
    """Base de los rechazos propios de /create-followup-task."""


class OportunidadNoEncontrada(SeguimientoRechazado):
    """No existe ninguna oportunidad con ese id (-> 404)."""


class SeguimientoNoPermitido(SeguimientoRechazado):
    """
    La oportunidad no cumple la condición (-> 409). Motivos, uno por
    criterio: sin_presupuesto, con_gate, estado_no_permitido,
    contacto_registrado, visita_existente y plazo_no_cumplido.
    """


# ======================================================================
# La operación completa
# ======================================================================


def abrir_seguimiento(data: FollowupTaskCreate) -> FollowupTaskResponse:
    """
    Abre el seguimiento de una oportunidad, en UNA transacción: o se
    cambian el estado y el log a la vez, o nada.

    Excepciones (el adaptador las traduce a HTTP):
      OportunidadNoEncontrada   404
      ConfiguracionIncompleta   503 (sin log, P4)
      SeguimientoNoPermitido    409, con el motivo del criterio
      RuntimeError              500 (segunda barrera; no debe pasar nunca)
    """
    # Conexión de escritura: commit al salir del "with" sin excepción;
    # rollback (y la excepción sigue hacia arriba) si salta una.
    with get_transactional_connection() as conn:
        # El cursor se cierra SIEMPRE al salir de este bloque. Un "return"
        # dentro de un "with" sale con normalidad, así que se hace commit.
        with conn.cursor() as cursor:
            return abrir_seguimiento_con_cursor(cursor, data)


def abrir_seguimiento_con_cursor(cursor, data: FollowupTaskCreate) -> FollowupTaskResponse:
    """
    Todo el trabajo, con el cursor de quien llama y DENTRO de su
    transacción (no hace commit ni rollback).

    Recibe el cursor, como leer_listado, para que la prueba del límite
    exacto del plazo (plan, 6.1 S1) lo ejecute en una transacción suya que
    termina en ROLLBACK, con el mismo now().

    Orden de las comprobaciones (plan, 2.9):
      404 -> 200 (ya abierto) -> 503 -> 409 (seis criterios) -> 201
    """
    # ------------------------------------------------------------------
    # 1. Encontrar la oportunidad y BLOQUEARLA (404)
    # ------------------------------------------------------------------
    # SQL: el id y el estado de la oportunidad, con FOR UPDATE: bloquea su
    # fila hasta el final de la transacción. POST /visits y POST
    # /gate-decisions bloquean la MISMA fila, así que las tres operaciones
    # sobre una oportunidad se ponen en fila (plan, 2.11). Sin JOIN: solo
    # se bloquea la oportunidad.
    cursor.execute(
        "SELECT o.id, o.estado FROM oportunidades o WHERE o.id = %(oportunidad_id)s FOR UPDATE;",
        {"oportunidad_id": data.oportunidad_id},
    )
    # La única fila, o None si no existe.
    fila = cursor.fetchone()
    # Sin fila: 404. Al lanzar, quien llama hace rollback (y suelta el
    # bloqueo).
    if fila is None:
        raise OportunidadNoEncontrada(
            "oportunidad_no_encontrada",
            f"No existe ninguna oportunidad con id {data.oportunidad_id}.",
        )
    # Desempaquetado: una variable por columna del SELECT.
    oportunidad_id, estado = fila

    # ------------------------------------------------------------------
    # 2. ¿Ya está abierto? (200, sin escribir nada)
    # ------------------------------------------------------------------
    # Va ANTES que el 503 y los 409 (plan, 2.9): tras el 201 el estado ya
    # no es 'presupuesto_enviado', y un reintento de n8n daría 409; y
    # contestar "ya estaba abierto" no necesita la regla.
    if estado == ESTADO_ABIERTO:
        # SQL: el ÚLTIMO log de apertura de esta oportunidad (en N0 solo
        # puede haber uno): su id, el motivo guardado en el detalle (->>
        # lo saca como texto) y su fecha.
        cursor.execute(
            """
            SELECT id, detalle ->> 'motivo', created_at
            FROM logs
            WHERE entity_type = %(entity_type)s
              AND entity_id = %(oportunidad_id)s
              AND accion = %(accion)s
            ORDER BY id DESC
            LIMIT 1;
            """,
            {
                "entity_type": LOG_ENTITY_TYPE_OPORTUNIDAD,
                "oportunidad_id": oportunidad_id,
                "accion": ACCION_SEGUIMIENTO_ABIERTO,
            },
        )
        # El log original, o None si el estado se puso a mano (P5).
        original = cursor.fetchone()
        # Con log: sus datos. Sin log (P5): log_id y fecha_apertura a None,
        # y el motivo de la petición.
        if original is not None:
            log_id, motivo, fecha_log = original
            fecha_apertura = fecha_log.astimezone(ZONA_MADRID)
        else:
            log_id, motivo, fecha_apertura = None, data.motivo, None
        # Respuesta de la repetición (creado=False -> 200). No se ha
        # escrito nada: el commit del "with" no confirma ningún cambio.
        return FollowupTaskResponse(
            log_id=log_id,
            oportunidad_id=oportunidad_id,
            motivo=motivo,
            estado_oportunidad=ESTADO_ABIERTO,
            fecha_apertura=fecha_apertura,
            creado=False,
        )

    # ------------------------------------------------------------------
    # 3. El reloj y la regla de 48 h (503)
    # ------------------------------------------------------------------
    # SQL: now() de Postgres. Dentro de la transacción no cambia: es el
    # "ahora" del plazo.
    cursor.execute("SELECT now();")
    # fetchone()[0]: la única columna de la única fila.
    ahora = cursor.fetchone()[0]
    # SQL: el valor de la regla, leído en ESTA llamada (nunca escrito en el
    # código). CLAVE_SEGUIMIENTO se lee del módulo al ejecutarse.
    cursor.execute("SELECT valor FROM reglas_negocio WHERE clave = %(clave)s;", {"clave": CLAVE_SEGUIMIENTO})
    # La fila, o None si la regla no existe.
    fila_regla = cursor.fetchone()
    # El valor (Decimal), o None si no hay fila.
    valor = fila_regla[0] if fila_regla is not None else None
    # La MISMA validación que el listado: None si es un entero de 1 a 8760.
    problema = problema_horas(CLAVE_SEGUIMIENTO, valor)
    # Con problema: 503, SIN log (P4, como el listado con la misma regla).
    if problema is not None:
        raise ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIGURACION, [problema])
    # Válida: las horas como int (llegaban como Decimal('48.00')).
    horas = int(valor)

    # ------------------------------------------------------------------
    # 4. Los seis criterios, en UNA consulta (409)
    # ------------------------------------------------------------------
    # Los parámetros de la condición compartida, más el id. "|" une dos
    # diccionarios en uno nuevo.
    params = parametros_condicion(ahora, horas) | {"oportunidad_id": oportunidad_id}
    # SQL: la fecha del presupuesto y, como columnas de sí o no, los seis
    # criterios COMPARTIDOS (columnas_criterios, el mismo texto que el
    # WHERE del listado). LEFT JOIN: si no hay presupuesto, p.* es NULL y
    # el primer criterio da false. De presupuestos no se lee ningún
    # importe. Los dos trozos son textos fijos del programa.
    cursor.execute(
        "SELECT p.created_at, " + columnas_criterios() + " "
        "FROM oportunidades o "
        "LEFT JOIN presupuestos p ON p.oportunidad_id = o.id "
        "WHERE o.id = %(oportunidad_id)s;",
        params,
    )
    # Primera columna: la fecha del presupuesto; el resto, los criterios.
    fecha_presupuesto, *valores = cursor.fetchone()
    # El motivo del primer criterio que falla, o None si se cumplen todos.
    motivo_409 = primer_criterio_incumplido(valores)
    # Si falla alguno: 409 con su motivo, sin escribir nada.
    if motivo_409 is not None:
        raise SeguimientoNoPermitido(motivo_409, MENSAJES_409[motivo_409])

    # ------------------------------------------------------------------
    # 5. Escritura: el estado (con la segunda barrera) y el log
    # ------------------------------------------------------------------
    # SQL: pasa la oportunidad a 'seguimiento_pendiente' y anota cuándo
    # cambió (updated_at). NO toca fecha_ultimo_contacto: no ha habido
    # contacto (pre-decisión 8). Segunda barrera (P10): solo cambia la fila
    # si SIGUE en 'presupuesto_enviado'.
    cursor.execute(
        """
        UPDATE oportunidades
        SET estado = %(estado_nuevo)s, updated_at = now()
        WHERE id = %(oportunidad_id)s
          AND estado = %(estado_por_abrir)s;
        """,
        {"estado_nuevo": ESTADO_ABIERTO, "oportunidad_id": oportunidad_id, "estado_por_abrir": ESTADO_POR_ABRIR},
    )
    # rowcount: cuántas filas cambió el UPDATE. Con el FOR UPDATE tiene que
    # ser 1 siempre; si no, algo se ha saltado el bloqueo y se para todo
    # (500 y rollback) en vez de escribir un log de algo que no ocurrió.
    if cursor.rowcount != 1:
        raise RuntimeError(
            f"Segunda barrera: el UPDATE de la oportunidad {oportunidad_id} cambió "
            f"{cursor.rowcount} filas (se esperaba 1)."
        )

    # El detalle del log: con qué motivo y con qué plazo se abrió (si un
    # día cambia la regla, este seguimiento conserva el suyo). La fecha va
    # como texto ISO porque JSON no tiene tipo fecha. Ningún dato personal
    # ni importe.
    detalle = {
        "motivo": data.motivo.value,
        "estado_anterior": ESTADO_POR_ABRIR,
        "horas_seguimiento_presupuesto": horas,
        "fecha_presupuesto": fecha_presupuesto.astimezone(ZONA_MADRID).isoformat(),
    }
    # SQL: inserta UNA fila en logs, asociada a la oportunidad, con la
    # acción 'seguimiento_abierto' y el detalle como JSONB. RETURNING
    # devuelve su id y su fecha (la de apertura).
    cursor.execute(
        """
        INSERT INTO logs (entity_type, entity_id, accion, detalle)
        VALUES (%(entity_type)s, %(oportunidad_id)s, %(accion)s, %(detalle)s)
        RETURNING id, created_at;
        """,
        {
            "entity_type": LOG_ENTITY_TYPE_OPORTUNIDAD,
            "oportunidad_id": oportunidad_id,
            "accion": ACCION_SEGUIMIENTO_ABIERTO,
            "detalle": Json(detalle),
        },
    )
    # Las dos columnas del RETURNING, desempaquetadas.
    log_id, fecha_log = cursor.fetchone()

    # La respuesta de un seguimiento NUEVO (creado=True -> 201). Quien
    # llama hace el commit al salir de su "with".
    return FollowupTaskResponse(
        log_id=log_id,
        oportunidad_id=oportunidad_id,
        motivo=data.motivo,
        estado_oportunidad=ESTADO_ABIERTO,
        fecha_apertura=fecha_log.astimezone(ZONA_MADRID),
        creado=True,
    )
