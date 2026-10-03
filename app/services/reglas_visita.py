"""
Reglas de fecha y configuración de las visitas técnicas, COMPARTIDAS por
dos endpoints (plan de /gate-decisions, sección 2.3):
  - POST /visits          (app/services/visits_service.py): la visita que
                          pide el cliente desde el chat.
  - POST /gate-decisions  (app/services/gate_decisions_service.py, Bloque
                          C2): la visita que acuerda administración por
                          teléfono en un caso de Gate.

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
from datetime import date, datetime, time

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
CLAVES_REGLAS = (CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN, CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN, CLAVE_DURACION)

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
