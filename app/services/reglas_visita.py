"""
Reglas de fecha y configuración de las visitas técnicas, COMPARTIDAS por
dos endpoints (plan de /gate-decisions, sección 2.3):
  - POST /visits          (app/services/visits_service.py): la visita que
                          pide el cliente desde el chat.
  - POST /resultados-llamada (app/services/resultados_llamada_service.py):
                          la visita que acuerda administración por
                          teléfono. (Nació como POST /gate-decisions, Bloque
                          C2; cambio 2026-10-22: ese alias se quitó en el
                          bloque 5, versión anterior en aba39c4.)

Las dos tienen que cumplir EXACTAMENTE las mismas reglas (en el futuro, de
lunes a viernes, visita completa dentro de una franja, con los valores de
reglas_negocio). Por eso viven aquí una sola vez, en lugar de copiarse.
(D45, 2026-10-23: más la antelación mínima por canal, que cada endpoint
elige con primer_dia_del_canal; plan docs/Plan_Antelacion_Visitas_D45.txt.)

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
# (Nota 2026-10-13, D32: paso_huecos_min de D31 ya NO se lee. La sustituye
# la rejilla de D23.5: duración + desplazamiento. La fila se borra después
# del merge con paso14b, por expand/contract; plan D32, 2.6.)
# D32 (paso14): minutos entre el fin de una visita y el inicio de la
# siguiente (D23.5: 15). Entra en la rejilla y en el choque de dos visitas.
CLAVE_DESPLAZAMIENTO = "desplazamiento_min"
# D32 (paso14): cuántos días CON hueco ofrece el modo días de GET
# /huecos-libres y de la tool consultar_huecos_libres (10).
CLAVE_DIAS_OFERTA = "dias_oferta_visita"
# El máximo de dias_oferta_visita y, a la vez, cuántos días laborables se
# miran como mucho buscando días con hueco (unas 12 semanas; plan D32, 3.3).
MAX_DIAS_BUSQUEDA = 60
# D45 (paso17, 2026-10-23): la antelación mínima de una visita, por canal.
# El valor es el n.º del día laborable DESPUÉS de hoy que es la primera
# fecha posible (2: el chat, desde el 2.º laborable; 1: administración, desde
# el 1.º; 0: hoy mismo, solo con la condición de ser futura).
CLAVE_ANTELACION_CHAT = "dias_antelacion_visita_chat"
CLAVE_ANTELACION_ADMIN = "dias_antelacion_visita_admin"
# El máximo de las dos (P5 del plan D45): 0..10, como dias_oferta_visita.
MAX_DIAS_ANTELACION = 10
# Los dos canales. Los fija el ADAPTADOR (la tool MCP, "chat"; GET
# /huecos-libres, "administracion"), nunca un dato de la petición: así el
# chat no puede pedir la regla de administración (plan D45, sección 2).
CANAL_CHAT = "chat"
CANAL_ADMINISTRACION = "administracion"
# Qué clave usa cada canal.
CLAVE_ANTELACION_DE = {CANAL_CHAT: CLAVE_ANTELACION_CHAT, CANAL_ADMINISTRACION: CLAVE_ANTELACION_ADMIN}
# Las nueve claves que se leen y validan juntas (503 si falta alguna). Desde
# D32: las cinco de paso9, el desplazamiento y los días de oferta; desde
# D45, las dos antelaciones.
CLAVES_REGLAS = (CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN, CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN, CLAVE_DURACION,
                 CLAVE_DESPLAZAMIENTO, CLAVE_DIAS_OFERTA, CLAVE_ANTELACION_CHAT, CLAVE_ANTELACION_ADMIN)
# Las dos franjas, como pares (nombre, clave de inicio, clave de fin). El
# nombre es el de la respuesta ("manana" sin eñe, como en get_business_rules).
FRANJAS = (("manana", CLAVE_MANANA_INICIO, CLAVE_MANANA_FIN), ("tarde", CLAVE_TARDE_INICIO, CLAVE_TARDE_FIN))

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

# Los días de la semana en español y en minúscula (weekday(): lunes = 0).
# Una tabla propia: strftime("%A") depende del idioma del sistema. (D45: se
# muda aquí desde huecos_libres_service.py, que la importa, porque ahora la
# usa también el mensaje del 422 antelacion_insuficiente.)
DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

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
    """
    La fecha o la hora incumplen una regla de negocio (-> 422).

    horas_validas (D32, solo en fuera_de_rejilla): las horas de inicio de la
    rejilla por franja, {"manana": [...], "tarde": [...]}; None en los demás
    motivos. Se guarda aparte del mensaje para que el adaptador la devuelva
    (P2 del plan D32), como huecos_libres en el 409.
    """

    def __init__(self, motivo: str, mensaje: str, horas_validas: dict[str, list[str]] | None = None):
        # El motivo y el mensaje los guarda la clase base (RechazoNegocio).
        super().__init__(motivo, mensaje)
        # Y las horas válidas, o None (el adaptador solo las añade si no es None).
        self.horas_validas = horas_validas


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
    (D32: "choca", contando el desplazamiento; ver choca().)

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


def rejilla(reglas: dict[str, int]) -> dict[str, list[int]]:
    """
    D32: las horas de inicio VÁLIDAS de cualquier día, por franja, en
    minutos desde medianoche: {"manana": [510, 585, 660, 735], "tarde":
    [1020, 1095]} con los valores de hoy (D23.5: 08:30, 09:45, 11:00, 12:15,
    17:00 y 18:15).

    Es el ÚNICO sitio que calcula horas de inicio (plan D32, 2.5): la
    validación de la hora (validar_fecha), la disponibilidad (el 409, GET
    /huecos-libres y la tool) salen de aquí.

    Regla: en cada franja, la primera es el inicio de la franja y cada una
    de las siguientes, la anterior + duración + desplazamiento, mientras la
    VISITA entera quepa en la franja (el desplazamiento de la última no
    tiene que caber).
    """
    # El paso entre dos inicios: la visita y el desplazamiento hasta la
    # siguiente (75 con los valores de hoy). Nunca 0: leer_reglas_visita exige
    # duración > 0, así que el bucle siempre avanza.
    paso = reglas[CLAVE_DURACION] + reglas[CLAVE_DESPLAZAMIENTO]
    # El resultado, una lista por franja.
    horas: dict[str, list[int]] = {}
    # Las dos franjas.
    for nombre, clave_inicio, clave_fin in FRANJAS:
        # La lista de esta franja empieza vacía.
        horas[nombre] = []
        # El primer candidato: el inicio de la franja.
        minuto = reglas[clave_inicio]
        # Mientras la visita entera quepa en la franja (extremo incluido).
        while minuto + reglas[CLAVE_DURACION] <= reglas[clave_fin]:
            # Es una hora válida.
            horas[nombre].append(minuto)
            # La siguiente.
            minuto += paso
    # Las horas por franja.
    return horas


def rejilla_texto(reglas: dict[str, int]) -> dict[str, list[str]]:
    """La rejilla como textos "HH:MM" por franja (para mensajes y respuestas)."""
    # Cada minuto de cada franja, convertido con _hhmm.
    return {nombre: [_hhmm(m) for m in minutos] for nombre, minutos in rejilla(reglas).items()}


def _lista_con_o(horas: list[str]) -> str:
    """["08:30", "09:45", "11:00"] -> "08:30, 09:45 o 11:00" (para el mensaje)."""
    # Una sola (o ninguna): tal cual.
    if len(horas) <= 1:
        # "" o la única.
        return "".join(horas)
    # Varias: comas entre todas menos la última, que va con "o".
    return f"{', '.join(horas[:-1])} o {horas[-1]}"


def mensaje_fuera_de_rejilla(validas: dict[str, list[str]], pedida: str) -> str:
    """El texto del 422 fuera_de_rejilla: las horas válidas y la pedida."""
    # Cada franja con horas, como "mañana 08:30, 09:45, 11:00 o 12:15".
    partes = [f"{'mañana' if nombre == 'manana' else nombre} {_lista_con_o(horas)}"
              for nombre, horas in validas.items() if horas]
    # La frase completa.
    return (f"Las visitas empiezan a horas fijas: {'; '.join(partes)}. "
            f"La hora {pedida} no es una de ellas.")


# ======================================================================
# D45: antelación mínima (funciones puras)
# ======================================================================


def primer_dia_permitido(hoy: date, n: int) -> date:
    """
    D45: el n-ésimo día laborable DESPUÉS de hoy (n = 0: hoy mismo).
    Laborable = de lunes a viernes, el mismo criterio que el resto del
    sistema (weekday() < 5); los festivos no se detectan (limitación
    conocida). Ejemplos: viernes con n = 2 -> martes; lunes con n = 1 ->
    martes; sábado con n = 1 -> lunes.
    """
    # El día por el que va la cuenta, y cuántos laborables faltan.
    dia, faltan = hoy, n
    # Mientras falte alguno, se avanza un día.
    while faltan > 0:
        # El día siguiente.
        dia += timedelta(days=1)
        # Solo cuenta si es de lunes (0) a viernes (4).
        if dia.weekday() < 5:
            # Uno menos.
            faltan -= 1
    # El primer día permitido.
    return dia


def primer_dia_del_canal(ahora: datetime, reglas: dict[str, int], canal: str) -> date:
    """
    D45: la primera fecha posible para ese canal. "Hoy" es la fecha EN
    MADRID del now() de Postgres (precisión 6 del tutor): a las 23:59 y a
    las 00:01 son días distintos. El número de laborables sale de
    reglas_negocio (ninguno escrito en el código).

    canal: CANAL_CHAT o CANAL_ADMINISTRACION, puesto por el adaptador.
    Otro valor es un fallo de programación: ValueError, nunca un canal por
    defecto elegido en silencio.
    """
    # Un canal desconocido: error, sin adivinar.
    if canal not in CLAVE_ANTELACION_DE:
        # Se nombra el canal recibido (no es un dato del cliente).
        raise ValueError(f"canal desconocido: {canal!r}")
    # Hoy en Madrid (el día del reloj de Postgres, no el del UTC).
    hoy = ahora.astimezone(ZONA_MADRID).date()
    # El laborable que toca, con la regla de ese canal.
    return primer_dia_permitido(hoy, reglas[CLAVE_ANTELACION_DE[canal]])


def mensaje_antelacion(primer_dia: date) -> str:
    """El texto del 422 antelacion_insuficiente, neutro para los dos canales
    (precisión 1 del tutor): "La primera fecha posible para la visita es el
    martes 27/10."."""
    # El día de la semana y la fecha dd/mm.
    return f"La primera fecha posible para la visita es el {DIAS_SEMANA[primer_dia.weekday()]} {primer_dia:%d/%m}."


def validar_antelacion(inicio: datetime, primer_dia: date | None) -> None:
    """
    D45: lanza FechaNoValida("antelacion_insuficiente") si el día de la
    visita (en Madrid) es anterior a primer_dia. primer_dia None: sin
    antelación (confirmar_hora, que solo exige que sea futura).

    validar_fecha la llama justo después de fecha_pasada; POST /visits la
    llama aparte, DESPUÉS de detectar la repetición exacta (P4 del plan D45:
    un reenvío de n8n no cambia la respuesta, D7).
    """
    # Sin primer día: no hay nada que mirar.
    if primer_dia is None:
        # Nada.
        return
    # El día de la visita en Madrid (inicio ya lleva esa zona; se fuerza
    # por si llega en otra).
    dia = inicio.astimezone(ZONA_MADRID).date()
    # Antes del primer día permitido: 422.
    if dia < primer_dia:
        # Con el mensaje neutro.
        raise FechaNoValida("antelacion_insuficiente", mensaje_antelacion(primer_dia))


def validar_fecha(inicio: datetime, ahora: datetime, reglas: dict[str, int], primer_dia: date | None) -> None:
    """
    Aplica las reglas de la fecha. No devuelve nada si todo está bien;
    si no, lanza FechaNoValida con el motivo y un mensaje que se le puede
    leer al cliente.

    inicio: el instante pedido, en hora de Madrid.
    ahora:  el now() de la base de datos (la referencia de tiempo es el
            reloj de Postgres, no el del ordenador que ejecuta el código).
    reglas: los valores de reglas_negocio, ya comprobados.
    primer_dia: D45, la primera fecha posible (primer_dia_del_canal), o
            None si no se aplica antelación (confirmar_hora, y POST /visits,
            que la mira aparte). OBLIGATORIO: sin valor por defecto, para
            que nadie se olvide de decidirlo.

    Las reglas se comprueban en orden y se informa de la PRIMERA que
    falla: pasado, antelación (D45), fin de semana, franja y (desde D32,
    2026-10-13) rejilla.
    """
    # Comparar dos datetime conscientes funciona aunque estén en zonas
    # distintas: Python compara el instante real, no el texto.
    if inicio <= ahora:
        raise FechaNoValida(
            "fecha_pasada",
            f"La visita tiene que ser en el futuro, y el {inicio:%d/%m/%Y} a las {inicio:%H:%M} ya ha pasado.",
        )

    # D45: la antelación mínima, justo después del pasado (plan D45, 2).
    validar_antelacion(inicio, primer_dia)

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

    # D32 (P1, P2): dentro de la franja, la hora tiene que ser una de las de
    # la rejilla. Va DESPUÉS de fuera_de_franja: una hora fuera de la franja
    # sigue dando fuera_de_franja (plan D32, 2.3). Se compara en minutos (la
    # hora llega como HH:MM: sin segundos).
    if not any(empieza in minutos for minutos in rejilla(reglas).values()):
        # Las horas válidas como texto, por franja.
        validas = rejilla_texto(reglas)
        # 422 con el mensaje y las horas válidas aparte.
        raise FechaNoValida("fuera_de_rejilla", mensaje_fuera_de_rejilla(validas, _hhmm(empieza)), validas)


# ======================================================================
# Acceso a la base de datos (con el cursor de quien llama)
# ======================================================================


def leer_reglas_visita(cursor) -> tuple[dict[str, int], list[str]]:
    """
    Lee las claves de CLAVES_REGLAS (siete desde D32, nueve desde D45) de reglas_negocio y
    devuelve (reglas, problemas).
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
    # D32: los días de oferta, entre 1 y MAX_DIAS_BUSQUEDA (60). El 0 lo deja
    # pasar el caso 2 (está dentro del día) y no tendría sentido: no se
    # ofrecería ningún día. El desplazamiento puede ser 0 (sin desplazamiento).
    if CLAVE_DIAS_OFERTA in reglas and not (1 <= reglas[CLAVE_DIAS_OFERTA] <= MAX_DIAS_BUSQUEDA):
        # Se nombra el rango para que administración sepa qué poner.
        problemas.append(f"{CLAVE_DIAS_OFERTA}: debe estar entre 1 y {MAX_DIAS_BUSQUEDA} "
                         f"({reglas[CLAVE_DIAS_OFERTA]})")
    # D45 (P5): las dos antelaciones, entre 0 y MAX_DIAS_ANTELACION (10). Los
    # decimales y los negativos ya los rechazó el caso 2; aquí, el máximo.
    for clave in (CLAVE_ANTELACION_CHAT, CLAVE_ANTELACION_ADMIN):
        # Solo si se leyó bien y se pasa del máximo.
        if clave in reglas and reglas[clave] > MAX_DIAS_ANTELACION:
            # Se nombra el rango, como en los días de oferta.
            problemas.append(f"{clave}: debe estar entre 0 y {MAX_DIAS_ANTELACION} ({reglas[clave]})")

    # Se devuelven los dos: quien llama decide qué hacer si hay problemas.
    return reglas, problemas


def registrar_configuracion_incompleta(cursor, oportunidad_id: int, problemas: list[str], origen: str) -> None:
    """
    Deja en logs el rastro de una configuración incompleta, para
    administración (decisión P2 de /visits).

    origen: qué endpoint lo provocó ("visits" o "resultados_llamada"; los
    logs anteriores al 2026-10-22 pueden decir "gate_decisions", el alias
    quitado). Va solo
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
# D31 y D32: el hueco tiene que estar libre (D31 revisa D18.6; D32, del
# 2026-10-13, revisa D31: rejilla de D23.5 y desplazamiento)
# ======================================================================
# Un solo técnico. Una visita 'solicitada' o 'confirmada' OCUPA el intervalo
# [inicio, inicio + duracion_visita_min) y, desde D32, necesita además
# desplazamiento_min minutos hasta la siguiente. Dos visitas CHOCAN si entre
# el fin de una y el inicio de la otra hay MENOS de desplazamiento_min
# (exactamente desplazamiento_min no es chocar: 09:45 tras una de 08:30 deja
# 15 justos). Entre dos horas de la rejilla hay justo duración +
# desplazamiento, así que dos horas válidas nunca chocan si son distintas;
# una visita antigua fuera de la rejilla (o una rejilla que cambie) sí
# puede chocar con varias (plan D32, C3 y R3).
#
# Orden de las comprobaciones (precisión 2 del tutor), igual en POST
# /visits, POST /resultados-llamada: forma (422 de Pydantic) ->
# 404 -> estado y Gate (409) -> reglas de fecha (503 y 422 de negocio,
# fuera_de_rejilla la última) -> las comprobaciones propias de cada endpoint
# que miran la visita PROPIA (repetición 200, visita_confirmada,
# visita_cambiada, visita_activa_existente) -> hueco_ocupado, la última
# antes de escribir.
#
# UNA sola función calcula la disponibilidad (plan D32, 2.5):
# disponibilidad(). La usan el 409 (comprobar_hueco_libre), GET
# /huecos-libres y la tool consultar_huecos_libres. Prohibido calcularla en
# otro sitio.


def choca(inicio_a: datetime, inicio_b: datetime, duracion_min: int, desplazamiento_min: int) -> bool:
    """
    True si dos visitas de duracion_min minutos que empiezan en inicio_a y
    en inicio_b chocan: entre el fin de una y el inicio de la otra hay menos
    de desplazamiento_min minutos (o se solapan). Sustituye a solapa() de
    D31, que es este mismo cálculo con desplazamiento 0.
    """
    # Las cuentas, en UTC: sumar minutos a una hora de Madrid con su zona
    # es una suma "de reloj de pared" (gotcha de CLAUDE.md); en UTC es una
    # duración real.
    a = inicio_a.astimezone(timezone.utc)
    # La otra, también en UTC.
    b = inicio_b.astimezone(timezone.utc)
    # Lo que ocupa una visita contando el desplazamiento hasta la siguiente.
    ocupa = timedelta(minutes=duracion_min + desplazamiento_min)
    # Chocan si cada una empieza ANTES de que la otra termine y pase el
    # desplazamiento (estricto: llegar justo a tiempo no es chocar).
    return a < b + ocupa and b < a + ocupa


def fin_de_visita(inicio: datetime, reglas: dict[str, int]) -> datetime:
    """
    D33 (2026-10-14): la hora de FIN de una visita que empieza en inicio,
    en hora de Madrid con su desfase: inicio + duracion_visita_min. La
    devuelven POST /visits y POST /resultados-llamada como
    fecha_fin_visita, para que n8n (WF5, Google Calendar) no copie la regla.

    reglas: las de leer_reglas_visita, ya comprobadas (duración > 0). Es el
    ÚNICO sitio que suma la duración para la respuesta; nunca un número
    escrito a mano.
    """
    # La suma, en UTC: sumar minutos a una hora de Madrid con su zona es una
    # suma "de reloj de pared" (gotcha de CLAUDE.md); en UTC es una duración
    # real. Después, de vuelta a Madrid, con el desfase de ESE instante.
    return (inicio.astimezone(timezone.utc) + timedelta(minutes=reglas[CLAVE_DURACION])).astimezone(ZONA_MADRID)


def disponibilidad(cursor, dias: list[date], ahora: datetime, reglas: dict[str, int],
                   excluir: list[int] | tuple[int, ...] = (),
                   primer_dia: date | None = None) -> dict[date, dict[str, list[str]]]:
    """
    D32: las horas de inicio LIBRES ("HH:MM", Madrid) de cada día de la
    lista, por franja: {date(2026, 10, 15): {"manana": ["09:45", "11:00"],
    "tarde": ["18:15"]}, ...}.

    Una hora está libre si es de la rejilla (rejilla()), el día es de lunes
    a viernes, es POSTERIOR a ahora (el now() de Postgres) y no choca con
    ninguna visita activa ('solicitada' o 'confirmada'), salvo las de
    excluir (la propia visita que se sustituye o se confirma, C8 de D31).
    Un sábado, un domingo o un día ya pasado dan las dos listas vacías.

    primer_dia (D45): si se da, los días ANTERIORES salen con las dos listas
    vacías (antelación mínima al OFRECER, GET /huecos-libres y la tool).
    comprobar_hueco_libre NO lo pasa: el 409 solo mira choques, y la
    antelación de quien escribe ya la miró validar_fecha (así confirmar_hora
    no cambia).

    Hace UNA sola consulta, sea cual sea el número de días (plan D32, 13):
    las visitas activas de toda la ventana, de la medianoche del primer día
    menos lo que ocupa una visita hasta la medianoche siguiente al último
    más lo mismo (una visita de la víspera o del día siguiente también
    puede chocar).

    Recibe el cursor de quien llama: lee DENTRO de su transacción (y, en el
    409, después del bloqueo del día).
    """
    # Sin días, nada que calcular (y ninguna consulta).
    if not dias:
        # Diccionario vacío.
        return {}
    # Lo que ocupa una visita contando el desplazamiento.
    ocupa = timedelta(minutes=reglas[CLAVE_DURACION] + reglas[CLAVE_DESPLAZAMIENTO])
    # Desde: la medianoche del primer día, en Madrid, menos lo que ocupa.
    desde = datetime.combine(min(dias), time(0, 0), tzinfo=ZONA_MADRID) - ocupa
    # Hasta: la medianoche siguiente al último día, más lo que ocupa.
    hasta = datetime.combine(max(dias) + timedelta(days=1), time(0, 0), tzinfo=ZONA_MADRID) + ocupa
    # SQL: el inicio de las visitas activas de la ventana, salvo las
    # excluidas. "NOT (id = ANY(...))" con una lista vacía es verdadero;
    # ::int[] le dice a Postgres el tipo aunque la lista esté vacía. Solo se
    # lee fecha_propuesta: nada de quién es la visita.
    cursor.execute(
        """
        SELECT fecha_propuesta FROM visitas
        WHERE estado = ANY(%s)
          AND NOT (id = ANY(%s::int[]))
          AND fecha_propuesta >= %s AND fecha_propuesta < %s;
        """,
        (list(ESTADOS_QUE_OCUPAN), list(excluir), desde, hasta),
    )
    # Solo los inicios (llegan en UTC; choca compara instantes).
    ocupadas = [fila[0] for fila in cursor.fetchall()]
    # La rejilla, una sola vez para todos los días.
    horas_rejilla = rejilla(reglas)
    # El resultado, día a día.
    resultado: dict[date, dict[str, list[str]]] = {}
    # Cada día pedido (sin repetir: set).
    for dia in sorted(set(dias)):
        # Las dos franjas, vacías al principio.
        libres: dict[str, list[str]] = {nombre: [] for nombre, _, _ in FRANJAS}
        # Sábado (5) o domingo (6), o (D45) antes del primer día permitido:
        # sin horas.
        if dia.weekday() < 5 and (primer_dia is None or dia >= primer_dia):
            # Cada franja con sus minutos de la rejilla.
            for nombre, minutos in horas_rejilla.items():
                # Cada hora de la rejilla.
                for minuto in minutos:
                    # Ese minuto de ese día, en Madrid (con el desfase de ESE día).
                    candidato = datetime.combine(dia, time(minuto // 60, minuto % 60), tzinfo=ZONA_MADRID)
                    # En el futuro y sin chocar con ninguna ocupada: libre.
                    if candidato > ahora and not any(
                            choca(candidato, o, reglas[CLAVE_DURACION], reglas[CLAVE_DESPLAZAMIENTO])
                            for o in ocupadas):
                        # Como texto "HH:MM".
                        libres[nombre].append(_hhmm(minuto))
        # Las horas libres de ese día.
        resultado[dia] = libres
    # Todos los días.
    return resultado


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
    D31/D32: lanza HuecoOcupado (409) si la hora pedida no está entre las
    libres de su día según disponibilidad(). Si lo está, no hace nada.

    Se llama DESPUÉS de validar_fecha (la hora ya es futura, de lunes a
    viernes y de la rejilla), así que "no está entre las libres" significa
    exactamente "choca con otra visita activa".

    excluir: ids de visitas que NO cuentan porque son de la propia
    oportunidad y se sustituyen o se confirman en esta misma transacción (la
    sustitución de /visits, confirmar_hora y otra_hora: C8 de D31).

    Se llama con el cursor de quien llama, DESPUÉS de bloquear su
    oportunidad (FOR UPDATE) y como última comprobación antes de escribir.

    Contra dos peticiones a la vez (P15 de D31): antes de mirar, toma un
    bloqueo de Postgres para ESE día (pg_advisory_xact_lock), que dura hasta
    el final de la transacción. La segunda petición del mismo día espera
    aquí a que la primera termine; su consulta de después ve la visita ya
    confirmada (READ COMMITTED) y da 409. Solo protege a quien pasa por
    aquí: por eso es el ÚNICO camino de escritura (R6 de D31).
    """
    # El día de la visita en Madrid (el de la franja).
    dia = inicio.astimezone(ZONA_MADRID).date()
    # SQL: el bloqueo del día. Dos enteros: el del proyecto y el día como
    # AAAAMMDD (20261019). Se libera solo al terminar la transacción.
    cursor.execute("SELECT pg_advisory_xact_lock(%s, %s);",
                   (CLAVE_BLOQUEO_VISITAS, dia.year * 10000 + dia.month * 100 + dia.day))
    # Las horas libres de ese día, con la función ÚNICA (plan D32, 2.5).
    por_franja = disponibilidad(cursor, [dia], ahora, reglas, excluir)[dia]
    # Aplanadas en orden (mañana y tarde): el formato de huecos_libres de
    # siempre (P3 del plan D32: no cambia).
    libres = [hora for nombre, _, _ in FRANJAS for hora in por_franja[nombre]]
    # La hora pedida, como texto "HH:MM" de Madrid.
    pedida = inicio.astimezone(ZONA_MADRID).strftime("%H:%M")
    # Si no está entre las libres: 409 con el mensaje y la lista.
    if pedida not in libres:
        # La misma excepción de D31.
        raise HuecoOcupado("hueco_ocupado", mensaje_hueco_ocupado(libres), libres)
