"""
Lógica de negocio de GET /gate-avisos/{oportunidad_id}: los datos de un
caso con Gate para el aviso a administración (WF2) y para el formulario de
POST /gate-decisions (plan: docs/Plan_Endpoint_Aviso_Gate.txt).

SOLO LECTURA (pre-decisión 8): usa get_db_connection(), que termina con
rollback, y no escribe nada en ninguna tabla, tampoco en logs.

Dos consultas (P5): la primera es la "puerta" y no lee ningún dato
personal; la segunda solo se ejecuta si la oportunidad tiene Gate. Así, en
un caso sin Gate, el servicio ni siquiera llega a leer el nombre, el email
o el teléfono (minimización, RGPD).

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando no
puede devolver el aviso, lanza una excepción con un "motivo" (código
estable) y un "mensaje"; el adaptador app/api/aviso_gate.py decide el
código HTTP de cada una.

Desde el Bloque B1 de la ficha (docs/Plan_Endpoint_Ficha_Oportunidad.txt,
sección 4), las columnas del caso y la construcción del contacto, la
reforma, las fotos y el presupuesto viven en datos_oportunidad.py, que
comparte con la ficha. Aquí se quedan la puerta, la decisión y la
respuesta. Sin cambios de comportamiento.
"""

# La conexión de SOLO LECTURA del pool: al salir del "with" hace rollback y
# devuelve la conexión, así que es imposible que este servicio confirme
# una escritura.
from app.db.connection import get_db_connection

# El esquema de la respuesta y las dos piezas que se construyen aquí
# (app/schemas/aviso_gate.py).
from app.schemas.aviso_gate import AvisoGateResponse, DecisionAviso, PresupuestoAviso

# Lo compartido con la ficha (app/services/datos_oportunidad.py): las
# columnas del caso y las funciones que construyen sus piezas.
# deducir_sin_iva no se usa en este archivo: se importa para que siga
# existiendo aquí con el mismo nombre (los scripts de verificación la
# importan de este módulo; plan de la ficha, 4.2).
from app.services.datos_oportunidad import (
    COLUMNAS_CASO,
    a_madrid,
    campos_presupuesto,
    caso_desde_fila,
    columnas_caso_sql,
    contacto_desde,
    deducir_sin_iva,
    fotos_desde,
    reforma_desde,
)

# RechazoNegocio: la base común de los rechazos de negocio de todos los
# endpoints (guarda motivo y mensaje).
from app.services.reglas_visita import RechazoNegocio


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================


class AvisoGateRechazado(RechazoNegocio):
    """Base de los rechazos propios de GET /gate-avisos (motivo y mensaje los guarda RechazoNegocio)."""


class OportunidadNoEncontrada(AvisoGateRechazado):
    """No existe ninguna oportunidad con ese id (-> 404)."""


class EstadoNoPermiteAviso(AvisoGateRechazado):
    """La oportunidad no tiene un caso de Gate que avisar (-> 409). Motivos: sin_presupuesto y sin_gate."""


# ======================================================================
# La operación completa
# ======================================================================


def obtener_aviso(oportunidad_id: int) -> AvisoGateResponse:
    """
    Devuelve los datos del caso con Gate de esa oportunidad.

    Orden de las comprobaciones (plan, 1.7):
      404 oportunidad_no_encontrada
      409 sin_presupuesto
      409 sin_gate
      200 con el aviso
    No depende del estado: un caso ya decidido sigue devolviendo su aviso,
    con su decisión.
    """
    # get_db_connection(): conexión de solo lectura. Todo lo de dentro del
    # "with" ocurre en una transacción que se deshace al salir.
    with get_db_connection() as conn:
        # El cursor envía las consultas y lee sus resultados.
        cursor = conn.cursor()

        # --------------------------------------------------------------
        # 1. La puerta: ¿existe, tiene presupuesto y tiene Gate?
        # --------------------------------------------------------------
        # SQL: el id de la oportunidad, y el id y requiere_aprobacion de su
        # presupuesto. LEFT JOIN ("une si existe"): si no hay presupuesto,
        # la fila sale igual con p.id NULL; con un JOIN normal desaparecería
        # y parecería que la oportunidad no existe. Esta consulta NO lee
        # ningún dato personal.
        cursor.execute(
            """
            SELECT o.id, p.id, p.requiere_aprobacion
            FROM oportunidades o
            LEFT JOIN presupuestos p ON p.oportunidad_id = o.id
            WHERE o.id = %s;
            """,
            (oportunidad_id,),
        )
        # fetchone(): la única fila, o None si no hay ninguna.
        puerta = cursor.fetchone()
        # Sin fila: la oportunidad no existe (404).
        if puerta is None:
            raise OportunidadNoEncontrada(
                "oportunidad_no_encontrada",
                f"No existe ninguna oportunidad con id {oportunidad_id}.",
            )
        # Desempaquetado: una variable por columna; el "_" es el id de la
        # oportunidad, que ya se conoce.
        _, presupuesto_id, requiere_aprobacion = puerta
        # Sin presupuesto no hay Gate posible (409).
        if presupuesto_id is None:
            raise EstadoNoPermiteAviso(
                "sin_presupuesto",
                "La oportunidad todavía no tiene presupuesto calculado, así que no hay ningún Gate que avisar.",
            )
        # Presupuesto sin Gate: nunca se devuelven sus datos (pre-decisión 4).
        if not requiere_aprobacion:
            raise EstadoNoPermiteAviso(
                "sin_gate",
                "El presupuesto de esta oportunidad no requiere aprobación (no tiene Gate): no hay nada que avisar.",
            )

        # --------------------------------------------------------------
        # 2. El caso: solo se llega aquí si hay Gate
        # --------------------------------------------------------------
        # SQL, por partes:
        #   - Las 17 columnas del caso (contacto de la solicitud, reforma,
        #     fotos, presupuesto y umbral vigente) salen de
        #     columnas_caso_sql(), en datos_oportunidad.py, donde se explica
        #     cada una. Detrás, las 4 de la decisión, que son solo de aquí.
        #   - JOIN normal con leads y presupuestos: siempre existen (claves
        #     foráneas NOT NULL, y la puerta ya comprobó el presupuesto).
        #   - Sin JOIN con clientes: la ficha no se lee nunca (P1).
        #   - LEFT JOIN con umbrales_gate, decisiones_gate y visitas: pueden
        #     no existir (sin decisión, o descarte sin visita); entonces sus
        #     columnas llegan NULL en vez de perderse la fila.
        #   - Como mucho UNA fila: presupuestos y decisiones_gate tienen
        #     UNIQUE (oportunidad_id).
        #   - NO lee el informe de la decisión ni lead_token (D2, D3).
        #   - El texto se monta con "+": las dos partes son textos fijos
        #     del programa; el id va como parámetro %s, nunca pegado.
        cursor.execute(
            "SELECT "
            + columnas_caso_sql()
            + """,
                   d.decision,
                   d.created_at,
                   d.motivo,
                   v.fecha_propuesta
            FROM oportunidades o
            JOIN leads l                ON l.id = o.lead_id
            JOIN presupuestos p         ON p.oportunidad_id = o.id
            LEFT JOIN umbrales_gate u   ON u.tipo_reforma = o.tipo_reforma
            LEFT JOIN decisiones_gate d ON d.oportunidad_id = o.id
            LEFT JOIN visitas v         ON v.id = d.visita_id
            WHERE o.id = %s;
            """,
            (oportunidad_id,),
        )
        # La única fila (la puerta ya comprobó que existe).
        fila = cursor.fetchone()
        # Se cierra el cursor; la conexión la devuelve el "with" al pool.
        cursor.close()

    # A partir de aquí ya no se usa la base de datos: solo se construye la
    # respuesta con lo leído.

    # Las primeras columnas son las del caso: se emparejan con sus nombres
    # (un número distinto de valores es un error, no un desplazamiento).
    n_caso = len(COLUMNAS_CASO)
    caso = caso_desde_fila(fila[:n_caso])
    # Las 4 últimas, las de la decisión, una variable por columna.
    decision, fecha_decision, motivo_descarte, fecha_visita = fila[n_caso:]

    # Decisión (D3): None si no hay fila en decisiones_gate; si la hay, sus
    # datos sin el informe, con las fechas en hora de Madrid.
    if decision is None:
        decision_aviso = None
    else:
        decision_aviso = DecisionAviso(
            decision=decision,
            fecha_decision=a_madrid(fecha_decision),
            motivo=motivo_descarte,
            fecha_visita=a_madrid(fecha_visita),
        )

    # La respuesta completa. Pydantic comprueba cada campo con el esquema
    # (y extra="forbid" impide añadir ninguno que no esté declarado).
    return AvisoGateResponse(
        oportunidad_id=oportunidad_id,
        estado_oportunidad=caso["estado"],
        fecha_solicitud=a_madrid(caso["fecha_solicitud"]),
        # Contacto (P1): el de la solicitud o no_disponible, nunca la ficha
        # de clientes.
        contacto=contacto_desde(caso),
        reforma=reforma_desde(caso),
        # Lista vacía si la columna es NULL (plan, 1.5).
        fotos=fotos_desde(caso),
        # Los 8 campos, con el sin IVA deducido con el IVA de ESTE
        # presupuesto (plan, 2.1); "**" los pasa como argumentos con nombre.
        presupuesto=PresupuestoAviso(**campos_presupuesto(caso)),
        decision=decision_aviso,
    )
