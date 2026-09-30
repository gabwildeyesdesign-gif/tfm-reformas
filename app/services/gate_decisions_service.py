"""
Lógica de negocio de POST /gate-decisions: el resultado de la llamada de
administración a un cliente cuyo presupuesto activó el Gate (plan:
docs/Plan_Endpoint_Gate_Decisions.txt, secciones 1.4, 1.6, 1.9 y 2.2).

Dos decisiones posibles:
  - visita_acordada: se crea una visita YA confirmada (la acordó
    administración por teléfono) y la oportunidad pasa a 'visita_agendada'.
  - descartar: la oportunidad pasa a 'perdida', con un motivo.
En los dos casos queda una fila en decisiones_gate con el informe de la
llamada (auditoría anual) y una fila en logs.

Las reglas de fecha de la visita son EXACTAMENTE las de POST /visits: se
usan desde app/services/reglas_visita.py, sin copiarlas.

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando una
decisión no se puede registrar, lanza una excepción con un "motivo" (código
estable) y un "mensaje" (texto para administración); el adaptador
app/api/gate_decisions.py decide el código HTTP de cada una.
"""

# Json: le dice a psycopg2 que un diccionario de Python va a una columna
# JSONB (logs.detalle).
from psycopg2.extras import Json

from app.db.connection import get_transactional_connection
from app.schemas.gate_decisions import (
    DecisionGate,
    GateDecisionCreate,
    GateDecisionResponse,
)

# Reglas de fecha y de configuración COMPARTIDAS con POST /visits (plan,
# 2.3). FechaNoValida no se importa porque aquí no se lanza a mano: la
# lanza validar_fecha, y el adaptador la captura como RechazoNegocio.
from app.services.reglas_visita import (
    ZONA_MADRID,
    ConfiguracionIncompleta,
    RechazoNegocio,
    combinar_fecha_hora_madrid,
    leer_reglas_visita,
    registrar_configuracion_incompleta,
    validar_fecha,
)

# Estados de visita y de oportunidad que ya define POST /visits. Se importan
# en vez de copiarse (decisión D3 del Bloque C2): si un día cambia el nombre
# de un estado, se cambia en un solo sitio.
#   ESTADOS_VISITA_ACTIVA          ('solicitada', 'confirmada'): las que
#                                  cuentan para el índice único parcial.
#   VISITA_CONFIRMADA              'confirmada'.
#   ESTADO_OPORTUNIDAD_CON_VISITA  'visita_agendada'.
from app.services.visits_service import (
    ESTADO_OPORTUNIDAD_CON_VISITA,
    ESTADOS_VISITA_ACTIVA,
    VISITA_CONFIRMADA,
)

# Origen con el que este endpoint firma el log de configuración incompleta
# (plan, 2.3: /visits firma "visits").
ORIGEN_LOG = "gate_decisions"

# El ÚNICO estado de la oportunidad desde el que se puede decidir un Gate
# por primera vez (409 estado_no_permitido en cualquier otro).
ESTADO_PENDIENTE_APROBACION = "pendiente_aprobacion"

# Estado al que pasa la oportunidad al descartar.
ESTADO_PERDIDA = "perdida"

# visitas.texto_cliente es NOT NULL, pero en una visita acordada por
# teléfono el cliente no ha escrito nada. Decisión P2: este texto fijo. El
# informe completo de la llamada queda en decisiones_gate, enlazado por
# visita_id.
TEXTO_VISITA_ACORDADA = "Visita acordada por teléfono por administración tras la revisión del Gate."

# Registro en logs, con el mismo criterio que /visits y calculate-estimate:
# la historia de una oportunidad se consulta en un solo sitio.
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_DECISION_REGISTRADA = "gate_decision_registrada"


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================
# DecisionGateRechazada es la base de los rechazos PROPIOS de este
# endpoint y hereda de RechazoNegocio (reglas_visita.py), la base común con
# /visits. Los rechazos COMPARTIDOS, FechaNoValida (422) y
# ConfiguracionIncompleta (503), vienen de reglas_visita.py. El adaptador
# captura RechazoNegocio, que incluye a todos.


class DecisionGateRechazada(RechazoNegocio):
    """Base de los rechazos propios de /gate-decisions (motivo y mensaje los guarda RechazoNegocio)."""


class OportunidadNoEncontrada(DecisionGateRechazada):
    """No existe ninguna oportunidad con ese id (-> 404)."""


class EstadoNoPermiteDecision(DecisionGateRechazada):
    """
    La oportunidad no admite ESTA decisión (-> 409). Motivos:
    sin_presupuesto, sin_gate, decision_ya_registrada, estado_no_permitido
    y visita_activa_existente.
    """


# ======================================================================
# La operación completa
# ======================================================================


def registrar_decision(data: GateDecisionCreate) -> GateDecisionResponse:
    """
    Registra la decisión de administración sobre el Gate de una oportunidad.

    Todo ocurre en UNA transacción (get_transactional_connection): o se
    aplican todos los cambios (visita, decisión, estado y log) o ninguno.

    Las comprobaciones siguen el orden de la sección 1.9 del plan:
      404 oportunidad_no_encontrada
      409 sin_presupuesto
      409 sin_gate
      ¿ya hay decisión?  igual -> 200 (creado=False, sin escribir nada)
                         distinta -> 409 decision_ya_registrada
      409 estado_no_permitido
      solo visita_acordada: 503 configuracion_incompleta,
                            422 fecha_pasada / fin_de_semana / fuera_de_franja,
                            409 visita_activa_existente
    """
    # El instante pedido (fecha + hora en Madrid), solo si hay visita. Se
    # calcula antes de abrir la transacción porque no necesita la base de
    # datos, y hace falta tanto para la repetición (paso 4) como para las
    # reglas de fecha (paso 6).
    if data.decision == DecisionGate.VISITA_ACORDADA:
        inicio = combinar_fecha_hora_madrid(data.fecha, data.hora)
    else:
        inicio = None

    # Texto de la decisión y del motivo, tal como se guardan en la base de
    # datos. .value saca el texto de un Enum ("descartar"); se pasa así a
    # psycopg2 para no depender de cómo trata un Enum.
    decision_texto = data.decision.value
    motivo_texto = data.motivo.value if data.motivo is not None else None

    # rechazo_diferido: mismo mecanismo que /visits. El log de configuración
    # incompleta tiene que QUEDARSE; si la excepción se lanzara dentro del
    # "with", get_transactional_connection haría rollback y el log se
    # perdería. Se guarda aquí y se lanza después del commit.
    rechazo_diferido = None

    with get_transactional_connection() as conn:
        cursor = conn.cursor()

        # --------------------------------------------------------------
        # 1. Encontrar la oportunidad y BLOQUEARLA
        # --------------------------------------------------------------
        # FOR UPDATE OF o: bloquea SOLO la fila de la oportunidad hasta el
        # final de la transacción. Si llegan dos peticiones a la vez para
        # la misma oportunidad (un reintento de n8n, o dos personas de
        # administración), la segunda espera aquí a que la primera
        # termine, y después ve la decisión que la primera escribió. Es la
        # primera barrera contra decisiones duplicadas; la segunda es el
        # UNIQUE de decisiones_gate.oportunidad_id.
        #
        # oportunidades va en el FROM (el lado obligatorio) y presupuestos
        # con LEFT JOIN, porque puede no existir. Postgres no permite FOR
        # UPDATE sobre el lado opcional de un LEFT JOIN; por eso el "OF o".
        #
        # now(): la hora de la base de datos, referencia para "en el
        # futuro". Dentro de una transacción no cambia.
        #
        # NO se lee ningún importe (plan, 1.5).
        cursor.execute(
            """
            SELECT o.id, o.estado, p.id, p.requiere_aprobacion, now()
            FROM oportunidades o
            LEFT JOIN presupuestos p ON p.oportunidad_id = o.id
            WHERE o.id = %s
            FOR UPDATE OF o;
            """,
            (data.oportunidad_id,),
        )
        fila = cursor.fetchone()
        if fila is None:
            raise OportunidadNoEncontrada(
                "oportunidad_no_encontrada",
                f"No existe ninguna oportunidad con id {data.oportunidad_id}.",
            )
        # Desempaquetado: cada variable recibe una columna del SELECT, en
        # el mismo orden.
        oportunidad_id, estado, presupuesto_id, requiere_aprobacion, ahora = fila

        # --------------------------------------------------------------
        # 2. y 3. ¿Hay presupuesto, y tiene Gate? (409)
        # --------------------------------------------------------------
        # sin_gate va ANTES que la decisión previa: una oportunidad sin Gate
        # nunca puede tener decisión, y el motivo útil es sin_gate (1.9).
        if presupuesto_id is None:
            raise EstadoNoPermiteDecision(
                "sin_presupuesto",
                "La oportunidad todavía no tiene presupuesto calculado, así que no hay ningún Gate que decidir.",
            )
        if not requiere_aprobacion:
            raise EstadoNoPermiteDecision(
                "sin_gate",
                "El presupuesto de esta oportunidad no requiere aprobación (no tiene Gate): "
                "no hay ninguna decisión que registrar.",
            )

        # --------------------------------------------------------------
        # 4. ¿Ya hay una decisión registrada?
        # --------------------------------------------------------------
        # Como mucho una (UNIQUE en decisiones_gate.oportunidad_id). El LEFT
        # JOIN con visitas trae la fecha de la visita acordada, para saber
        # si la petición es una repetición exacta; en un descarte no hay
        # visita y v.fecha_propuesta llega como None.
        #
        # Va ANTES que el estado (paso 5): tras la primera decisión el
        # estado ya no es pendiente_aprobacion, y un reintento legítimo de
        # n8n daría 409 estado_no_permitido en vez de 200 (plan, 1.9).
        cursor.execute(
            """
            SELECT d.id, d.decision, d.motivo, d.visita_id, v.fecha_propuesta
            FROM decisiones_gate d
            LEFT JOIN visitas v ON v.id = d.visita_id
            WHERE d.oportunidad_id = %s;
            """,
            (oportunidad_id,),
        )
        previa = cursor.fetchone()

        if previa is not None:
            previa_id, previa_decision, previa_motivo, previa_visita_id, previa_fecha = previa

            # ¿Es la MISMA decisión? (plan, 1.6)
            #   visita_acordada: misma decisión y misma fecha y hora. La
            #     fecha llega de Postgres en UTC; comparar dos datetime con
            #     zona compara el instante real, así que coincide con
            #     inicio aunque este esté en hora de Madrid.
            #   descartar: misma decisión y mismo motivo (P3).
            if previa_decision == decision_texto:
                if data.decision == DecisionGate.VISITA_ACORDADA:
                    es_repeticion = previa_fecha == inicio
                else:
                    es_repeticion = previa_motivo == motivo_texto
            else:
                es_repeticion = False

            if es_repeticion:
                # Repetición: se devuelve lo que ya estaba, SIN escribir
                # nada (tampoco fecha_ultimo_contacto). El informe de esta
                # petición se ignora y se conserva el original.
                #
                # "return" dentro de un "with": Python sale del bloque con
                # normalidad, así que get_transactional_connection hace
                # commit (de nada) y devuelve la conexión al pool.
                return GateDecisionResponse(
                    decision_id=previa_id,
                    oportunidad_id=oportunidad_id,
                    decision=previa_decision,
                    motivo=previa_motivo,
                    estado_oportunidad=estado,
                    visita_id=previa_visita_id,
                    # Expresión condicional "A if condición else B": se
                    # pasa a hora de Madrid solo si hay fecha.
                    fecha_propuesta=previa_fecha.astimezone(ZONA_MADRID) if previa_fecha is not None else None,
                    creado=False,
                )

            # Otra decisión, otra fecha u otro motivo: la decisión es
            # definitiva (R2), se registra una sola vez por oportunidad.
            raise EstadoNoPermiteDecision(
                "decision_ya_registrada",
                f"Esta oportunidad ya tiene registrada otra decisión ({previa_decision}, id {previa_id}). "
                "La decisión es definitiva: si fue un error, hay que corregirla a mano en la base de datos.",
            )

        # --------------------------------------------------------------
        # 5. ¿El estado permite decidir? (409)
        # --------------------------------------------------------------
        if estado != ESTADO_PENDIENTE_APROBACION:
            raise EstadoNoPermiteDecision(
                "estado_no_permitido",
                f"La oportunidad está en estado '{estado}', y solo se puede registrar una decisión "
                f"del Gate en '{ESTADO_PENDIENTE_APROBACION}'.",
            )

        # --------------------------------------------------------------
        # 6. Preparar la decisión
        # --------------------------------------------------------------
        if data.decision == DecisionGate.VISITA_ACORDADA:
            # Reglas de visita, las MISMAS de /visits (503 si faltan).
            reglas, problemas = leer_reglas_visita(cursor)
            if problemas:
                # Rastro para administración, firmado con el origen
                # "gate_decisions", y excepción DIFERIDA (ver arriba).
                registrar_configuracion_incompleta(cursor, oportunidad_id, problemas, ORIGEN_LOG)
                rechazo_diferido = ConfiguracionIncompleta(
                    "configuracion_incompleta",
                    "No se puede registrar la visita por un problema de configuración de las visitas "
                    "(reglas_negocio). Revisa las claves indicadas en 'faltan'.",
                    problemas,
                )
            else:
                # Reglas de la fecha: futura, de lunes a viernes y dentro
                # de una franja. Si falla, validar_fecha lanza FechaNoValida
                # (422), y el "with" hace rollback de todo.
                validar_fecha(inicio, ahora, reglas)

                # P1: ¿ya hay una visita activa? No debería (/visits
                # rechaza los casos con Gate), pero sin esta comprobación el
                # índice único parcial de visitas lo rechazaría con un 500.
                cursor.execute(
                    "SELECT id FROM visitas WHERE oportunidad_id = %s AND estado = ANY(%s);",
                    (oportunidad_id, list(ESTADOS_VISITA_ACTIVA)),
                )
                activa = cursor.fetchone()
                if activa is not None:
                    raise EstadoNoPermiteDecision(
                        "visita_activa_existente",
                        f"La oportunidad ya tiene una visita activa (id {activa[0]}); no se puede acordar otra.",
                    )

                # La visita nace 'confirmada': la ha confirmado
                # administración por teléfono. psycopg2 envía inicio con su
                # desfase (+02:00 o +01:00) y Postgres lo guarda como un
                # instante (TIMESTAMPTZ). RETURNING devuelve el id asignado
                # y la fecha tal como quedó guardada.
                cursor.execute(
                    """
                    INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, fecha_propuesta;
                    """,
                    (oportunidad_id, inicio, VISITA_CONFIRMADA, TEXTO_VISITA_ACORDADA),
                )
                visita_id, fecha_guardada = cursor.fetchone()
                fecha_madrid = fecha_guardada.astimezone(ZONA_MADRID)
                nuevo_estado = ESTADO_OPORTUNIDAD_CON_VISITA
        else:
            # Descartar: no hay visita.
            visita_id = None
            fecha_madrid = None
            nuevo_estado = ESTADO_PERDIDA

        # --------------------------------------------------------------
        # 7. Escritura (solo si no hay un 503 pendiente)
        # --------------------------------------------------------------
        if rechazo_diferido is None:
            # La decisión. visita_id es None al descartar; al acordar, la
            # clave foránea doble (visita_id, oportunidad_id) garantiza que
            # la visita es de ESTA oportunidad. data.informe ya llega
            # recortado del esquema, como exige el CHECK.
            cursor.execute(
                """
                INSERT INTO decisiones_gate (oportunidad_id, visita_id, decision, motivo, informe)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (oportunidad_id, visita_id, decision_texto, motivo_texto, data.informe),
            )
            # fetchone() devuelve una tupla de una columna, (id,); [0] saca
            # el valor.
            decision_id = cursor.fetchone()[0]

            # La oportunidad cambia de estado. fecha_ultimo_contacto = now()
            # en las dos decisiones (P5): administración acaba de hablar
            # con el cliente. Al salir de 'pendiente_aprobacion', el
            # recordatorio del Gate de WF3 deja de verla solo (P8).
            #
            # presupuestos NO se toca: requiere_aprobacion sigue true (el
            # Gate es permanente) y aprobado_por sigue NULL (N0 no
            # identifica al operador).
            cursor.execute(
                """
                UPDATE oportunidades
                SET estado = %s, fecha_ultimo_contacto = now(), updated_at = now()
                WHERE id = %s;
                """,
                (nuevo_estado, oportunidad_id),
            )

            # Log. El informe NO se copia (P9): vive en decisiones_gate,
            # fuente única, y el log guarda decision_id para enlazarlo. La
            # fecha va como texto ISO porque JSON no tiene tipo fecha.
            detalle = {
                "decision_id": decision_id,
                "decision": decision_texto,
                "motivo": motivo_texto,
                "visita_id": visita_id,
                "fecha_propuesta": fecha_madrid.isoformat() if fecha_madrid is not None else None,
            }
            cursor.execute(
                "INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES (%s, %s, %s, %s);",
                (LOG_ENTITY_TYPE_OPORTUNIDAD, oportunidad_id, ACCION_DECISION_REGISTRADA, Json(detalle)),
            )

            respuesta = GateDecisionResponse(
                decision_id=decision_id,
                oportunidad_id=oportunidad_id,
                decision=data.decision,
                motivo=data.motivo,
                estado_oportunidad=nuevo_estado,
                visita_id=visita_id,
                fecha_propuesta=fecha_madrid,
                creado=True,
            )

    # Fuera del "with": la transacción ya se ha confirmado (con el log del
    # 503 dentro, si lo hubo).
    if rechazo_diferido is not None:
        raise rechazo_diferido
    return respuesta
