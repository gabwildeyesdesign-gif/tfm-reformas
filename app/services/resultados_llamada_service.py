"""
Lógica de negocio del resultado de las llamadas de administración: POST
/resultados-llamada (plan: docs/Plan_Resultado_Llamadas.txt, secciones
2.2-2.9 con sus notas fechadas; la lógica del Gate es la de
docs/Plan_Endpoint_Gate_Decisions.txt).

Cambio 2026-10-22: alias POST /gate-decisions quitado (bloque 5); versión
anterior en aba39c4. Con él se fueron registrar_decision, el parámetro
solo_gate y todo lo que solo servía al alias (sus mensajes, el 409 sin_gate,
el 409 decision_ya_registrada y el origen de log "gate_decisions").

Historia: en el Bloque B1 (2026-10-11) este archivo se llamaba
gate_decisions_service.py y se movió aquí sin cambiar nada. En el Bloque B2
(el mismo día) se amplía a los tres tipos de llamada:
  gate                 visita_acordada | descartar          (como antes)
  seguimiento          visita_acordada | descartar          (D25.10)
  confirmacion_visita  confirmar_hora | otra_hora | cliente_cancela (D25.11)
El tipo NO lo envía el formulario: lo deduce app/services/tipo_llamada.py.

Todo ocurre en UNA transacción (READ COMMITTED), con la fila de la
oportunidad bloqueada (FOR UPDATE): POST /visits y POST /create-followup-task
bloquean la misma fila, así que las operaciones sobre una oportunidad se
ponen en fila.

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando un
resultado no se puede registrar, lanza una excepción con un "motivo" (código
estable) y un "mensaje"; el adaptador app/api/resultados_llamada.py decide
el código HTTP.
"""

# Json: le dice a psycopg2 que un diccionario de Python va a una columna
# JSONB (logs.detalle).
from psycopg2.extras import Json

# La conexión de escritura del pool: commit al salir del "with" si todo va
# bien, rollback si se lanza una excepción dentro.
from app.db.connection import get_transactional_connection
# Los esquemas de la ruta y las reglas de qué acompaña a cada resultado.
from app.schemas.resultados_llamada import (
    CON_FECHA,
    ResultadoLlamada,
    ResultadoLlamadaCreate,
    ResultadoLlamadaResponse,
    TipoLlamada,
)

# Reglas de fecha y de configuración COMPARTIDAS con POST /visits.
from app.services.reglas_visita import (
    ZONA_MADRID,
    ConfiguracionIncompleta,
    FechaNoValida,
    RechazoNegocio,
    combinar_fecha_hora_madrid,
    comprobar_hueco_libre,
    # D33: el fin de la visita para la respuesta (fecha_fin_visita).
    fin_de_visita,
    leer_reglas_visita,
    registrar_configuracion_incompleta,
    validar_fecha,
)
# La deducción del tipo de llamada (la misma que usa la ficha) y los
# resultados que admite cada tipo.
from app.services.tipo_llamada import deducir_tipo_llamada, resultados_de

# Estados de visita y de oportunidad que ya define POST /visits (se importan,
# no se copian: D3).
from app.services.visits_service import (
    ESTADO_OPORTUNIDAD_CON_VISITA,
    ESTADOS_VISITA_ACTIVA,
    VISITA_CANCELADA,
    VISITA_CONFIRMADA,
    VISITA_SOLICITADA,
)

# ======================================================================
# Constantes
# ======================================================================

# Origen del log de configuración incompleta (P12). (Cambio 2026-10-22: el
# del alias, "gate_decisions", se quitó con él; antes esta se llamaba
# ORIGEN_LOG_NUEVO. Los logs antiguos con "gate_decisions" siguen en la tabla.)
ORIGEN_LOG = "resultados_llamada"

# Estado al que pasa la oportunidad al perderse (descartar, cliente_cancela).
ESTADO_PERDIDA = "perdida"

# Textos fijos de las visitas que confirma administración por teléfono
# (visitas.texto_cliente es NOT NULL). El del Gate es el de siempre (P2 de
# /gate-decisions); los otros dos, P8 de este plan. otra_hora NO copia el
# texto del cliente (se quedaría duplicado; plan, sección 7).
TEXTO_VISITA_ACORDADA = "Visita acordada por teléfono por administración tras la revisión del Gate."
TEXTO_VISITA_SEGUIMIENTO = "Visita acordada por teléfono por administración en la llamada de seguimiento."
TEXTO_OTRA_HORA = ("Visita confirmada por teléfono por administración a otra hora; "
                   "la pedida desde el chat queda cancelada.")

# Registro en logs (L-b, P4): el Gate conserva su acción de siempre; los
# otros dos tipos escriben una nueva. Las dos llevan la clave 'estado' (la
# lee la ficha para el historial).
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_DECISION_REGISTRADA = "gate_decision_registrada"
ACCION_LLAMADA_REGISTRADA = "llamada_registrada"

# Mensajes de la ruta.
MENSAJE_SIN_PRESUPUESTO = "La oportunidad todavía no tiene presupuesto: no hay ninguna llamada que registrar."
# El 503 de las visitas (el mismo texto de siempre).
MENSAJE_CONFIG_VISITAS = ("No se puede registrar la visita por un problema de configuración de las visitas "
                          "(reglas_negocio). Revisa las claves indicadas en 'faltan'.")
# El 503 de la regla de 48 h (como create-followup-task: sin log).
MENSAJE_CONFIG_SEGUIMIENTO = ("No se puede saber si la oportunidad está en el seguimiento: falta o no es válida "
                              "la regla de plazo de reglas_negocio (ver 'faltan'). No se ha escrito nada.")
# Mensaje de cada 409 de un criterio de b) (los mismos códigos que
# create-followup-task).
MENSAJES_CRITERIO = {
    "sin_presupuesto": MENSAJE_SIN_PRESUPUESTO,
    "con_gate": "El presupuesto de esta oportunidad tiene Gate y su estado no es 'pendiente_aprobacion'.",
    "contacto_registrado": "Ya hay un contacto registrado con el cliente: no está en el seguimiento por abrir.",
    "visita_existente": "La oportunidad tiene una visita que no está cancelada: no está en el seguimiento por abrir.",
    "plazo_no_cumplido": "El presupuesto aún no ha superado el plazo de seguimiento: no sale todavía en la lista.",
}


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================
# ResultadoRechazado es la base de los rechazos PROPIOS y hereda de
# RechazoNegocio (reglas_visita.py). FechaNoValida (422) y
# ConfiguracionIncompleta (503) son las compartidas con /visits.


class ResultadoRechazado(RechazoNegocio):
    """Base de los rechazos propios del resultado de una llamada (motivo y mensaje los guarda RechazoNegocio)."""


class OportunidadNoEncontrada(ResultadoRechazado):
    """No existe ninguna oportunidad con ese id (-> 404)."""


class EstadoNoPermiteResultado(ResultadoRechazado):
    """
    La oportunidad no admite ESTE resultado (-> 409). Motivos:
    sin_presupuesto, resultado_ya_registrado, estado_no_permitido,
    con_gate, contacto_registrado, visita_existente, plazo_no_cumplido,
    resultado_no_admitido, visita_cambiada y visita_activa_existente.
    """


# ======================================================================
# La operación completa
# ======================================================================


def registrar_resultado(data: ResultadoLlamadaCreate) -> ResultadoLlamadaResponse:
    """
    Registra el resultado de la llamada, en UNA transacción: o se aplican
    todos los cambios (visitas, resultado, estado y log) o ninguno.

    El rechazo diferido (503 con su log) se lanza DESPUÉS del commit, para
    que el log se conserve (mismo mecanismo que /visits).
    """
    # Desde aquí hasta el final del "with", todo es UNA transacción.
    with get_transactional_connection() as conn:
        # El cursor se cierra SIEMPRE al salir de este bloque.
        with conn.cursor() as cursor:
            # El trabajo; devuelve la respuesta o un rechazo diferido.
            respuesta, rechazo_diferido = registrar_resultado_con_cursor(cursor, data)
    # Fuera del "with": la transacción ya se ha confirmado (con el log del
    # 503 dentro, si lo hubo). El 503 se lanza ahora.
    if rechazo_diferido is not None:
        raise rechazo_diferido
    # Si no, el resultado se escribió y confirmó.
    return respuesta


def registrar_resultado_con_cursor(cursor, data: ResultadoLlamadaCreate):
    """
    Todo el trabajo, con el cursor de quien llama y DENTRO de su
    transacción (no hace commit ni rollback: así las pruebas lo ejecutan con
    ConexionSinCommit y ROLLBACK).

    Devuelve (respuesta, rechazo_diferido): uno de los dos es None.

    Orden (plan 2.8):
      404 -> 409 sin_presupuesto -> ¿hay resultado?
      (igual: 200, o 503 si es con visita y faltan sus reglas (D33, P1);
      distinto: 409) -> tipo (409 estado_no_permitido o el
      criterio de b); 503 de la regla de 48 h) -> 409 resultado_no_admitido
      -> con visita: 503 -> 409 visita_cambiada / 422 misma_hora_pedida ->
      422 de fecha -> 409 visita_activa_existente -> 409 hueco_ocupado (D31)
      -> escritura.
    """
    # El instante pedido (fecha + hora en Madrid), solo si el resultado
    # lleva visita; se calcula sin base de datos.
    inicio = combinar_fecha_hora_madrid(data.fecha, data.hora) if data.resultado in CON_FECHA else None
    # Los textos tal como se guardan (.value saca el texto de un Enum).
    resultado_texto = data.resultado.value
    motivo_texto = data.motivo.value if data.motivo is not None else None

    # ------------------------------------------------------------------
    # 1. Encontrar la oportunidad y BLOQUEARLA (404)
    # ------------------------------------------------------------------
    # SQL: id, estado, presupuesto y Gate de la oportunidad, y now(). FOR
    # UPDATE OF o: bloquea SOLO su fila hasta el final de la transacción
    # (Postgres no permite FOR UPDATE sobre el lado opcional de un LEFT
    # JOIN). Ningún importe.
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
    # La única fila, o None si no existe: 404.
    fila = cursor.fetchone()
    if fila is None:
        raise OportunidadNoEncontrada(
            "oportunidad_no_encontrada",
            f"No existe ninguna oportunidad con id {data.oportunidad_id}.",
        )
    # Una variable por columna del SELECT.
    oportunidad_id, estado, presupuesto_id, requiere_aprobacion, ahora = fila

    # ------------------------------------------------------------------
    # 2. Presupuesto (409)
    # ------------------------------------------------------------------
    # Sin presupuesto no hay ninguna llamada que registrar. (No hay sin_gate:
    # un seguimiento no tiene Gate; era solo del alias, quitado el 2026-10-22.)
    if presupuesto_id is None:
        raise EstadoNoPermiteResultado("sin_presupuesto", MENSAJE_SIN_PRESUPUESTO)

    # ------------------------------------------------------------------
    # 3. ¿Ya hay un resultado registrado? (200 o 409)
    # ------------------------------------------------------------------
    # Va ANTES que el tipo: tras el 201 el estado ya cambió, y un reintento
    # legítimo de n8n daría 409 en vez de 200.
    previa = _repeticion_o_rechazo(cursor, oportunidad_id, estado, data, inicio)
    # Repetición exacta: lo que ya estaba, sin escribir nada; desde D33 es ya
    # el par (respuesta, rechazo_diferido), por el 503 de P1.
    if previa is not None:
        return previa

    # ------------------------------------------------------------------
    # 4. El tipo de llamada (409 o 503)
    # ------------------------------------------------------------------
    # La deducción compartida con la ficha.
    deducido = deducir_tipo_llamada(cursor, oportunidad_id, estado, ahora)
    # La regla de 48 h está rota: 503, sin log (como create-followup-task).
    if deducido.motivo_no == "configuracion_incompleta":
        raise ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIG_SEGUIMIENTO, deducido.faltan)
    # No corresponde ninguna llamada: 409 con su motivo.
    if deducido.tipo is None:
        raise EstadoNoPermiteResultado(
            deducido.motivo_no,
            MENSAJES_CRITERIO.get(deducido.motivo_no,
                                  f"La oportunidad está en estado '{estado}': no tiene ninguna llamada pendiente."),
        )
    # El tipo y, en una confirmación, la visita pedida y su fecha.
    tipo, solicitada_id, fecha_pedida = deducido.tipo, deducido.visita_id, deducido.fecha_pedida

    # ------------------------------------------------------------------
    # 5. ¿El resultado es de ESE tipo? (409)
    # ------------------------------------------------------------------
    # Un formulario desfasado (por ejemplo, el cliente pidió visita entre la
    # lista y la llamada) da un 409 que dice el tipo actual.
    if data.resultado not in resultados_de(tipo):
        raise EstadoNoPermiteResultado(
            "resultado_no_admitido",
            f"La llamada de esta oportunidad es de tipo '{tipo.value}' y admite "
            f"{', '.join(r.value for r in resultados_de(tipo))}, no '{resultado_texto}'. "
            "Vuelve a abrir la ficha: su situación ha cambiado.",
        )

    # ------------------------------------------------------------------
    # 6. Lo que necesita una visita (503, 409, 422)
    # ------------------------------------------------------------------
    # D33: sin visita no se leen las reglas (y no hay fin que calcular).
    reglas = None
    if data.resultado in CON_FECHA:
        # Reglas de visita, las MISMAS de /visits (503 con log si faltan).
        reglas, problemas = leer_reglas_visita(cursor)
        # Con problemas: rastro para administración y 503 DIFERIDO (el log
        # se confirma con el commit de quien llama).
        if problemas:
            registrar_configuracion_incompleta(cursor, oportunidad_id, problemas, ORIGEN_LOG)
            return None, ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIG_VISITAS, problemas)
        # confirmar_hora: tiene que ser la hora que administración leyó al
        # cliente (C11): si el cliente la cambió desde el chat, 409.
        if data.resultado == ResultadoLlamada.CONFIRMAR_HORA and inicio != fecha_pedida:
            raise EstadoNoPermiteResultado(
                "visita_cambiada",
                f"La visita pedida es el {fecha_pedida.astimezone(ZONA_MADRID):%d/%m/%Y a las %H:%M}, no la hora "
                "enviada: el cliente puede haberla cambiado. Vuelve a abrir la ficha.",
            )
        # otra_hora: tiene que ser OTRA (si es la misma, era confirmar_hora).
        if data.resultado == ResultadoLlamada.OTRA_HORA and inicio == fecha_pedida:
            raise FechaNoValida(
                "misma_hora_pedida",
                "La hora enviada es la misma que pidió el cliente: el resultado es confirmar_hora, no otra_hora.",
            )
        # Las reglas de la fecha (futura, laborable, dentro de una franja),
        # también para la hora pedida (C12: puede haber pasado). 422.
        validar_fecha(inicio, ahora, reglas)
        # visita_acordada: ninguna visita activa (P1 de /gate-decisions).
        if data.resultado == ResultadoLlamada.VISITA_ACORDADA:
            # SQL: una visita activa ('solicitada' o 'confirmada') de la oportunidad.
            cursor.execute(
                "SELECT id FROM visitas WHERE oportunidad_id = %s AND estado = ANY(%s);",
                (oportunidad_id, list(ESTADOS_VISITA_ACTIVA)),
            )
            # Si la hay, 409 (sin esto, el índice parcial daría un 500).
            activa = cursor.fetchone()
            if activa is not None:
                raise EstadoNoPermiteResultado(
                    "visita_activa_existente",
                    f"La oportunidad ya tiene una visita activa (id {activa[0]}); no se puede acordar otra.",
                )
        # D31: el hueco tiene que estar libre (409 hueco_ocupado), la última
        # comprobación antes de escribir, con la MISMA función que /visits
        # (P16). La visita pedida de la propia
        # oportunidad no cuenta: confirmar_hora la confirma y otra_hora la
        # cancela en esta misma transacción (C8). Bloquea el día (P15).
        comprobar_hueco_libre(cursor, inicio, ahora, reglas, [solicitada_id] if solicitada_id is not None else [])

    # ------------------------------------------------------------------
    # 7. Escritura
    # ------------------------------------------------------------------
    # D33: con las reglas leídas en el paso 6 (o None), para el fin de la visita.
    return _escribir(cursor, data, tipo, oportunidad_id, estado, inicio, solicitada_id, fecha_pedida, reglas), None


def _repeticion_o_rechazo(cursor, oportunidad_id, estado, data, inicio):
    """
    Si ya hay un resultado: la respuesta de la repetición (si es la MISMA
    petición) o un 409 (si es otra). Si no hay ninguno, None.

    D33 (2026-10-14): la repetición devuelve el par (respuesta,
    rechazo_diferido). Con visita, su fecha_fin_visita necesita las reglas,
    que en este camino todavía no se han leído: se leen aquí (UNA consulta,
    solo en este caso) y, si faltan, 503 con su log, como el 201 y como la
    repetición de /visits (P1 del plan D33).
    """
    # SQL: el resultado previo (como mucho uno: UNIQUE (oportunidad_id)) y la
    # fecha de su visita confirmada (LEFT JOIN: puede no tener).
    cursor.execute(
        """
        SELECT d.id, d.tipo_llamada, d.decision, d.motivo, d.visita_id, v.fecha_propuesta, d.visita_cancelada_id
        FROM decisiones_gate d
        LEFT JOIN visitas v ON v.id = d.visita_id
        WHERE d.oportunidad_id = %s;
        """,
        (oportunidad_id,),
    )
    # La fila, o None si todavía no hay ninguna.
    previa = cursor.fetchone()
    if previa is None:
        return None
    # Una variable por columna.
    previa_id, previa_tipo, previa_resultado, previa_motivo, previa_visita, previa_fecha, previa_cancelada = previa
    # ¿La MISMA petición? Mismo resultado y, con visita, la misma fecha y
    # hora (dos datetime con zona se comparan por el instante); sin visita,
    # el mismo motivo.
    if previa_resultado == data.resultado.value:
        # Con visita: la misma fecha y hora.
        if data.resultado in CON_FECHA:
            es_repeticion = previa_fecha == inicio
        # Sin visita: el mismo motivo.
        else:
            es_repeticion = previa_motivo == (data.motivo.value if data.motivo is not None else None)
    # Otro resultado: nunca es repetición.
    else:
        es_repeticion = False
    # Repetición: lo que ya estaba, sin escribir nada (el informe nuevo se
    # ignora y se conserva el original).
    if es_repeticion:
        # D33: sin visita, no hay fin (ni lectura de reglas).
        fin = None
        # Con visita: las reglas de visita, para su fin.
        if previa_fecha is not None:
            reglas, problemas = leer_reglas_visita(cursor)
            # Rotas: el rastro para administración y el 503 DIFERIDO (el log
            # se confirma con el commit de quien llama), como en el paso 6.
            if problemas:
                registrar_configuracion_incompleta(cursor, oportunidad_id, problemas, ORIGEN_LOG)
                # Sin respuesta, y el 503 como rechazo diferido.
                return None, ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIG_VISITAS, problemas)
            # Bien: el fin, con la función compartida.
            fin = fin_de_visita(previa_fecha, reglas)
        return ResultadoLlamadaResponse(
            llamada_id=previa_id,
            oportunidad_id=oportunidad_id,
            tipo_llamada=previa_tipo,
            resultado=previa_resultado,
            motivo=previa_motivo,
            estado_oportunidad=estado,
            visita_id=previa_visita,
            # En hora de Madrid, solo si hay fecha.
            fecha_visita=previa_fecha.astimezone(ZONA_MADRID) if previa_fecha is not None else None,
            # D33: su fin (None sin visita).
            fecha_fin_visita=fin,
            visita_cancelada_id=previa_cancelada,
            creado=False,
        ), None
    # Otro resultado: definitivo (R2).
    raise EstadoNoPermiteResultado(
        "resultado_ya_registrado",
        f"Esta oportunidad ya tiene registrado otro resultado ({previa_resultado}, id {previa_id}). "
        "El resultado es definitivo: si fue un error, hay que corregirlo a mano en la base de datos.",
    )


# D33: recibe además las reglas de visita (para fecha_fin_visita).
def _escribir(cursor, data, tipo, oportunidad_id, estado, inicio, solicitada_id, fecha_pedida, reglas):
    """
    Las escrituras de un resultado nuevo (plan 2.4), con segunda barrera en
    cada UPDATE (rowcount = 1). Devuelve la respuesta (creado=True).

    reglas (D33): las de visita que leyó el paso 6, para fecha_fin_visita;
    None en descartar y cliente_cancela (no hay visita).
    """
    # Lo que se escribe, según el resultado (todo empieza vacío).
    visita_id, fecha_visita, cancelada_id = None, None, None
    r = data.resultado

    # visita_acordada: una visita nueva, ya confirmada, con el texto fijo de
    # su tipo; la oportunidad pasa a 'visita_agendada'.
    if r == ResultadoLlamada.VISITA_ACORDADA:
        texto = TEXTO_VISITA_ACORDADA if tipo == TipoLlamada.GATE else TEXTO_VISITA_SEGUIMIENTO
        visita_id, fecha_visita = _insertar_visita_confirmada(cursor, oportunidad_id, inicio, texto)
        nuevo_estado = ESTADO_OPORTUNIDAD_CON_VISITA
    # confirmar_hora: la visita pedida pasa a 'confirmada'.
    elif r == ResultadoLlamada.CONFIRMAR_HORA:
        _cambiar_solicitada(cursor, solicitada_id, VISITA_CONFIRMADA)
        visita_id, fecha_visita = solicitada_id, fecha_pedida.astimezone(ZONA_MADRID)
        nuevo_estado = ESTADO_OPORTUNIDAD_CON_VISITA
    # otra_hora: la pedida se cancela PRIMERO (índice parcial de una visita
    # activa) y después se crea la confirmada.
    elif r == ResultadoLlamada.OTRA_HORA:
        _cambiar_solicitada(cursor, solicitada_id, VISITA_CANCELADA)
        # La nueva, confirmada a la hora acordada.
        visita_id, fecha_visita = _insertar_visita_confirmada(cursor, oportunidad_id, inicio, TEXTO_OTRA_HORA)
        # Y la pedida, como cancelada en la fila del resultado.
        cancelada_id = solicitada_id
        nuevo_estado = ESTADO_OPORTUNIDAD_CON_VISITA
    # cliente_cancela: la pedida se cancela y la oportunidad se pierde (D25.11).
    elif r == ResultadoLlamada.CLIENTE_CANCELA:
        _cambiar_solicitada(cursor, solicitada_id, VISITA_CANCELADA)
        cancelada_id = solicitada_id
        nuevo_estado = ESTADO_PERDIDA
    # descartar: sin visita; la oportunidad se pierde.
    else:
        nuevo_estado = ESTADO_PERDIDA

    # SQL: la fila del resultado, con su tipo y sus dos visitas (las claves
    # foráneas dobles garantizan que son de ESTA oportunidad). El informe ya
    # llega recortado del esquema, como exige el CHECK.
    cursor.execute(
        """
        INSERT INTO decisiones_gate
            (oportunidad_id, visita_id, visita_cancelada_id, tipo_llamada, decision, motivo, informe)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (oportunidad_id, visita_id, cancelada_id, tipo.value, r.value,
         data.motivo.value if data.motivo is not None else None, data.informe),
    )
    # fetchone() da (id,); [0] saca el valor.
    llamada_id = cursor.fetchone()[0]

    # SQL: la oportunidad cambia de estado; fecha_ultimo_contacto = now() en
    # todo resultado (pre-decisión 7). Segunda barrera: solo si SIGUE en el
    # estado leído (con el FOR UPDATE tiene que ser siempre así).
    cursor.execute(
        """
        UPDATE oportunidades
        SET estado = %s, fecha_ultimo_contacto = now(), updated_at = now()
        WHERE id = %s AND estado = %s;
        """,
        (nuevo_estado, oportunidad_id, estado),
    )
    # Si no cambió exactamente una fila, algo se saltó el bloqueo: 500 y
    # rollback, en vez de dejar un resultado de algo que no ocurrió.
    if cursor.rowcount != 1:
        raise RuntimeError(f"Segunda barrera: el UPDATE de la oportunidad {oportunidad_id} cambió "
                           f"{cursor.rowcount} filas (se esperaba 1).")

    # El log (L-b). El informe NO se copia (P9 de /gate-decisions). La fecha
    # va como texto ISO (JSON no tiene tipo fecha).
    fecha_iso = fecha_visita.isoformat() if fecha_visita is not None else None
    if tipo == TipoLlamada.GATE:
        # Gate: su acción de siempre y sus claves de siempre, más dos nuevas.
        accion = ACCION_DECISION_REGISTRADA
        detalle = {"decision_id": llamada_id, "decision": r.value, "motivo": data.motivo.value if data.motivo else None,
                   "visita_id": visita_id, "fecha_propuesta": fecha_iso,
                   "tipo_llamada": tipo.value, "estado": nuevo_estado}
    else:
        # Seguimiento y confirmación: la acción nueva, con 'estado' (la ficha).
        accion = ACCION_LLAMADA_REGISTRADA
        detalle = {"llamada_id": llamada_id, "tipo_llamada": tipo.value, "resultado": r.value,
                   "motivo": data.motivo.value if data.motivo else None, "visita_id": visita_id,
                   "visita_cancelada_id": cancelada_id, "fecha_visita": fecha_iso, "estado": nuevo_estado}
    # SQL: UNA fila en logs, asociada a la oportunidad, con el detalle JSONB.
    cursor.execute(
        "INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES (%s, %s, %s, %s);",
        (LOG_ENTITY_TYPE_OPORTUNIDAD, oportunidad_id, accion, Json(detalle)),
    )

    # La respuesta de un resultado NUEVO (creado=True -> 201).
    return ResultadoLlamadaResponse(
        llamada_id=llamada_id,
        oportunidad_id=oportunidad_id,
        tipo_llamada=tipo,
        resultado=r,
        motivo=data.motivo,
        estado_oportunidad=nuevo_estado,
        visita_id=visita_id,
        fecha_visita=fecha_visita,
        # D33: su fin con las reglas del paso 6; None si no queda visita.
        fecha_fin_visita=fin_de_visita(fecha_visita, reglas) if fecha_visita is not None else None,
        visita_cancelada_id=cancelada_id,
        creado=True,
    )


def _insertar_visita_confirmada(cursor, oportunidad_id, inicio, texto):
    """Crea una visita 'confirmada' en ese instante. Devuelve (id, fecha en Madrid)."""
    # SQL: la visita nueva; psycopg2 envía el instante con su desfase y
    # RETURNING devuelve el id y la fecha guardada.
    cursor.execute(
        """
        INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente)
        VALUES (%s, %s, %s, %s)
        RETURNING id, fecha_propuesta;
        """,
        (oportunidad_id, inicio, VISITA_CONFIRMADA, texto),
    )
    # El id y la fecha, que Postgres devuelve en UTC: en hora de Madrid.
    visita_id, guardada = cursor.fetchone()
    return visita_id, guardada.astimezone(ZONA_MADRID)


def _cambiar_solicitada(cursor, visita_id, estado_nuevo):
    """Pasa la visita 'solicitada' a estado_nuevo, con segunda barrera."""
    # SQL: solo si SIGUE en 'solicitada' (con el bloqueo de la oportunidad,
    # siempre; si no, algo se lo ha saltado).
    cursor.execute(
        "UPDATE visitas SET estado = %s, updated_at = now() WHERE id = %s AND estado = %s;",
        (estado_nuevo, visita_id, VISITA_SOLICITADA),
    )
    # Exactamente una fila, o se para todo (500 y rollback).
    if cursor.rowcount != 1:
        raise RuntimeError(f"Segunda barrera: el UPDATE de la visita {visita_id} cambió "
                           f"{cursor.rowcount} filas (se esperaba 1).")
