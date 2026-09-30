"""
Lógica de negocio de POST /visits: la SOLICITUD de visita técnica que el
Agente 2 registra desde el chat (plan: docs/Plan_Endpoint_Visits.txt).

Una visita es una solicitud, no una reserva: administración llama siempre
al cliente para confirmar. El backend no habla con ningún calendario.

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando
una solicitud no se puede aceptar, lanza una excepción de Python con un
"motivo" (un código estable) y un "mensaje" (texto para el cliente); el
adaptador app/api/visits.py decide qué código HTTP corresponde a cada
una.
"""

from psycopg2.extras import Json

from app.db.connection import get_transactional_connection
from app.schemas.visits import VisitaCreate, VisitaResponse

# Reglas de fecha y de configuración COMPARTIDAS con POST /gate-decisions.
# Hasta el 2026-09-30 vivían en este archivo; se mudaron a reglas_visita.py
# sin cambiar su lógica (plan de /gate-decisions, sección 2.3).
from app.services.reglas_visita import (
    ZONA_MADRID,
    ConfiguracionIncompleta,
    RechazoNegocio,
    combinar_fecha_hora_madrid,
    leer_reglas_visita,
    registrar_configuracion_incompleta,
    validar_fecha,
)

# Origen con el que este endpoint firma el log de configuración incompleta.
ORIGEN_LOG = "visits"

# Estados de la oportunidad desde los que se puede pedir visita.
# 'visita_agendada' está incluido para poder CAMBIAR la fecha de una
# solicitud ya hecha (sustitución).
ESTADOS_QUE_PERMITEN_VISITA = ("presupuesto_enviado", "seguimiento_pendiente", "visita_agendada")

# Estado al que pasa la oportunidad tras la solicitud.
ESTADO_OPORTUNIDAD_CON_VISITA = "visita_agendada"

# Estados de visita. Una visita "activa" es la que todavía cuenta: como
# mucho hay una por oportunidad (índice único parcial de paso9).
VISITA_SOLICITADA = "solicitada"
VISITA_CONFIRMADA = "confirmada"
VISITA_CANCELADA = "cancelada"
ESTADOS_VISITA_ACTIVA = (VISITA_SOLICITADA, VISITA_CONFIRMADA)

# Registro en logs, con el mismo criterio que calculate-estimate: la
# historia de una oportunidad se consulta en un solo sitio.
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_SOLICITADA = "visita_solicitada"
ACCION_SUSTITUIDA = "visita_sustituida"


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================
# VisitaRechazada es la base de los rechazos PROPIOS de /visits, y hereda
# de RechazoNegocio (reglas_visita.py), la base común con /gate-decisions.
# Los rechazos COMPARTIDOS, FechaNoValida (422) y ConfiguracionIncompleta
# (503), viven en reglas_visita.py. El adaptador (app/api/visits.py)
# captura RechazoNegocio, que incluye a todos, y decide el código HTTP de
# cada familia.


class VisitaRechazada(RechazoNegocio):
    """Base de los rechazos propios de /visits (motivo y mensaje los guarda RechazoNegocio)."""


class LeadNoEncontrado(VisitaRechazada):
    """No hay ningún lead con ese lead_token (-> 404)."""


class EstadoNoPermiteVisita(VisitaRechazada):
    """El estado del lead no permite pedir visita desde el chat (-> 409)."""


# ======================================================================
# La operación completa
# ======================================================================


def solicitar_visita(data: VisitaCreate) -> VisitaResponse:
    """
    Registra la solicitud de visita del lead con ese lead_token.

    Todo ocurre en UNA transacción (get_transactional_connection): o se
    aplican todos los cambios (cancelar la visita anterior, crear la nueva,
    cambiar el estado de la oportunidad y escribir el log) o ninguno.

    Excepciones que puede lanzar (el adaptador las traduce a HTTP):
      LeadNoEncontrado         404  lead_no_encontrado
      EstadoNoPermiteVisita    409  sin_presupuesto, presupuesto_con_gate,
                                    estado_no_permitido, visita_confirmada
      ConfiguracionIncompleta  503  configuracion_incompleta
      FechaNoValida            422  fecha_pasada, fin_de_semana,
                                    fuera_de_franja
    """
    inicio = combinar_fecha_hora_madrid(data.fecha, data.hora)

    # rechazo_diferido: la configuración incompleta se registra en logs, y
    # ese INSERT tiene que QUEDARSE. Si la excepción se lanzara dentro del
    # "with", get_transactional_connection haría rollback y el log se
    # perdería. Por eso se guarda aquí, el "with" termina con normalidad
    # (commit del log) y se lanza justo después.
    rechazo_diferido = None

    with get_transactional_connection() as conn:
        cursor = conn.cursor()

        # --------------------------------------------------------------
        # 1. Encontrar la oportunidad y BLOQUEARLA
        # --------------------------------------------------------------
        # Misma oportunidad que GET /leads/session: la primera por o.id.
        #
        # FOR UPDATE OF o: bloquea SOLO la fila de la oportunidad hasta el
        # final de la transacción. Si llegan dos peticiones a la vez para
        # el mismo lead_token, la segunda espera aquí a que la primera
        # termine, y después ve lo que la primera escribió (en READ
        # COMMITTED, cada sentencia ve lo confirmado hasta ese momento).
        # Es la primera barrera contra duplicados; la segunda es el índice
        # único parcial de visitas (plan, 2.4).
        #
        # oportunidades va con JOIN normal (no LEFT JOIN) porque Postgres
        # no permite FOR UPDATE sobre el lado opcional de un LEFT JOIN.
        # presupuestos sí va con LEFT JOIN: puede no existir.
        #
        # now(): la hora de la base de datos, en la misma consulta. Es la
        # referencia para "en el futuro". Dentro de una transacción now()
        # no cambia, así que todas las comprobaciones usan el mismo ahora.
        #
        # NO se lee ningún importe (D18.5).
        cursor.execute(
            """
            SELECT o.id, o.estado, p.id, p.requiere_aprobacion, now()
            FROM leads l
            JOIN oportunidades o ON o.lead_id = l.id
            LEFT JOIN presupuestos p ON p.oportunidad_id = o.id
            WHERE l.lead_token = %s
            ORDER BY o.id
            LIMIT 1
            FOR UPDATE OF o;
            """,
            (data.lead_token,),
        )
        fila = cursor.fetchone()
        if fila is None:
            raise LeadNoEncontrado(
                "lead_no_encontrado",
                "No hay ninguna solicitud de presupuesto asociada a esta conversación.",
            )
        oportunidad_id, estado, presupuesto_id, requiere_aprobacion, ahora = fila

        # --------------------------------------------------------------
        # 2. ¿El estado permite pedir visita? (409)
        # --------------------------------------------------------------
        # Orden: sin presupuesto -> Gate -> estado. El Gate va antes que el
        # estado porque, con un Gate pendiente, el estado también falla
        # ('pendiente_aprobacion'), y el motivo útil para el agente es el
        # del Gate.
        if presupuesto_id is None:
            raise EstadoNoPermiteVisita(
                "sin_presupuesto",
                "Todavía no hay un presupuesto calculado, así que aún no se puede pedir la visita.",
            )
        # Defensa del BACKEND (regla 5): un cliente con Gate no puede pedir
        # visita desde el chat, aunque el agente se equivoque. OJO,
        # limitación asumida (P5): requiere_aprobacion no vuelve a false al
        # aprobarse el Gate, así que este bloqueo es permanente. Se revisa
        # con POST /gate-decisions.
        if requiere_aprobacion:
            raise EstadoNoPermiteVisita(
                "presupuesto_con_gate",
                "Un técnico está revisando el presupuesto personalmente y se pondrá en contacto "
                "contigo para concretar la visita.",
            )
        if estado not in ESTADOS_QUE_PERMITEN_VISITA:
            raise EstadoNoPermiteVisita(
                "estado_no_permitido",
                "En la situación actual de tu solicitud no se puede pedir una visita desde el chat; "
                "administración se pondrá en contacto contigo.",
            )

        # --------------------------------------------------------------
        # 3. Reglas de negocio de la visita (503 si faltan)
        # --------------------------------------------------------------
        reglas, problemas = leer_reglas_visita(cursor)
        if problemas:
            # Se deja rastro para administración (P2), firmado con el
            # origen "visits", y se DIFIERE la excepción para que este
            # INSERT se confirme (ver arriba).
            registrar_configuracion_incompleta(cursor, oportunidad_id, problemas, ORIGEN_LOG)
            rechazo_diferido = ConfiguracionIncompleta(
                "configuracion_incompleta",
                "Ahora mismo no se pueden registrar visitas por un problema de configuración; "
                "administración se pondrá en contacto contigo.",
                problemas,
            )
        else:
            # ----------------------------------------------------------
            # 4. Reglas de la fecha (422)
            # ----------------------------------------------------------
            validar_fecha(inicio, ahora, reglas)

            # ----------------------------------------------------------
            # 5. ¿Ya hay una visita activa para esta oportunidad?
            # ----------------------------------------------------------
            # Como mucho una, por el índice único parcial.
            cursor.execute(
                """
                SELECT id, fecha_propuesta, estado FROM visitas
                WHERE oportunidad_id = %s AND estado = ANY(%s);
                """,
                (oportunidad_id, list(ESTADOS_VISITA_ACTIVA)),
            )
            activa = cursor.fetchone()

            if activa is not None:
                activa_id, activa_fecha, activa_estado = activa
                # Misma fecha: es una repetición (un reintento de n8n o el
                # cliente repitiendo lo mismo). Se devuelve la visita que
                # ya existe, sin escribir nada; el texto_cliente nuevo se
                # ignora, igual que POST /leads con un token repetido.
                # activa_fecha llega de Postgres en UTC; la comparación es
                # entre instantes, así que coincide con inicio aunque este
                # esté en hora de Madrid.
                if activa_fecha == inicio:
                    return VisitaResponse(
                        visita_id=activa_id,
                        estado=activa_estado,
                        fecha_propuesta=activa_fecha.astimezone(ZONA_MADRID),
                        sustituye_a=None,
                        creado=False,
                    )
                # Otra fecha y ya CONFIRMADA: administración ya acordó esa
                # fecha por teléfono, y el chat no la deshace (P4).
                if activa_estado == VISITA_CONFIRMADA:
                    raise EstadoNoPermiteVisita(
                        "visita_confirmada",
                        "Ya tienes una visita confirmada con administración. Para cambiarla, "
                        "administración se pondrá en contacto contigo.",
                    )
                # Otra fecha y 'solicitada': se cancela y se sustituye.
                cursor.execute(
                    "UPDATE visitas SET estado = %s, updated_at = now() WHERE id = %s;",
                    (VISITA_CANCELADA, activa_id),
                )
                sustituye_a = activa_id
                fecha_anterior = activa_fecha.astimezone(ZONA_MADRID)
            else:
                sustituye_a = None
                fecha_anterior = None

            # ----------------------------------------------------------
            # 6. Crear la visita nueva
            # ----------------------------------------------------------
            # psycopg2 envía inicio con su desfase (+02:00 o +01:00), y
            # Postgres lo guarda como un instante (TIMESTAMPTZ).
            cursor.execute(
                """
                INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente)
                VALUES (%s, %s, %s, %s)
                RETURNING id, fecha_propuesta, estado;
                """,
                (oportunidad_id, inicio, VISITA_SOLICITADA, data.texto_cliente),
            )
            visita_id, fecha_guardada, estado_visita = cursor.fetchone()

            # ----------------------------------------------------------
            # 7. La oportunidad pasa a 'visita_agendada'
            # ----------------------------------------------------------
            cursor.execute(
                "UPDATE oportunidades SET estado = %s, updated_at = now() WHERE id = %s;",
                (ESTADO_OPORTUNIDAD_CON_VISITA, oportunidad_id),
            )

            # ----------------------------------------------------------
            # 8. Registro en logs
            # ----------------------------------------------------------
            # Las fechas van como texto ISO (isoformat) porque JSON no
            # tiene un tipo fecha.
            fecha_madrid = fecha_guardada.astimezone(ZONA_MADRID)
            detalle = {
                "visita_id": visita_id,
                "fecha_propuesta": fecha_madrid.isoformat(),
                "texto_cliente": data.texto_cliente,
            }
            if sustituye_a is not None:
                detalle["sustituye_a"] = sustituye_a
                detalle["fecha_anterior"] = fecha_anterior.isoformat()
            cursor.execute(
                "INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES (%s, %s, %s, %s);",
                (
                    LOG_ENTITY_TYPE_OPORTUNIDAD,
                    oportunidad_id,
                    ACCION_SUSTITUIDA if sustituye_a is not None else ACCION_SOLICITADA,
                    Json(detalle),
                ),
            )

            respuesta = VisitaResponse(
                visita_id=visita_id,
                estado=estado_visita,
                fecha_propuesta=fecha_madrid,
                sustituye_a=sustituye_a,
                creado=True,
            )

    # Fuera del "with": la transacción ya se ha confirmado.
    if rechazo_diferido is not None:
        raise rechazo_diferido
    return respuesta
