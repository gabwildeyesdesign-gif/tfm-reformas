"""
Servicio de la disponibilidad de visitas AL OFRECER (D32, plan
docs/Plan_Horario_Visitas_D32.txt, secciones 3 y 11): GET /huecos-libres
(formulario de n8n, con X-Gate-Secret) y la tool MCP consultar_huecos_libres
(Agente 2) llaman a estas dos funciones:

  - consultar_dias(canal): los próximos dias_oferta_visita (10) días
    laborables, desde el PRIMER DÍA PERMITIDO del canal (D45; antes, desde
    HOY, P4), que tengan al menos una hora libre, cada uno con sus horas
    por franja. Si hay días completos, se busca más allá, hasta
    MAX_DIAS_BUSQUEDA (60) laborables (P6 = A). Los días completos no salen.
  - consultar_dia(fecha, canal): las horas libres de esa fecha, por franja,
    con el motivo si no tiene ninguna (P7: fecha_pasada, fin_de_semana o
    dia_completo; D45: antelacion_insuficiente; siempre 200).

D45 (2026-10-23, plan docs/Plan_Antelacion_Visitas_D45.txt): canal es
OBLIGATORIO y lo pone el ADAPTADOR, nunca la petición: GET /huecos-libres
pasa CANAL_ADMINISTRACION (1.er laborable después de hoy) y la tool MCP,
CANAL_CHAT (2.º laborable). Sin valor por defecto: olvidarlo es un TypeError.

Las horas las calcula disponibilidad() de reglas_visita.py, la MISMA función
que usa el 409 hueco_ocupado de POST /visits, POST /resultados-llamada y el
alias (plan, 2.5): aquí no se calcula ninguna hora. Por eso cada hora
ofrecida pasa la validación de esos endpoints (T1 del plan).

Solo lectura: SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY
como primera orden. Si falta o no es válida una regla, ConfiguracionIncompleta
(el adaptador da 503 SIN log, con [AVISO]). Nunca datos de otras visitas:
disponibilidad() solo lee fecha_propuesta, y las respuestas solo llevan
horas libres.

Como todo services/, este archivo NO importa fastapi ni fastmcp.
"""

# date: un día; datetime: un instante; time: una hora; timedelta: duraciones.
from datetime import date, datetime, time, timedelta

# La conexión de solo lectura del pool.
from app.db.connection import get_db_connection

# Los modelos de la respuesta.
from app.schemas.huecos_libres import DiaConHuecosFormulario, HuecosDiaResponse, HuecosDiasResponse

# De reglas_visita.py: la zona, las claves de días de oferta y del tope de
# búsqueda, las franjas, la lectura de las reglas, la función ÚNICA de
# disponibilidad y la excepción del 503.
from app.services.reglas_visita import (
    CLAVE_DIAS_OFERTA,
    # D45: los días de la semana (mudados a reglas_visita.py; el nombre
    # sigue existiendo en este módulo por esta importación).
    DIAS_SEMANA,
    FRANJAS,
    MAX_DIAS_BUSQUEDA,
    ZONA_MADRID,
    ConfiguracionIncompleta,
    disponibilidad,
    leer_reglas_visita,
    # D45: la primera fecha posible de cada canal.
    primer_dia_del_canal,
    rejilla_texto,
)

# El modo de la transacción: la PRIMERA orden (mismo texto que el listado y
# get_business_rules).
SQL_MODO_TRANSACCION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;"

# Mensaje del 503 (llega a quien mire el error en n8n, nunca al cliente).
MENSAJE_CONFIGURACION = (
    "No se pueden calcular las horas libres de visita: falta o no es válida una "
    "fila de reglas_negocio (ver 'faltan'). No se ha escrito nada."
)


# Lo común de los dos modos: el modo, el reloj y las reglas.
def _leer_ahora_y_reglas(cursor) -> tuple[datetime, dict[str, int]]:
    """
    Dentro de la transacción de quien llama: el modo (primera orden), el
    reloj de Postgres y las reglas de visita. ConfiguracionIncompleta si
    alguna falta o no es válida.
    """
    # 1. PRIMERA orden: una sola foto y ninguna escritura posible.
    cursor.execute(SQL_MODO_TRANSACCION)
    # 2. El reloj de la base de datos (la misma referencia que POST /visits).
    cursor.execute("SELECT now();")
    # fetchone()[0]: la única columna de la única fila.
    ahora = cursor.fetchone()[0]
    # 3. Las reglas de visita, con la función de POST /visits.
    reglas, problemas = leer_reglas_visita(cursor)
    # Con algún problema: 503, sin escribir nada (es de solo lectura).
    if problemas:
        # La misma excepción que el 503 de los demás endpoints.
        raise ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIGURACION, problemas)
    # El reloj y las reglas.
    return ahora, reglas


def etiqueta_dia(dia: date, por_franja: dict[str, list[str]]) -> str:
    """
    El texto EXACTO de la opción del desplegable de n8n (plan, 3.3):
    "Jueves 15/10 — mañana: 09:45 · 11:00 — tarde: 18:15". Una franja sin
    horas no se escribe. Única por construcción: lleva la fecha.
    """
    # El día con mayúscula y la fecha dd/mm.
    texto = f"{DIAS_SEMANA[dia.weekday()].capitalize()} {dia:%d/%m}"
    # Cada franja con horas: " — mañana: 09:45 · 11:00".
    for nombre, _, _ in FRANJAS:
        # Solo si tiene alguna hora.
        if por_franja[nombre]:
            # "mañana" con eñe en el texto (la clave del JSON va sin ella).
            texto += f" — {'mañana' if nombre == 'manana' else nombre}: {' · '.join(por_franja[nombre])}"
    # La etiqueta completa.
    return texto


def consultar_dias(canal: str) -> HuecosDiasResponse:
    """
    Modo días: los próximos dias_oferta_visita días laborables, desde el
    primer día permitido del canal (D45), con al menos una hora libre. UNA
    consulta de visitas para toda la ventana de búsqueda (la hace
    disponibilidad()).
    """
    # Conexión de solo lectura: al salir del "with" hace rollback y vuelve al pool.
    with get_db_connection() as conn:
        # El cursor se cierra siempre al salir de este bloque.
        with conn.cursor() as cursor:
            # El reloj y las reglas (503 si falta alguna).
            ahora, reglas = _leer_ahora_y_reglas(cursor)
            # D45: el primer día permitido del canal (hoy, en Madrid, más
            # los laborables de su regla).
            primero = primer_dia_del_canal(ahora, reglas, canal)
            # Los candidatos: los MAX_DIAS_BUSQUEDA primeros laborables desde
            # ese día (incluido; antes de D45, desde hoy). Como mucho 7/5 de
            # días naturales por laborable.
            laborables = [d for d in (primero + timedelta(days=i) for i in range(MAX_DIAS_BUSQUEDA * 7 // 5 + 7))
                          if d.weekday() < 5][:MAX_DIAS_BUSQUEDA]
            # Las horas libres de todos, con la función ÚNICA (una consulta);
            # con el primer día también ahí, por si la regla es 0 (hoy).
            libres = disponibilidad(cursor, laborables, ahora, reglas, primer_dia=primero)
    # Los días con alguna hora, en orden, hasta dias_oferta_visita.
    dias = []
    # Cada laborable, en orden.
    for dia in laborables:
        # Sus horas por franja.
        por_franja = libres[dia]
        # Cuántas tiene.
        total = sum(len(por_franja[nombre]) for nombre, _, _ in FRANJAS)
        # Un día completo (o un hoy sin horas futuras) no sale.
        if total == 0:
            # Al siguiente.
            continue
        # El día, con su etiqueta del desplegable.
        dias.append(DiaConHuecosFormulario(
            fecha=dia, dia_semana=DIAS_SEMANA[dia.weekday()], manana=por_franja["manana"],
            tarde=por_franja["tarde"], num_horas_libres=total, etiqueta=etiqueta_dia(dia, por_franja)))
        # Con los dias_oferta_visita que se piden, basta.
        if len(dias) == reglas[CLAVE_DIAS_OFERTA]:
            # Fin de la búsqueda.
            break
    # La respuesta, con la hora del cálculo en Madrid.
    return HuecosDiasResponse(modo="dias", generado_en=ahora.astimezone(ZONA_MADRID), dias=dias)


def motivo_sin_horas(fecha: date, ahora: datetime, reglas: dict[str, int], primer_dia: date) -> str:
    """
    Por qué un día no tiene ninguna hora libre (solo se llama si no tiene
    ninguna). Función PURA (sin base de datos): se prueba con fechas fijas
    (V5 del plan D45). Orden, el mismo que validar_fecha: fecha_pasada ->
    antelacion_insuficiente (D45) -> fin_de_semana -> dia_completo.
    """
    # Hoy, en Madrid.
    hoy = ahora.astimezone(ZONA_MADRID).date()
    # La última hora de la rejilla de ese día (para saber si ya pasaron todas).
    ultima = max((h for lista in rejilla_texto(reglas).values() for h in lista), default="00:00")
    # Ese instante en Madrid.
    ultima_inicio = datetime.combine(fecha, time(int(ultima[:2]), int(ultima[3:])), tzinfo=ZONA_MADRID)
    # Un día anterior a hoy, o un hoy en el que ya pasaron todas las horas.
    if fecha < hoy or ultima_inicio <= ahora:
        # Ya ha pasado.
        return "fecha_pasada"
    # D45: antes del primer día permitido del canal.
    if fecha < primer_dia:
        # Falta antelación.
        return "antelacion_insuficiente"
    # Sábado o domingo.
    if fecha.weekday() >= 5:
        # Fin de semana.
        return "fin_de_semana"
    # Un laborable permitido sin horas: alguien las ha cogido todas.
    return "dia_completo"


def consultar_dia(fecha: date, canal: str) -> HuecosDiaResponse:
    """
    Modo un día: las horas libres de esa fecha, recalculadas ahora, y si no
    hay ninguna, por qué (fecha_pasada, antelacion_insuficiente,
    fin_de_semana o dia_completo).
    """
    # Conexión de solo lectura.
    with get_db_connection() as conn:
        # El cursor se cierra siempre.
        with conn.cursor() as cursor:
            # El reloj y las reglas (503 si falta alguna).
            ahora, reglas = _leer_ahora_y_reglas(cursor)
            # D45: el primer día permitido del canal.
            primero = primer_dia_del_canal(ahora, reglas, canal)
            # Las horas libres de ese día, con la función ÚNICA y la antelación.
            por_franja = disponibilidad(cursor, [fecha], ahora, reglas, primer_dia=primero)[fecha]
    # Las dos franjas juntas, en orden: las opciones de radio.
    horas = [hora for nombre, _, _ in FRANJAS for hora in por_franja[nombre]]
    # Sin motivo si hay horas; si no, el de la función pura.
    motivo = None if horas else motivo_sin_horas(fecha, ahora, reglas, primero)
    # La respuesta.
    return HuecosDiaResponse(
        modo="dia", generado_en=ahora.astimezone(ZONA_MADRID), fecha=fecha,
        dia_semana=DIAS_SEMANA[fecha.weekday()], manana=por_franja["manana"], tarde=por_franja["tarde"],
        horas=horas, sin_horas_motivo=motivo)
