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

from datetime import date, datetime, time

# ZoneInfo: las zonas horarias oficiales (base de datos IANA). En Windows
# necesita el paquete tzdata (fijado en requirements.txt, decisión P1):
# sin él, ZoneInfo("Europe/Madrid") falla con ZoneInfoNotFoundError.
from zoneinfo import ZoneInfo

from psycopg2.extras import Json

from app.db.connection import get_transactional_connection
from app.schemas.visits import VisitaCreate, VisitaResponse

# Zona horaria en la que el cliente dice la fecha y la hora. Tiene en
# cuenta el cambio de hora: las 08:30 del 23/10/2026 son +02:00 (verano)
# y las del 26/10/2026, +01:00 (invierno).
ZONA_MADRID = ZoneInfo("Europe/Madrid")

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
ACCION_CONFIGURACION_INCOMPLETA = "visita_configuracion_incompleta"

# Claves de reglas_negocio que usa este servicio (migración paso9). Los
# valores están en MINUTOS DESDE MEDIANOCHE, hora de Madrid: 510 = 08:30.
CLAVE_MANANA_INICIO = "visita_manana_inicio_min"
CLAVE_MANANA_FIN = "visita_manana_fin_min"
CLAVE_TARDE_INICIO = "visita_tarde_inicio_min"
CLAVE_TARDE_FIN = "visita_tarde_fin_min"
CLAVE_DURACION = "duracion_visita_min"
CLAVES_REGLAS = (CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN, CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN, CLAVE_DURACION)

MINUTOS_POR_DIA = 24 * 60


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================
# Todas heredan de VisitaRechazada, que guarda el motivo y el mensaje. El
# adaptador captura cada familia y la convierte en su código HTTP
# (404, 409, 422 o 503). Así la regla "qué código es cada cosa" vive en
# un único sitio, app/api/visits.py.


class VisitaRechazada(Exception):
    """Base común: la solicitud no se puede aceptar tal como viene."""

    def __init__(self, motivo: str, mensaje: str):
        # super().__init__(mensaje) hace que str(excepción) sea el mensaje.
        super().__init__(mensaje)
        self.motivo = motivo
        self.mensaje = mensaje


class LeadNoEncontrado(VisitaRechazada):
    """No hay ningún lead con ese lead_token (-> 404)."""


class EstadoNoPermiteVisita(VisitaRechazada):
    """El estado del lead no permite pedir visita desde el chat (-> 409)."""


class FechaNoValida(VisitaRechazada):
    """La fecha o la hora incumplen una regla de negocio (-> 422)."""


class ConfiguracionIncompleta(VisitaRechazada):
    """
    Falta o es incoherente alguna fila de reglas_negocio (-> 503).

    faltan: lista de claves que faltan o no tienen un valor válido. Se
    guarda aparte del mensaje para que el adaptador la pueda devolver.
    """

    def __init__(self, motivo: str, mensaje: str, faltan: list[str]):
        super().__init__(motivo, mensaje)
        self.faltan = faltan


# ======================================================================
# Funciones puras (sin base de datos): se prueban directamente
# ======================================================================


def combinar_fecha_hora_madrid(fecha: date, hora: time) -> datetime:
    """
    Junta un día y una hora en un INSTANTE concreto, en hora de Madrid.

    datetime.combine(fecha, hora, tzinfo=...) crea un datetime "consciente"
    (con zona horaria). ZoneInfo calcula el desfase correcto para ESE día:
    +02:00 en horario de verano y +01:00 en el de invierno. Un desfase fijo
    escrito a mano se equivocaría la mitad del año; por eso se instaló
    tzdata (prueba en scripts/check_visits_service.py).

    Las franjas de visita (08:30-20:00) nunca caen en la hora que se salta
    o se repite al cambiar el reloj (entre las 02:00 y las 03:00), así que
    no hay horas inexistentes ni ambiguas que tratar.
    """
    return datetime.combine(fecha, hora, tzinfo=ZONA_MADRID)


def _hhmm(minutos: int) -> str:
    """Minutos desde medianoche -> texto "HH:MM" (510 -> "08:30")."""
    # divmod(a, b) devuelve a la vez el cociente y el resto: (8, 30).
    horas, mins = divmod(minutos, 60)
    return f"{horas:02d}:{mins:02d}"


def validar_fecha(inicio: datetime, ahora: datetime, reglas: dict[str, int]) -> None:
    """
    Aplica las tres reglas de la fecha. No devuelve nada si todo está bien;
    si no, lanza FechaNoValida con el motivo y un mensaje que el agente
    puede leerle al cliente.

    inicio: el instante pedido, en hora de Madrid.
    ahora:  el now() de la base de datos (la referencia de tiempo es el
            reloj de Postgres, no el del ordenador que ejecuta el código).
    reglas: los cinco valores de reglas_negocio, ya comprobados.

    Las reglas se comprueban en orden y se informa de la PRIMERA que
    falla: pasado, fin de semana, franja.
    """
    # Comparar dos datetime conscientes funciona aunque estén en zonas
    # distintas: Python compara el instante real, no el texto.
    if inicio <= ahora:
        raise FechaNoValida(
            "fecha_pasada",
            f"La visita tiene que ser en el futuro, y el {inicio:%d/%m/%Y} a las {inicio:%H:%M} ya ha pasado.",
        )

    # weekday(): lunes = 0 ... sábado = 5, domingo = 6. Se usa el día EN
    # MADRID (inicio ya lleva esa zona), no el día en UTC.
    if inicio.weekday() >= 5:
        raise FechaNoValida(
            "fin_de_semana",
            f"Las visitas son de lunes a viernes, y el {inicio:%d/%m/%Y} es fin de semana.",
        )

    manana = (reglas[CLAVE_MANANA_INICIO], reglas[CLAVE_MANANA_FIN])
    tarde = (reglas[CLAVE_TARDE_INICIO], reglas[CLAVE_TARDE_FIN])
    duracion = reglas[CLAVE_DURACION]

    # Todo en minutos desde medianoche para comparar números enteros.
    empieza = inicio.hour * 60 + inicio.minute
    termina = empieza + duracion

    # La visita vale si EMPIEZA y TERMINA dentro de la misma franja, con
    # los dos extremos incluidos: con 60 min, 12:30 vale (termina a las
    # 13:30 justas) y 12:45 no (terminaría a las 13:45).
    # any(...) es True si al menos una de las franjas cumple la condición.
    cabe = any(ini <= empieza and termina <= fin for ini, fin in (manana, tarde))
    if not cabe:
        raise FechaNoValida(
            "fuera_de_franja",
            f"La visita dura {duracion} minutos y debe empezar y terminar dentro de una franja: "
            f"mañana de {_hhmm(manana[0])} a {_hhmm(manana[1])} o tarde de {_hhmm(tarde[0])} a {_hhmm(tarde[1])}. "
            f"Empezando a las {_hhmm(empieza)} terminaría a las {_hhmm(termina)}.",
        )


# ======================================================================
# Lectura de las reglas
# ======================================================================


def _leer_reglas(cursor) -> tuple[dict[str, int], list[str]]:
    """
    Lee las cinco claves de reglas_negocio y devuelve (reglas, problemas).

    reglas:    clave -> minutos como int, solo de las filas válidas.
    problemas: lista de textos, vacía si todo está bien. No se adivina
               ningún valor: si algo falla, el servicio responde 503.

    Un valor es válido si es un número ENTERO de minutos dentro del día
    (reglas_negocio.valor es NUMERIC(10,2) y llega como Decimal('510.00')).
    Además cada franja debe empezar antes de terminar, y la duración debe
    ser positiva.
    """
    cursor.execute(
        "SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);",
        (list(CLAVES_REGLAS),),
    )
    # dict(...) sobre una lista de pares (clave, valor) crea un diccionario.
    leidas = dict(cursor.fetchall())

    reglas: dict[str, int] = {}
    problemas: list[str] = []
    for clave in CLAVES_REGLAS:
        valor = leidas.get(clave)
        if valor is None:
            problemas.append(f"{clave}: no existe")
        elif valor != valor.to_integral_value() or not (0 <= valor <= MINUTOS_POR_DIA):
            # to_integral_value() redondea al entero; si cambia, es que
            # tenía decimales (510.50 minutos no tiene sentido aquí).
            problemas.append(f"{clave}: valor no válido ({valor})")
        else:
            reglas[clave] = int(valor)

    # Coherencia, solo si las filas implicadas son válidas.
    for ini, fin in ((CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN), (CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN)):
        if ini in reglas and fin in reglas and reglas[ini] >= reglas[fin]:
            problemas.append(f"{ini} ({reglas[ini]}) no es menor que {fin} ({reglas[fin]})")
    if reglas.get(CLAVE_DURACION) == 0:
        problemas.append(f"{CLAVE_DURACION}: debe ser mayor que 0")

    return reglas, problemas


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
        reglas, problemas = _leer_reglas(cursor)
        if problemas:
            # Se deja rastro para administración (P2) y se DIFIERE la
            # excepción para que este INSERT se confirme (ver arriba).
            cursor.execute(
                "INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES (%s, %s, %s, %s);",
                (
                    LOG_ENTITY_TYPE_OPORTUNIDAD,
                    oportunidad_id,
                    ACCION_CONFIGURACION_INCOMPLETA,
                    Json({"problemas": problemas}),
                ),
            )
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
