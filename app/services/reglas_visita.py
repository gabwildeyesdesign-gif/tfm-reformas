"""
Reglas de fecha y configuración de las visitas técnicas, COMPARTIDAS por
dos endpoints (plan de /gate-decisions, sección 2.3):
  - POST /visits          (app/services/visits_service.py): la visita que
                          pide el cliente desde el chat.
  - POST /gate-decisions  (app/services/gate_decisions_service.py, Bloque
                          C2): la visita que acuerda administración por
                          teléfono en un caso de Gate. (Nota 2026-10-11: el
                          servicio se llama ahora
                          resultados_llamada_service.py y la ruta nueva es
                          POST /resultados-llamada; /gate-decisions es su
                          alias.)

Las dos tienen que cumplir EXACTAMENTE las mismas reglas (en el futuro, de
lunes a viernes, visita completa dentro de una franja, con los valores de
reglas_negocio). Por eso viven aquí una sola vez, en lugar de copiarse.

Este código se MUDÓ de visits_service.py el 2026-09-30 sin cambiar la
lógica de ninguna regla. Cambios solo de organización:
  - _leer_reglas pasa a llamarse leer_reglas_visita: el guion bajo inicial
    significa "de uso interno de este archivo", y ahora la usan dos.
  - Base común RechazoNegocio para los rechazos de los dos endpoints.
  - El INSERT del log de configuración incompleta pasa a la función
    registrar_configuracion_incompleta, que añade al detalle el "origen"
    (qué endpoint lo provocó).

Como todo services/, este archivo NO importa fastapi ni fastmcp.
"""

# Los tipos de fecha y hora de la librería estándar: date (un día), time
# (una hora del día) y datetime (un instante: día y hora, con zona).
from datetime import date, datetime, time, timedelta, timezone

# ZoneInfo: las zonas horarias oficiales (base de datos IANA). En Windows
# necesita el paquete tzdata (fijado en requirements.txt, decisión P1 de
# /visits): sin él, ZoneInfo("Europe/Madrid") falla con
# ZoneInfoNotFoundError.
from zoneinfo import ZoneInfo

# Json: le dice a psycopg2 que un diccionario de Python va a una columna
# JSONB (logs.detalle).
from psycopg2.extras import Json

# Zona horaria en la que se dicen la fecha y la hora de una visita. Tiene
# en cuenta el cambio de hora: las 08:30 del 23/10/2026 son +02:00
# (verano) y las del 26/10/2026, +01:00 (invierno).
ZONA_MADRID = ZoneInfo("Europe/Madrid")

# Claves de reglas_negocio con las franjas y la duración (migración paso9).
# Los valores están en MINUTOS DESDE MEDIANOCHE, hora de Madrid: 510 = 08:30.
CLAVE_MANANA_INICIO = "visita_manana_inicio_min"
CLAVE_MANANA_FIN = "visita_manana_fin_min"
CLAVE_TARDE_INICIO = "visita_tarde_inicio_min"
CLAVE_TARDE_FIN = "visita_tarde_fin_min"
CLAVE_DURACION = "duracion_visita_min"
# D31 (2026-10-12, paso13): cada cuántos minutos se ofrecen horas de inicio
# libres en el 409 hueco_ocupado (30: 08:30, 09:00, 09:30...). Es solo para
# OFRECER: se puede pedir cualquier hora válida, como antes (P19, opción C).
CLAVE_PASO_HUECOS = "paso_huecos_min"
# Las seis claves que se leen y validan juntas (503 si falta alguna). Desde
# D31 incluye el paso: así un paso que falte da 503 al principio, en el
# mismo sitio que las demás, y no solo cuando un hueco esté ocupado.
CLAVES_REGLAS = (CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN, CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN, CLAVE_DURACION,
                 CLAVE_PASO_HUECOS)

# D31: los estados de visita que OCUPAN su hueco (una 'cancelada' o una
# 'completada' no ocupan nada).
ESTADOS_QUE_OCUPAN = ("solicitada", "confirmada")

# D31: el primer número del bloqueo de Postgres (pg_advisory_xact_lock con
# dos enteros). Es fijo del proyecto (31, por D31, y 01: visitas); el
# segundo número es el día (AAAAMMDD). Así dos peticiones del MISMO día se
# esperan una a otra, y las de días distintos no.
CLAVE_BLOQUEO_VISITAS = 3101

# 1440: el valor máximo que puede tener una de esas claves (medianoche).
MINUTOS_POR_DIA = 24 * 60

# Log de configuración incompleta. La acción no depende de quién lo
# provoque: el problema es la configuración de las VISITAS. Quién lo
# provocó va en el detalle, como "origen".
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_CONFIGURACION_INCOMPLETA = "visita_configuracion_incompleta"


# ======================================================================
# Excepciones compartidas
# ======================================================================


class RechazoNegocio(Exception):
    """
    Base común de TODOS los rechazos de negocio de los endpoints que usan
    estas reglas. Guarda un "motivo" (código estable, para n8n y las
    pruebas) y un "mensaje" (texto para el cliente o para administración).

    Cada endpoint tiene además su propia base (VisitaRechazada en /visits)
    que hereda de esta; su adaptador (app/api/...) captura RechazoNegocio y
    decide el código HTTP de cada familia.
    """

    def __init__(self, motivo: str, mensaje: str):
        # super().__init__(mensaje) hace que str(excepción) sea el mensaje.
        super().__init__(mensaje)
        # Se guardan los dos como atributos para que el adaptador los lea
        # (error.motivo, error.mensaje) al construir la respuesta HTTP.
        self.motivo = motivo
        self.mensaje = mensaje


class FechaNoValida(RechazoNegocio):
    """La fecha o la hora incumplen una regla de negocio (-> 422)."""


class ConfiguracionIncompleta(RechazoNegocio):
    """
    Falta o es incoherente alguna fila de reglas_negocio (-> 503).

    faltan: lista de claves que faltan o no tienen un valor válido. Se
    guarda aparte del mensaje para que el adaptador la pueda devolver.
    """

    def __init__(self, motivo: str, mensaje: str, faltan: list[str]):
        # El motivo y el mensaje los guarda la clase base (RechazoNegocio).
        super().__init__(motivo, mensaje)
        # Y la lista de claves con problemas, como atributo propio.
        self.faltan = faltan


class HuecoOcupado(RechazoNegocio):
    """
    D31: la visita se solapa con otra visita activa (-> 409 hueco_ocupado).

    huecos_libres: las horas de inicio ("HH:MM") de ese día que siguen
    libres, ya en el futuro; lista vacía si no queda ninguna. Se guarda
    aparte del mensaje para que el adaptador la devuelva y el agente la
    ofrezca sin tener que sacarla de una frase (P18).
    """

    def __init__(self, motivo: str, mensaje: str, huecos_libres: list[str]):
        # El motivo y el mensaje los guarda la clase base (RechazoNegocio).
        super().__init__(motivo, mensaje)
        # Y la lista de horas libres, como atributo propio.
        self.huecos_libres = huecos_libres


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
    # Une el día y la hora con la zona de Madrid (ver el docstring).
    return datetime.combine(fecha, hora, tzinfo=ZONA_MADRID)


def _hhmm(minutos: int) -> str:
    """Minutos desde medianoche -> texto "HH:MM" (510 -> "08:30")."""
    # divmod(a, b) devuelve a la vez el cociente y el resto: (8, 30).
    horas, mins = divmod(minutos, 60)
    # ":02d" escribe el número con dos cifras, con un cero delante si hace
    # falta: 8 -> "08".
    return f"{horas:02d}:{mins:02d}"


def validar_fecha(inicio: datetime, ahora: datetime, reglas: dict[str, int]) -> None:
    """
    Aplica las tres reglas de la fecha. No devuelve nada si todo está bien;
    si no, lanza FechaNoValida con el motivo y un mensaje que se le puede
    leer al cliente.

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

    # Las dos franjas como pares (inicio, fin) en minutos, y la duración.
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
    # Si no cabe en ninguna, se rechaza con un mensaje que dice las franjas
    # y a qué hora terminaría la visita pedida.
    if not cabe:
        raise FechaNoValida(
            "fuera_de_franja",
            f"La visita dura {duracion} minutos y debe empezar y terminar dentro de una franja: "
            f"mañana de {_hhmm(manana[0])} a {_hhmm(manana[1])} o tarde de {_hhmm(tarde[0])} a {_hhmm(tarde[1])}. "
            f"Empezando a las {_hhmm(empieza)} terminaría a las {_hhmm(termina)}.",
        )


# ======================================================================
# Acceso a la base de datos (con el cursor de quien llama)
# ======================================================================


def leer_reglas_visita(cursor) -> tuple[dict[str, int], list[str]]:
    """
    Lee las cinco claves de reglas_negocio y devuelve (reglas, problemas).
    (Antes se llamaba _leer_reglas y vivía en visits_service.py.)

    reglas:    clave -> minutos como int, solo de las filas válidas.
    problemas: lista de textos, vacía si todo está bien. No se adivina
               ningún valor: si algo falla, el endpoint responde 503.

    Un valor es válido si es un número ENTERO de minutos dentro del día
    (reglas_negocio.valor es NUMERIC(10,2) y llega como Decimal('510.00')).
    Además cada franja debe empezar antes de terminar, y la duración debe
    ser positiva.

    Recibe el cursor de quien la llama para leer DENTRO de su transacción.
    """
    # SQL: lee la clave y el valor de las filas de reglas_negocio cuya
    # clave está en la lista CLAVES_REGLAS. "= ANY(%s)" significa "es igual
    # a alguno de los elementos de la lista"; psycopg2 convierte la lista
    # de Python en un array de Postgres.
    cursor.execute(
        "SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);",
        (list(CLAVES_REGLAS),),
    )
    # dict(...) sobre una lista de pares (clave, valor) crea un diccionario.
    leidas = dict(cursor.fetchall())

    # Los dos resultados empiezan vacíos y se rellenan en el bucle.
    reglas: dict[str, int] = {}
    problemas: list[str] = []
    # Se recorren las claves ESPERADAS (no las leídas), para detectar las
    # que faltan.
    for clave in CLAVES_REGLAS:
        # .get(clave) devuelve None si la clave no se leyó.
        valor = leidas.get(clave)
        # Caso 1: la fila no existe.
        if valor is None:
            problemas.append(f"{clave}: no existe")
        # Caso 2: existe pero tiene decimales o está fuera del día.
        elif valor != valor.to_integral_value() or not (0 <= valor <= MINUTOS_POR_DIA):
            # to_integral_value() redondea al entero; si cambia, es que
            # tenía decimales (510.50 minutos no tiene sentido aquí).
            problemas.append(f"{clave}: valor no válido ({valor})")
        # Caso 3: valor válido; se guarda como int (llegaba como Decimal).
        else:
            reglas[clave] = int(valor)

    # Coherencia, solo si las filas implicadas son válidas.
    # Cada franja tiene que empezar antes de terminar.
    for ini, fin in ((CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN), (CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN)):
        if ini in reglas and fin in reglas and reglas[ini] >= reglas[fin]:
            problemas.append(f"{ini} ({reglas[ini]}) no es menor que {fin} ({reglas[fin]})")
    # Una visita de 0 minutos no tiene sentido (los negativos ya los
    # rechazó el caso 2).
    if reglas.get(CLAVE_DURACION) == 0:
        problemas.append(f"{CLAVE_DURACION}: debe ser mayor que 0")
    # D31: un paso de 0 minutos dejaría el bucle de huecos_libres sin avanzar.
    if reglas.get(CLAVE_PASO_HUECOS) == 0:
        problemas.append(f"{CLAVE_PASO_HUECOS}: debe ser mayor que 0")

    # Se devuelven los dos: quien llama decide qué hacer si hay problemas.
    return reglas, problemas


def registrar_configuracion_incompleta(cursor, oportunidad_id: int, problemas: list[str], origen: str) -> None:
    """
    Deja en logs el rastro de una configuración incompleta, para
    administración (decisión P2 de /visits).

    origen: qué endpoint lo provocó ("visits" o "gate_decisions"). Va solo
    en el detalle del log, no en la respuesta al cliente. Es la única
    diferencia con el INSERT que antes estaba en visits_service.py.

    Usa el cursor de quien llama, dentro de SU transacción. OJO: quien
    llama debe lanzar la excepción DESPUÉS del commit (rechazo diferido),
    o este INSERT se desharía con el rollback.
    """
    # SQL: inserta UNA fila en logs, asociada a la oportunidad, con la
    # acción "visita_configuracion_incompleta" y, como JSONB, la lista de
    # problemas y el origen. Los %s se rellenan con la tupla de debajo.
    cursor.execute(
        "INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES (%s, %s, %s, %s);",
        (
            LOG_ENTITY_TYPE_OPORTUNIDAD,
            oportunidad_id,
            ACCION_CONFIGURACION_INCOMPLETA,
            Json({"problemas": problemas, "origen": origen}),
        ),
    )


# ======================================================================
# D31: el hueco tiene que estar libre (revisa D18.6)
# ======================================================================
# Un solo técnico: una visita 'solicitada' o 'confirmada' OCUPA el
# intervalo [inicio, inicio + duracion_visita_min), cerrado al principio y
# abierto al final. Dos visitas chocan si se SOLAPAN, no solo si empiezan a
# la misma hora: una de 10:00 a 11:00 ocupa las 10:59 y deja libres las
# 11:00 (plan, sección 10.3).
#
# Orden de las comprobaciones (precisión 2 del tutor), igual en POST
# /visits, POST /resultados-llamada y su alias: forma (422 de Pydantic) ->
# 404 -> estado y Gate (409) -> reglas de fecha (503 y 422 de negocio) ->
# las comprobaciones propias de cada endpoint que miran la visita PROPIA
# (repetición 200, visita_confirmada, visita_cambiada, visita_activa_existente)
# -> hueco_ocupado, la última antes de escribir.


def solapa(inicio_a: datetime, inicio_b: datetime, duracion_min: int) -> bool:
    """
    True si dos visitas de duracion_min minutos que empiezan en inicio_a y
    en inicio_b se solapan. Intervalos [inicio, fin): que una termine justo
    cuando empieza la otra NO es solapar.
    """
    # Las cuentas, en UTC: sumar minutos a una hora de Madrid con su zona
    # es una suma "de reloj de pared" (gotcha de CLAUDE.md); en UTC es una
    # duración real.
    a = inicio_a.astimezone(timezone.utc)
    # La otra, también en UTC.
    b = inicio_b.astimezone(timezone.utc)
    # La duración como timedelta.
    duracion = timedelta(minutes=duracion_min)
    # Se solapan si cada una empieza ANTES de que termine la otra (estricto:
    # el final es abierto).
    return a < b + duracion and b < a + duracion


def huecos_libres(dia: date, ahora: datetime, reglas: dict[str, int], ocupadas: list[datetime]) -> list[str]:
    """
    Las horas de inicio ("HH:MM", Madrid) de ese día que se pueden ofrecer:
    cada paso_huecos_min minutos desde el inicio de cada franja, con la
    visita entera dentro de la franja (la misma regla que validar_fecha), ya
    en el futuro y sin solaparse con ninguna de las ocupadas.
    """
    # La lista empieza vacía.
    libres = []
    # Las dos franjas: (inicio, fin) en minutos desde medianoche.
    for clave_inicio, clave_fin in ((CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN), (CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN)):
        # El primer candidato: el inicio de la franja.
        minuto = reglas[clave_inicio]
        # Mientras la visita entera quepa en la franja.
        while minuto + reglas[CLAVE_DURACION] <= reglas[clave_fin]:
            # Ese minuto de ese día, en Madrid (con su desfase de ESE día).
            candidato = datetime.combine(dia, time(minuto // 60, minuto % 60), tzinfo=ZONA_MADRID)
            # En el futuro y sin solaparse con ninguna ocupada: se ofrece.
            if candidato > ahora and not any(solapa(candidato, o, reglas[CLAVE_DURACION]) for o in ocupadas):
                # Como texto "HH:MM".
                libres.append(_hhmm(minuto))
            # El siguiente candidato.
            minuto += reglas[CLAVE_PASO_HUECOS]
    # Las horas libres, en orden.
    return libres


def mensaje_hueco_ocupado(libres: list[str]) -> str:
    """
    El texto del 409. Solo dice que la hora no está libre y cuáles sí: NUNCA
    datos de la otra visita (ni su id, ni su cliente, ni a qué hora termina).
    """
    # Con horas libres: se nombran.
    if libres:
        # ", ".join une la lista con comas.
        return ("Esa hora no está disponible: coincide con otra visita. "
                f"Horas de inicio libres ese día: {', '.join(libres)}.")
    # Sin ninguna: hay que pedir otro día.
    return "Esa hora no está disponible y ese día no queda ninguna hora libre: propón otro día."


def comprobar_hueco_libre(cursor, inicio: datetime, ahora: datetime, reglas: dict[str, int],
                          excluir: list[int] | tuple[int, ...] = ()) -> None:
    """
    D31: lanza HuecoOcupado (409) si la visita que empieza en inicio se
    solapa con otra visita activa. Si no, no hace nada.

    excluir: ids de visitas que NO cuentan porque son de la propia
    oportunidad y se sustituyen o se confirman en esta misma transacción (la
    sustitución de /visits, confirmar_hora y otra_hora: C8).

    Se llama con el cursor de quien llama, DESPUÉS de bloquear su
    oportunidad (FOR UPDATE) y como última comprobación antes de escribir.

    Contra dos peticiones a la vez (P15): antes de mirar, toma un bloqueo de
    Postgres para ESE día (pg_advisory_xact_lock), que dura hasta el final
    de la transacción. La segunda petición del mismo día espera aquí a que
    la primera termine; su consulta de después ve la visita ya confirmada
    (READ COMMITTED: cada sentencia ve lo confirmado hasta ese momento) y
    da 409. Solo protege a quien pasa por aquí: por eso es el ÚNICO camino
    (R6: una escritura a mano en la base de datos se lo salta).
    """
    # El día de la visita en Madrid (el de la franja).
    dia = inicio.astimezone(ZONA_MADRID).date()
    # SQL: el bloqueo del día. Dos enteros: el del proyecto y el día como
    # AAAAMMDD (20261019). Se libera solo al terminar la transacción
    # (commit o rollback); con Supavisor en modo Session, la transacción va
    # entera por la misma conexión.
    cursor.execute("SELECT pg_advisory_xact_lock(%s, %s);",
                   (CLAVE_BLOQUEO_VISITAS, dia.year * 10000 + dia.month * 100 + dia.day))
    # Desde: medianoche de ese día menos una duración (una visita que
    # empezara antes y terminara ese día también contaría). Hasta: la
    # medianoche siguiente. En Madrid, con el desfase de cada medianoche.
    desde = datetime.combine(dia, time(0, 0), tzinfo=ZONA_MADRID) - timedelta(minutes=reglas[CLAVE_DURACION])
    # La medianoche siguiente.
    hasta = datetime.combine(dia + timedelta(days=1), time(0, 0), tzinfo=ZONA_MADRID)
    # SQL: el inicio de las visitas activas de ese día, salvo las excluidas.
    # "NOT (id = ANY(...))" con una lista vacía es verdadero; ::int[] le dice
    # a Postgres el tipo de la lista aunque esté vacía.
    cursor.execute(
        """
        SELECT fecha_propuesta FROM visitas
        WHERE estado = ANY(%s)
          AND NOT (id = ANY(%s::int[]))
          AND fecha_propuesta >= %s AND fecha_propuesta < %s;
        """,
        (list(ESTADOS_QUE_OCUPAN), list(excluir), desde, hasta),
    )
    # Solo los inicios (llegan en UTC; solapa compara instantes).
    ocupadas = [fila[0] for fila in cursor.fetchall()]
    # ¿Se solapa con alguna? Si no, el hueco está libre y no se hace nada.
    if any(solapa(inicio, ocupada, reglas[CLAVE_DURACION]) for ocupada in ocupadas):
        # Las horas libres de ese día, para ofrecerlas.
        libres = huecos_libres(dia, ahora, reglas, ocupadas)
        # 409 con el mensaje y la lista.
        raise HuecoOcupado("hueco_ocupado", mensaje_hueco_ocupado(libres), libres)
