"""
Lógica de negocio de POST /cambios-visita: administración cambia o cancela,
por teléfono, una visita YA confirmada (D46; plan
docs/Plan_Cambio_Visita_D46.txt, secciones 3, 4 y 6, con el veredicto del
tutor al PASO 0).

  cambiar   la confirmada pasa a 'cancelada' y se crea otra 'confirmada' a la
            hora nueva (sin límite de veces, D46.2). La oportunidad sigue en
            'visita_agendada'.
  cancelar  la confirmada pasa a 'cancelada' y la oportunidad a 'perdida'
            (D46.3).
Cada operación deja una fila en cambios_visita (paso18) con su informe, y un
log SIN el informe.

Todo ocurre en UNA transacción (READ COMMITTED), con la fila de la
oportunidad bloqueada (FOR UPDATE): POST /visits, POST /resultados-llamada y
POST /create-followup-task bloquean la misma fila, así que las operaciones
sobre una oportunidad se ponen en fila (plan, sección 4).

La condición "la visita se puede cambiar o cancelar" vive en UNA función pura
de este archivo, motivo_no_modificable(), que usan este servicio y la ficha
(precisión 3 del tutor): así los dos no se pueden contradecir.

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando un
cambio no se puede hacer, lanza una excepción con un "motivo" (código
estable) y un "mensaje"; el adaptador app/api/cambios_visita.py decide el
código HTTP.
"""

# datetime: para anotar el tipo de las fechas de motivo_no_modificable.
from datetime import datetime

# Json: le dice a psycopg2 que un diccionario de Python va a una columna
# JSONB (logs.detalle).
from psycopg2.extras import Json

# La conexión de escritura del pool: commit al salir del "with" si todo va
# bien, rollback si se lanza una excepción dentro.
from app.db.connection import get_transactional_connection
# Los esquemas de la ruta.
from app.schemas.cambios_visita import AccionCambioVisita, CambioVisitaCreate, CambioVisitaResponse

# Reglas de fecha, de configuración y del hueco libre, COMPARTIDAS con POST
# /visits y POST /resultados-llamada.
from app.services.reglas_visita import (
    # D45: la hora nueva lleva la antelación de administración (P4).
    CANAL_ADMINISTRACION,
    ZONA_MADRID,
    ConfiguracionIncompleta,
    FechaNoValida,
    RechazoNegocio,
    combinar_fecha_hora_madrid,
    comprobar_hueco_libre,
    # D33: el fin de la visita para la respuesta (fecha_fin_visita).
    fin_de_visita,
    leer_reglas_visita,
    primer_dia_del_canal,
    registrar_configuracion_incompleta,
    validar_fecha,
)
# El estado de una oportunidad perdida y el mensaje del 503 de las visitas:
# los de POST /resultados-llamada (se importan, no se copian).
from app.services.resultados_llamada_service import ESTADO_PERDIDA, MENSAJE_CONFIG_VISITAS
# Estados de visita y de oportunidad que ya define POST /visits.
from app.services.visits_service import (
    ESTADO_OPORTUNIDAD_CON_VISITA,
    ESTADOS_VISITA_ACTIVA,
    VISITA_CANCELADA,
    VISITA_CONFIRMADA,
)

# ======================================================================
# Constantes
# ======================================================================

# Origen del log de configuración incompleta (503 al cambiar, plan 3, paso 9).
ORIGEN_LOG = "cambios_visita"

# Texto fijo de la visita nueva (visitas.texto_cliente es NOT NULL; P6 del
# veredicto del tutor). No copia el de la vieja: como en otra_hora.
TEXTO_VISITA_CAMBIADA = "Visita cambiada por teléfono por administración."

# Registro en logs (plan, sección 6). Los dos llevan la clave 'estado' (la
# lee la ficha). Solo el de la cancelación entra en el historial de estados
# (P9): cambiar no cambia el estado.
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_VISITA_CAMBIADA = "visita_confirmada_cambiada"
ACCION_VISITA_CANCELADA = "visita_confirmada_cancelada"

# Los motivos de motivo_no_modificable() (y de su 409).
SIN_VISITA_CONFIRMADA = "sin_visita_confirmada"
VISITA_PASADA = "visita_pasada"

# Mensajes de los 409 (para administración).
MENSAJE_SIN_VISITA = ("Esta oportunidad no tiene ninguna visita confirmada que se pueda cambiar o cancelar. "
                      "Vuelve a abrir la ficha.")
MENSAJE_PASADA = "La visita ya ha empezado o ha pasado: no se puede cambiar ni cancelar."
MENSAJE_YA_CANCELADA = ("La visita de esta oportunidad ya se canceló (la oportunidad está perdida): "
                        "no se puede cambiar ni volver a cancelar.")


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================
# CambioRechazado es la base de los rechazos PROPIOS y hereda de
# RechazoNegocio (reglas_visita.py). FechaNoValida (422), HuecoOcupado (409)
# y ConfiguracionIncompleta (503) son las compartidas con /visits.


class CambioRechazado(RechazoNegocio):
    """Base de los rechazos propios de un cambio de visita (motivo y mensaje los guarda RechazoNegocio)."""


class OportunidadNoEncontrada(CambioRechazado):
    """No existe ninguna oportunidad con ese id (-> 404)."""


class VisitaNoModificable(CambioRechazado):
    """
    La visita no se puede cambiar ni cancelar (-> 409). Motivos:
    visita_ya_cancelada, sin_visita_confirmada, visita_cambiada y
    visita_pasada.
    """


# ======================================================================
# La condición compartida con la ficha (función pura, precisión 3)
# ======================================================================


def motivo_no_modificable(estado_oportunidad: str, estado_visita: str | None, fecha_visita: datetime | None,
                          ahora: datetime) -> str | None:
    """
    None si la visita SE PUEDE cambiar o cancelar; si no, el motivo:
      sin_visita_confirmada  la oportunidad no está en 'visita_agendada', o
                             no tiene visita activa, o la que tiene es
                             'solicitada' (esa la confirma POST
                             /resultados-llamada, no esta ruta).
      visita_pasada          la confirmada ya ha empezado o ha pasado: su
                             inicio es <= ahora (D46.5; R5: una visita en
                             curso ya es pasada).

    estado_oportunidad: oportunidades.estado.
    estado_visita, fecha_visita: los de su visita ACTIVA ('solicitada' o
        'confirmada', como mucho una), o None si no tiene.
    ahora: el now() de Postgres (la referencia de tiempo es el reloj de la
        base de datos, no el del ordenador; precisión 2).

    Pura (sin base de datos): la usan este servicio (pasos 5 y 7) y la ficha
    (visita_modificable), y se prueba con fechas fijas.
    """
    # Sin oportunidad con visita, sin visita o con una que no es confirmada.
    if estado_oportunidad != ESTADO_OPORTUNIDAD_CON_VISITA or estado_visita != VISITA_CONFIRMADA or fecha_visita is None:
        # No hay nada que cambiar.
        return SIN_VISITA_CONFIRMADA
    # Ya empezó o pasó: dos datetime con zona se comparan por el instante.
    if fecha_visita <= ahora:
        # Demasiado tarde.
        return VISITA_PASADA
    # Futura y confirmada: se puede.
    return None


# ======================================================================
# La operación completa
# ======================================================================


def registrar_cambio(data: CambioVisitaCreate) -> CambioVisitaResponse:
    """
    Cambia o cancela la visita, en UNA transacción: o se aplican todos los
    cambios (visitas, cambios_visita, oportunidad y log) o ninguno.

    El rechazo diferido (503 con su log) se lanza DESPUÉS del commit, para
    que el log se conserve (mismo mecanismo que /visits y /resultados-llamada).
    """
    # Desde aquí hasta el final del "with", todo es UNA transacción.
    with get_transactional_connection() as conn:
        # El cursor se cierra SIEMPRE al salir de este bloque.
        with conn.cursor() as cursor:
            # El trabajo; devuelve la respuesta o un rechazo diferido.
            respuesta, rechazo_diferido = registrar_cambio_con_cursor(cursor, data)
    # Fuera del "with": la transacción ya se ha confirmado (con el log del
    # 503 dentro, si lo hubo). El 503 se lanza ahora.
    if rechazo_diferido is not None:
        raise rechazo_diferido
    # Si no, el cambio se escribió y confirmó.
    return respuesta


def registrar_cambio_con_cursor(cursor, data: CambioVisitaCreate):
    """
    Todo el trabajo, con el cursor de quien llama y DENTRO de su transacción
    (no hace commit ni rollback: así las pruebas lo ejecutan con
    ConexionSinCommit y ROLLBACK).

    Devuelve (respuesta, rechazo_diferido): uno de los dos es None.

    Orden (plan, sección 3):
      404 -> repetición (200, o 503 si faltan las reglas para el fin) ->
      409 visita_ya_cancelada -> 409 sin_visita_confirmada -> 409
      visita_cambiada -> 409 visita_pasada -> cancelar: escritura; cambiar:
      503 -> 422 misma_hora_actual -> 422 de fecha (antelación de
      administración) -> 409 hueco_ocupado -> escritura.
    """
    # Los dos instantes (fecha + hora en Madrid), sin base de datos: la
    # visita que ve el operador y, al cambiar, la nueva.
    actual = combinar_fecha_hora_madrid(data.fecha_actual, data.hora_actual)
    inicio = combinar_fecha_hora_madrid(data.fecha, data.hora) if data.accion == AccionCambioVisita.CAMBIAR else None

    # ------------------------------------------------------------------
    # 1. Encontrar la oportunidad y BLOQUEARLA (404)
    # ------------------------------------------------------------------
    # SQL: id y estado de la oportunidad, y now(). FOR UPDATE: bloquea su
    # fila hasta el final de la transacción. Ningún importe.
    cursor.execute("SELECT o.id, o.estado, now() FROM oportunidades o WHERE o.id = %s FOR UPDATE;",
                   (data.oportunidad_id,))
    # La única fila, o None si no existe: 404.
    fila = cursor.fetchone()
    if fila is None:
        raise OportunidadNoEncontrada(
            "oportunidad_no_encontrada",
            f"No existe ninguna oportunidad con id {data.oportunidad_id}.",
        )
    # Una variable por columna del SELECT.
    oportunidad_id, estado, ahora = fila

    # ------------------------------------------------------------------
    # 2. La visita activa y el último cambio
    # ------------------------------------------------------------------
    # SQL: la visita activa ('solicitada' o 'confirmada'): como mucho una,
    # por el índice parcial visitas_una_activa_por_oportunidad.
    cursor.execute(
        "SELECT id, estado, fecha_propuesta FROM visitas WHERE oportunidad_id = %s AND estado = ANY(%s);",
        (oportunidad_id, list(ESTADOS_VISITA_ACTIVA)),
    )
    # La fila, o None si no tiene ninguna activa.
    activa = cursor.fetchone()
    # Una variable por columna (las tres None sin visita activa).
    activa_id, activa_estado, activa_fecha = activa if activa is not None else (None, None, None)
    # SQL: el último cambio de la oportunidad, con la fecha de su visita
    # anterior (siempre existe) y la de su visita nueva y su estado (LEFT
    # JOIN: al cancelar no hay). ORDER BY id DESC LIMIT 1: el más reciente.
    cursor.execute(
        """
        SELECT c.id, c.accion, c.motivo, c.visita_anterior_id, va.fecha_propuesta,
               c.visita_nueva_id, vn.fecha_propuesta, vn.estado
        FROM cambios_visita c
        JOIN visitas va      ON va.id = c.visita_anterior_id
        LEFT JOIN visitas vn ON vn.id = c.visita_nueva_id
        WHERE c.oportunidad_id = %s
        ORDER BY c.id DESC
        LIMIT 1;
        """,
        (oportunidad_id,),
    )
    # La fila, o None si nunca se ha cambiado nada.
    ultimo = cursor.fetchone()

    # ------------------------------------------------------------------
    # 3. Repetición exacta (200, D7)
    # ------------------------------------------------------------------
    # Va ANTES que el estado (como en 4b): tras el 201 de una cancelación la
    # oportunidad ya está 'perdida', y un reintento legítimo de n8n daría 409.
    previa = _repeticion(cursor, data, oportunidad_id, estado, actual, inicio, ultimo)
    # Repetición: lo que ya estaba (o el 503 diferido), sin escribir nada más.
    if previa is not None:
        return previa

    # ------------------------------------------------------------------
    # 4-7. ¿Se puede tocar la visita? (409)
    # ------------------------------------------------------------------
    # 4. La última fila es una cancelación (con otro motivo, otra fecha u
    # otra acción): la visita ya no existe y la oportunidad está perdida.
    if ultimo is not None and ultimo[1] == AccionCambioVisita.CANCELAR.value:
        raise VisitaNoModificable("visita_ya_cancelada", MENSAJE_YA_CANCELADA)
    # La condición compartida con la ficha (precisión 3).
    no_modificable = motivo_no_modificable(estado, activa_estado, activa_fecha, ahora)
    # 5. Sin visita confirmada.
    if no_modificable == SIN_VISITA_CONFIRMADA:
        raise VisitaNoModificable(SIN_VISITA_CONFIRMADA, MENSAJE_SIN_VISITA)
    # 6. La pantalla está desfasada: la confirmada no es la que vio el
    # operador (otro la cambió). Dos datetime con zona: el instante.
    if activa_fecha != actual:
        raise VisitaNoModificable(
            "visita_cambiada",
            f"La visita confirmada es el {activa_fecha.astimezone(ZONA_MADRID):%d/%m/%Y a las %H:%M}, no la enviada "
            "como actual: puede haberla cambiado otra persona. Vuelve a abrir la ficha.",
        )
    # 7. Ya empezó o pasó (D46.5).
    if no_modificable == VISITA_PASADA:
        raise VisitaNoModificable(VISITA_PASADA, MENSAJE_PASADA)

    # ------------------------------------------------------------------
    # 8. Cancelar: sin reglas ni hueco (liberar un hueco no choca con nadie)
    # ------------------------------------------------------------------
    # P4: cancelar solo exige que la visita sea futura (ya comprobado).
    if data.accion == AccionCambioVisita.CANCELAR:
        return _cancelar(cursor, data, oportunidad_id, estado, activa_id), None

    # ------------------------------------------------------------------
    # 9. Cambiar: reglas (503), misma hora (422), fecha (422), hueco (409)
    # ------------------------------------------------------------------
    # Reglas de visita, las MISMAS de /visits (503 con log si faltan).
    reglas, problemas = leer_reglas_visita(cursor)
    # Con problemas: rastro para administración y 503 DIFERIDO (el log se
    # confirma con el commit de quien llama).
    if problemas:
        registrar_configuracion_incompleta(cursor, oportunidad_id, problemas, ORIGEN_LOG)
        return None, ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIG_VISITAS, problemas)
    # La nueva tiene que ser OTRA hora (si es la misma, no hay nada que cambiar).
    if inicio == actual:
        raise FechaNoValida(
            "misma_hora_actual",
            "La fecha y hora nuevas son las mismas que las de la visita confirmada: no hay nada que cambiar.",
        )
    # Las reglas de la fecha (futura, laborable, franja, rejilla), con la
    # antelación de ADMINISTRACIÓN para la hora nueva (D45; P4).
    validar_fecha(inicio, ahora, reglas, primer_dia_del_canal(ahora, reglas, CANAL_ADMINISTRACION))
    # D31: el hueco tiene que estar libre, la última comprobación antes de
    # escribir, con la MISMA función que /visits. La propia visita NO cuenta:
    # se cancela en esta misma transacción (plan, sección 4). Bloquea el día
    # NUEVO.
    comprobar_hueco_libre(cursor, inicio, ahora, reglas, [activa_id])
    # La escritura, con las reglas para el fin de la visita nueva.
    return _cambiar(cursor, data, oportunidad_id, estado, activa_id, activa_fecha, inicio, reglas), None


def _repeticion(cursor, data, oportunidad_id, estado, actual, inicio, ultimo):
    """
    Si la petición es la MISMA que el último cambio registrado: la respuesta
    de la repetición (creado=False) o el 503 diferido; si no, None (plan,
    sección 3, "Repetición"). El informe nuevo se ignora y se conserva el
    original.

      cambiar   el último es 'cambiar', su visita nueva SIGUE confirmada y
                es la fecha nueva pedida, y su visita anterior es la fecha
                actual enviada.
      cancelar  el último es 'cancelar', con el mismo motivo, y su visita
                anterior es la fecha actual enviada.
    """
    # Nunca se ha cambiado nada: no puede ser una repetición.
    if ultimo is None:
        return None
    # Una variable por columna.
    cambio_id, accion, motivo, anterior_id, anterior_fecha, nueva_id, nueva_fecha, nueva_estado = ultimo
    # Otra acción: no es repetición.
    if accion != data.accion.value:
        return None
    # La visita anterior tiene que ser la que se envía como actual.
    if anterior_fecha != actual:
        return None
    # Cancelar: el mismo motivo.
    if data.accion == AccionCambioVisita.CANCELAR:
        # Otro motivo: no es repetición (y el paso 4 dará visita_ya_cancelada).
        if motivo != data.motivo.value:
            return None
        # La misma cancelación: lo que ya estaba.
        return CambioVisitaResponse(
            cambio_id=cambio_id,
            oportunidad_id=oportunidad_id,
            accion=accion,
            motivo=motivo,
            estado_oportunidad=estado,
            visita_id=None,
            fecha_visita=None,
            fecha_fin_visita=None,
            visita_cancelada_id=anterior_id,
            creado=False,
        ), None
    # Cambiar: la nueva sigue confirmada y es la hora pedida.
    if nueva_estado != VISITA_CONFIRMADA or nueva_fecha != inicio:
        return None
    # D33: su fin necesita las reglas, que en este camino no se han leído: se
    # leen aquí y, si faltan, 503 con su log (como la repetición de 4b).
    reglas, problemas = leer_reglas_visita(cursor)
    # Rotas: el rastro para administración y el 503 DIFERIDO.
    if problemas:
        registrar_configuracion_incompleta(cursor, oportunidad_id, problemas, ORIGEN_LOG)
        # Sin respuesta, y el 503 como rechazo diferido.
        return None, ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIG_VISITAS, problemas)
    # El mismo cambio: lo que ya estaba, en hora de Madrid.
    fecha_nueva = nueva_fecha.astimezone(ZONA_MADRID)
    return CambioVisitaResponse(
        cambio_id=cambio_id,
        oportunidad_id=oportunidad_id,
        accion=accion,
        motivo=None,
        estado_oportunidad=estado,
        visita_id=nueva_id,
        fecha_visita=fecha_nueva,
        fecha_fin_visita=fin_de_visita(fecha_nueva, reglas),
        visita_cancelada_id=anterior_id,
        creado=False,
    ), None


def _cancelar_confirmada(cursor, visita_id):
    """Pasa la visita 'confirmada' a 'cancelada', con segunda barrera."""
    # SQL: solo si SIGUE 'confirmada' (con el bloqueo de la oportunidad,
    # siempre; si no, algo se lo ha saltado).
    cursor.execute(
        "UPDATE visitas SET estado = %s, updated_at = now() WHERE id = %s AND estado = %s;",
        (VISITA_CANCELADA, visita_id, VISITA_CONFIRMADA),
    )
    # Exactamente una fila, o se para todo (500 y rollback).
    if cursor.rowcount != 1:
        raise RuntimeError(f"Segunda barrera: el UPDATE de la visita {visita_id} cambió "
                           f"{cursor.rowcount} filas (se esperaba 1).")


def _actualizar_oportunidad(cursor, oportunidad_id, estado, nuevo_estado):
    """
    La oportunidad pasa a nuevo_estado (al cambiar, el mismo), con
    fecha_ultimo_contacto = now() (P8: el cliente ha hablado con
    administración) y updated_at. Segunda barrera: solo si SIGUE en
    'visita_agendada'.
    """
    # SQL: el UPDATE, solo si SIGUE en el estado leído.
    cursor.execute(
        """
        UPDATE oportunidades
        SET estado = %s, fecha_ultimo_contacto = now(), updated_at = now()
        WHERE id = %s AND estado = %s;
        """,
        (nuevo_estado, oportunidad_id, estado),
    )
    # Si no cambió exactamente una fila, algo se saltó el bloqueo: 500.
    if cursor.rowcount != 1:
        raise RuntimeError(f"Segunda barrera: el UPDATE de la oportunidad {oportunidad_id} cambió "
                           f"{cursor.rowcount} filas (se esperaba 1).")


def _insertar_cambio(cursor, oportunidad_id, accion, anterior_id, nueva_id, motivo, informe):
    """La fila de cambios_visita. Devuelve su id."""
    # SQL: la fila, con sus dos visitas (las claves foráneas dobles
    # garantizan que son de ESTA oportunidad). El informe ya llega recortado
    # del esquema, como exige el CHECK.
    cursor.execute(
        """
        INSERT INTO cambios_visita (oportunidad_id, accion, visita_anterior_id, visita_nueva_id, motivo, informe)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (oportunidad_id, accion, anterior_id, nueva_id, motivo, informe),
    )
    # fetchone() da (id,); [0] saca el valor.
    return cursor.fetchone()[0]


def _log(cursor, oportunidad_id, accion, detalle):
    """UNA fila en logs, asociada a la oportunidad. El informe NUNCA va aquí."""
    # SQL: el log, con el detalle JSONB.
    cursor.execute(
        "INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES (%s, %s, %s, %s);",
        (LOG_ENTITY_TYPE_OPORTUNIDAD, oportunidad_id, accion, Json(detalle)),
    )


def _cancelar(cursor, data, oportunidad_id, estado, activa_id):
    """Las escrituras de una cancelación nueva. Devuelve la respuesta (creado=True)."""
    # La confirmada pasa a 'cancelada'.
    _cancelar_confirmada(cursor, activa_id)
    # La fila del cambio, con su motivo y su informe.
    cambio_id = _insertar_cambio(cursor, oportunidad_id, AccionCambioVisita.CANCELAR.value, activa_id, None,
                                 data.motivo.value, data.informe)
    # La oportunidad se pierde (D46.3).
    _actualizar_oportunidad(cursor, oportunidad_id, estado, ESTADO_PERDIDA)
    # El log, con 'estado' (entra en el historial de la ficha) y sin informe.
    _log(cursor, oportunidad_id, ACCION_VISITA_CANCELADA,
         {"cambio_id": cambio_id, "visita_cancelada_id": activa_id, "motivo": data.motivo.value,
          "estado": ESTADO_PERDIDA})
    # La respuesta de una cancelación NUEVA (creado=True -> 201).
    return CambioVisitaResponse(
        cambio_id=cambio_id,
        oportunidad_id=oportunidad_id,
        accion=AccionCambioVisita.CANCELAR,
        motivo=data.motivo,
        estado_oportunidad=ESTADO_PERDIDA,
        visita_id=None,
        fecha_visita=None,
        fecha_fin_visita=None,
        visita_cancelada_id=activa_id,
        creado=True,
    )


def _cambiar(cursor, data, oportunidad_id, estado, activa_id, activa_fecha, inicio, reglas):
    """Las escrituras de un cambio nuevo. Devuelve la respuesta (creado=True)."""
    # La vieja se cancela PRIMERO (índice parcial de una visita activa por
    # oportunidad) ...
    _cancelar_confirmada(cursor, activa_id)
    # SQL: ... y después se crea la nueva, ya confirmada, con el texto fijo.
    # RETURNING: su id y la fecha guardada.
    cursor.execute(
        """
        INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente)
        VALUES (%s, %s, %s, %s)
        RETURNING id, fecha_propuesta;
        """,
        (oportunidad_id, inicio, VISITA_CONFIRMADA, TEXTO_VISITA_CAMBIADA),
    )
    # El id y la fecha, que Postgres devuelve en UTC: en hora de Madrid.
    nueva_id, guardada = cursor.fetchone()
    fecha_nueva = guardada.astimezone(ZONA_MADRID)
    # La fila del cambio, sin motivo, con su informe.
    cambio_id = _insertar_cambio(cursor, oportunidad_id, AccionCambioVisita.CAMBIAR.value, activa_id, nueva_id,
                                 None, data.informe)
    # La oportunidad sigue en 'visita_agendada' (P8: contacto y updated_at).
    _actualizar_oportunidad(cursor, oportunidad_id, estado, estado)
    # El log, con 'estado' y sin informe (no entra en el historial, P9). Las
    # fechas, como texto ISO (JSON no tiene tipo fecha).
    _log(cursor, oportunidad_id, ACCION_VISITA_CAMBIADA,
         {"cambio_id": cambio_id, "visita_cancelada_id": activa_id, "visita_id": nueva_id,
          "fecha_visita_anterior": activa_fecha.astimezone(ZONA_MADRID).isoformat(),
          "fecha_visita": fecha_nueva.isoformat(), "estado": estado})
    # La respuesta de un cambio NUEVO (creado=True -> 201).
    return CambioVisitaResponse(
        cambio_id=cambio_id,
        oportunidad_id=oportunidad_id,
        accion=AccionCambioVisita.CAMBIAR,
        motivo=None,
        estado_oportunidad=estado,
        visita_id=nueva_id,
        fecha_visita=fecha_nueva,
        # D33: su fin con las reglas del paso 9.
        fecha_fin_visita=fin_de_visita(fecha_nueva, reglas),
        visita_cancelada_id=activa_id,
        creado=True,
    )
