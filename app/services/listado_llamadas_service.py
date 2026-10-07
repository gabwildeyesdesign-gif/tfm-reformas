"""
Lógica de negocio de GET /llamadas-del-dia: la lista diaria de llamadas
que WF3 envía a administración (plan: docs/Plan_Endpoint_Listado_WF3.txt).

Cinco apartados (plan, 1.6):
  a) gates_sin_decision         Gate sin decisión registrada, presupuesto
                                de hace más de horas_recordatorio_gate h.
  b) seguimientos_por_abrir     presupuesto_enviado, sin visita (salvo
                                canceladas), sin contacto registrado y
                                presupuesto de hace más de
                                horas_seguimiento_presupuesto h.
  c) seguimientos_abiertos      seguimiento_pendiente.
  d) visitas_sin_confirmar      visitas 'solicitada' que NO son del
                                próximo día laborable.
  e) visitas_proximo_laborable  visitas 'solicitada' o 'confirmada' del
                                próximo día laborable.

SOLO LECTURA (plan, 1.9), con tres barreras:
  1. get_db_connection(), que termina con rollback;
  2. la PRIMERA orden de la transacción es SET TRANSACTION ISOLATION LEVEL
     REPEATABLE READ, READ ONLY: el propio Postgres rechaza cualquier
     escritura, y las cinco consultas ven la misma foto de los datos;
  3. ningún FOR UPDATE.
No escribe nada, tampoco en logs, ni siquiera con la configuración rota
(503 sin log, P3 del plan).

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando
falta una regla, lanza ConfiguracionIncompleta (la de reglas_visita.py);
el adaptador app/api/listado_llamadas.py la convierte en un 503.
"""

# date (un día), datetime (un instante), time (una hora del día) y
# timedelta (una duración, para sumar días).
from datetime import date, datetime, time, timedelta

# Decimal: reglas_negocio.valor llega de Postgres como Decimal ('48.00').
from decimal import Decimal

# La conexión de SOLO LECTURA del pool (termina con rollback).
from app.db.connection import get_db_connection

# El esquema de la respuesta y sus piezas (app/schemas/listado_llamadas.py).
from app.schemas.aviso_gate import OrigenContacto
from app.schemas.listado_llamadas import (
    ContactoLlamada,
    ListadoLlamadasResponse,
    LlamadaOportunidad,
    LlamadaVisita,
    MotivoLlamada,
    ReglasListado,
)

# ZONA_MADRID: la zona Europe/Madrid, con el cambio de hora.
# ConfiguracionIncompleta: el rechazo de "falta una regla" (-> 503), el
# mismo que usan /visits y /gate-decisions, con su lista "faltan".
from app.services.reglas_visita import ZONA_MADRID, ConfiguracionIncompleta

# ======================================================================
# Constantes
# ======================================================================

# Claves de reglas_negocio de los dos plazos (paso10 y paso11). Las
# funciones las leen en el momento de ejecutarse, así que una prueba puede
# cambiar el nombre EN MEMORIA para provocar el 503 sin tocar la tabla
# (como check_visits_service).
CLAVE_RECORDATORIO_GATE = "horas_recordatorio_gate"
CLAVE_SEGUIMIENTO = "horas_seguimiento_presupuesto"

# Estados de la oportunidad de cada apartado (columna oportunidades.estado).
ESTADO_GATE = "pendiente_aprobacion"
ESTADO_POR_ABRIR = "presupuesto_enviado"
ESTADO_ABIERTO = "seguimiento_pendiente"

# Estados de visita (columna visitas.estado).
VISITA_SOLICITADA = "solicitada"
VISITA_CONFIRMADA = "confirmada"
VISITA_CANCELADA = "cancelada"

# El modo de la transacción: la PRIMERA orden de obtener_listado (plan,
# 1.9 y 3.4). En una constante para que la prueba pueda comprobar el texto.
SQL_MODO_TRANSACCION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;"

# Mensaje del 503 (para administración, a través de n8n).
MENSAJE_CONFIGURACION = (
    "No se puede preparar la lista de llamadas: falta o no es válida una regla "
    "de reglas_negocio (ver 'faltan'). No se ha escrito nada."
)


# ======================================================================
# Funciones puras (sin base de datos): se prueban directamente
# ======================================================================


def validar_reglas_listado(leidas: dict[str, Decimal]) -> tuple[dict[str, int], list[str]]:
    """
    Comprueba las dos reglas de plazo y devuelve (reglas, problemas).

    leidas:    clave -> valor, tal como llegan de reglas_negocio.
    reglas:    clave -> horas como int, solo de las válidas.
    problemas: lista de textos, vacía si todo está bien. No se adivina
               ningún valor (P3 del plan: nada de 24 o 48 por defecto).

    Una regla es válida si existe y su valor es un ENTERO mayor que 0, sin
    máximo (P6): 24.50 o 0 son problemas.
    """
    # Las claves se toman de las constantes AHORA (no al cargar el archivo),
    # para que un cambio en memoria de una prueba se note aquí.
    claves = (CLAVE_RECORDATORIO_GATE, CLAVE_SEGUIMIENTO)
    # Los dos resultados empiezan vacíos y se rellenan en el bucle.
    reglas: dict[str, int] = {}
    problemas: list[str] = []
    # Se recorren las claves ESPERADAS (no las leídas), para detectar las
    # que faltan.
    for clave in claves:
        # .get(clave) devuelve None si la clave no se leyó.
        valor = leidas.get(clave)
        # La fila no existe.
        if valor is None:
            problemas.append(f"{clave}: no existe")
        # Existe pero tiene decimales (to_integral_value() redondea al
        # entero: si cambia, los tenía) o no es mayor que 0.
        elif valor != valor.to_integral_value() or valor <= 0:
            problemas.append(f"{clave}: valor no válido ({valor}); debe ser un entero mayor que 0")
        # Válida: se guarda como int (llegaba como Decimal('48.00')).
        else:
            reglas[clave] = int(valor)
    # Quien llama decide qué hacer si hay problemas.
    return reglas, problemas


def siguiente_laborable(hoy: date) -> date:
    """
    El siguiente día de lunes a viernes DESPUÉS de hoy (plan, 3.3): el
    viernes da el lunes, y el sábado y el domingo también el lunes.
    Festivos: no se tienen en cuenta (Adenda 5.4).
    """
    # Se empieza por mañana.
    dia = hoy + timedelta(days=1)
    # weekday(): lunes = 0 ... sábado = 5, domingo = 6. Mientras caiga en
    # fin de semana, se pasa al día siguiente.
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return dia


def limites_dia_madrid(dia: date) -> tuple[datetime, datetime]:
    """
    El intervalo [inicio, fin) de ese día en hora de Madrid: desde las
    00:00 de ese día hasta las 00:00 del siguiente (el fin NO se incluye).

    datetime.combine con tzinfo=ZONA_MADRID calcula el desfase correcto de
    cada extremo, así que un día con cambio de hora mide 23 o 25 horas, y
    el intervalo sale bien solo (plan, 3.3).
    """
    # Medianoche del día pedido, en Madrid.
    inicio = datetime.combine(dia, time(0, 0), tzinfo=ZONA_MADRID)
    # Medianoche del día siguiente, en Madrid (calculada desde la fecha,
    # no sumando 24 horas al inicio, que fallaría el día del cambio).
    fin = datetime.combine(dia + timedelta(days=1), time(0, 0), tzinfo=ZONA_MADRID)
    return inicio, fin


def contacto_de(tiene_contacto: bool, nombre: str | None, telefono: str | None) -> ContactoLlamada:
    """
    Contacto de un elemento, con la regla de GET /gate-avisos (P1 de su
    plan): el de la solicitud si el lead tiene la clave 'contacto'; si no,
    nombre y teléfono a None con origen no_disponible. Nunca la ficha de
    clientes (que ni siquiera se lee).
    """
    # El lead tiene la clave: sus datos, con origen "solicitud".
    if tiene_contacto:
        return ContactoLlamada(nombre=nombre, telefono=telefono, origen=OrigenContacto.SOLICITUD)
    # Lead anterior a la clave: los dos a None, con origen "no_disponible".
    return ContactoLlamada(nombre=None, telefono=None, origen=OrigenContacto.NO_DISPONIBLE)


# ======================================================================
# Las consultas de los apartados (con el cursor de quien llama)
# ======================================================================

# Columnas y tablas comunes de los apartados a), b) y c). Son textos FIJOS
# del programa (ningún dato de fuera se pega en ellos); los valores van
# siempre como parámetros %(...)s.
#   - "?" pregunta si el JSONB TIENE la clave 'contacto' (true/false).
#   - "->" saca el objeto 'contacto' y "->>" un valor como texto. NO se
#     lee el email del contacto: ni siquiera viaja desde Postgres.
#   - Sin JOIN con clientes: la ficha no se lee nunca.
#   - De presupuestos solo se lee created_at: ningún importe.
SQL_OPORTUNIDADES = """
    SELECT o.id,
           o.tipo_reforma,
           l.datos_estructurados ? 'contacto',
           l.datos_estructurados -> 'contacto' ->> 'nombre',
           l.datos_estructurados -> 'contacto' ->> 'telefono',
           p.created_at
    FROM oportunidades o
    JOIN presupuestos p ON p.oportunidad_id = o.id
    JOIN leads l        ON l.id = o.lead_id
"""

# Lo mismo para d) y e), partiendo de visitas. No se lee texto_cliente ni
# el id de la visita (minimización).
SQL_VISITAS = """
    SELECT v.oportunidad_id,
           o.tipo_reforma,
           l.datos_estructurados ? 'contacto',
           l.datos_estructurados -> 'contacto' ->> 'nombre',
           l.datos_estructurados -> 'contacto' ->> 'telefono',
           v.estado,
           v.fecha_propuesta,
           v.created_at
    FROM visitas v
    JOIN oportunidades o ON o.id = v.oportunidad_id
    JOIN leads l         ON l.id = o.lead_id
"""

# El WHERE y el ORDER BY de cada apartado (plan, 1.6 y sección 4).
#   make_interval(hours => %(horas)s): una duración de esas horas.
#   "<" y no "<=": límite ESTRICTO (D25.15): con exactamente 24 h no entra.
#   %(ahora)s: el now() de Postgres que obtener_listado leyó en ESTA
#     transacción (el mismo para todos los apartados).
WHERE_GATES = """
    WHERE o.estado = %(estado)s
      AND p.created_at < %(ahora)s - make_interval(hours => %(horas)s)
    ORDER BY p.created_at, o.id;
"""
# b): además, sin contacto registrado y sin ninguna visita que no esté
# cancelada (P5). NOT EXISTS: "no hay ninguna fila que cumpla esto".
WHERE_POR_ABRIR = """
    WHERE o.estado = %(estado)s
      AND o.fecha_ultimo_contacto IS NULL
      AND p.created_at < %(ahora)s - make_interval(hours => %(horas)s)
      AND NOT EXISTS (SELECT 1 FROM visitas v
                      WHERE v.oportunidad_id = o.id
                        AND v.estado <> %(cancelada)s)
    ORDER BY p.created_at, o.id;
"""
# c): sin plazo (ya pasó al abrirse el seguimiento).
WHERE_ABIERTOS = """
    WHERE o.estado = %(estado)s
    ORDER BY p.created_at, o.id;
"""
# d): solicitadas que NO caen en el próximo laborable (esas van en e),
# P4: sin repeticiones).
WHERE_SIN_CONFIRMAR = """
    WHERE v.estado = %(solicitada)s
      AND NOT (v.fecha_propuesta >= %(inicio)s AND v.fecha_propuesta < %(fin)s)
    ORDER BY v.created_at, v.id;
"""
# e): solicitadas o confirmadas dentro de [inicio, fin) del próximo
# laborable. "= ANY(%(activas)s)": igual a alguno de la lista.
WHERE_PROXIMO_LABORABLE = """
    WHERE v.estado = ANY(%(activas)s)
      AND v.fecha_propuesta >= %(inicio)s AND v.fecha_propuesta < %(fin)s
    ORDER BY v.fecha_propuesta, v.id;
"""


def _llamadas_oportunidad(cursor, where: str, params: dict, motivo: MotivoLlamada) -> list[LlamadaOportunidad]:
    """Ejecuta un apartado de oportunidades (a, b o c) y construye sus elementos."""
    # SQL: las columnas comunes + el WHERE/ORDER BY del apartado. Los dos
    # trozos son textos fijos de este archivo; los valores van en params.
    cursor.execute(SQL_OPORTUNIDADES + where, params)
    # Una LlamadaOportunidad por fila. El for desempaqueta cada fila en
    # una variable por columna, en el orden del SELECT.
    return [
        LlamadaOportunidad(
            oportunidad_id=oportunidad_id,
            motivo=motivo,
            tipo_reforma=tipo_reforma,
            contacto=contacto_de(tiene_contacto, nombre, telefono),
            # Postgres la entrega en UTC; se escribe en hora de Madrid.
            fecha_presupuesto=fecha_presupuesto.astimezone(ZONA_MADRID),
        )
        for oportunidad_id, tipo_reforma, tiene_contacto, nombre, telefono, fecha_presupuesto in cursor.fetchall()
    ]


def _llamadas_visita(cursor, where: str, params: dict, motivo: MotivoLlamada) -> list[LlamadaVisita]:
    """Ejecuta un apartado de visitas (d o e) y construye sus elementos."""
    # SQL: las columnas comunes de visitas + el WHERE/ORDER BY del apartado.
    cursor.execute(SQL_VISITAS + where, params)
    # Una LlamadaVisita por fila, con las dos fechas en hora de Madrid.
    return [
        LlamadaVisita(
            oportunidad_id=oportunidad_id,
            motivo=motivo,
            tipo_reforma=tipo_reforma,
            contacto=contacto_de(tiene_contacto, nombre, telefono),
            estado_visita=estado_visita,
            fecha_visita=fecha_visita.astimezone(ZONA_MADRID),
            fecha_solicitud_visita=fecha_solicitud.astimezone(ZONA_MADRID),
        )
        for (oportunidad_id, tipo_reforma, tiene_contacto, nombre, telefono,
             estado_visita, fecha_visita, fecha_solicitud) in cursor.fetchall()
    ]


def leer_listado(cursor, reglas: dict[str, int], ahora: datetime) -> ListadoLlamadasResponse:
    """
    Las cinco consultas, con el cursor de quien llama y DENTRO de su
    transacción (no abre ni cierra nada).

    reglas: las dos horas ya validadas (validar_reglas_listado).
    ahora:  el now() de Postgres de ESA transacción.

    Recibe el cursor para que la prueba del límite exacto (plan, 6.2, S5)
    pueda ejecutarla dentro de una transacción suya que termina en
    ROLLBACK, con el mismo now().
    """
    # El próximo laborable se calcula desde HOY en Madrid (no en UTC: a
    # las 00:30 de Madrid en verano, en UTC todavía es ayer).
    dia = siguiente_laborable(ahora.astimezone(ZONA_MADRID).date())
    # Sus límites [inicio, fin) en Madrid.
    inicio, fin = limites_dia_madrid(dia)

    # a) Gates sin decisión registrada.
    gates = _llamadas_oportunidad(
        cursor,
        WHERE_GATES,
        {"estado": ESTADO_GATE, "ahora": ahora, "horas": reglas[CLAVE_RECORDATORIO_GATE]},
        MotivoLlamada.GATE_SIN_DECISION,
    )
    # b) Seguimientos por abrir.
    por_abrir = _llamadas_oportunidad(
        cursor,
        WHERE_POR_ABRIR,
        {"estado": ESTADO_POR_ABRIR, "ahora": ahora, "horas": reglas[CLAVE_SEGUIMIENTO],
         "cancelada": VISITA_CANCELADA},
        MotivoLlamada.SEGUIMIENTO_POR_ABRIR,
    )
    # c) Seguimientos abiertos.
    abiertos = _llamadas_oportunidad(
        cursor, WHERE_ABIERTOS, {"estado": ESTADO_ABIERTO}, MotivoLlamada.SEGUIMIENTO_ABIERTO
    )
    # d) Visitas sin confirmar (salvo las del próximo laborable).
    sin_confirmar = _llamadas_visita(
        cursor,
        WHERE_SIN_CONFIRMAR,
        {"solicitada": VISITA_SOLICITADA, "inicio": inicio, "fin": fin},
        MotivoLlamada.VISITA_SIN_CONFIRMAR,
    )
    # e) Visitas del próximo laborable. psycopg2 convierte la lista de
    # Python en un array de Postgres para el "= ANY".
    proximo = _llamadas_visita(
        cursor,
        WHERE_PROXIMO_LABORABLE,
        {"activas": [VISITA_SOLICITADA, VISITA_CONFIRMADA], "inicio": inicio, "fin": fin},
        MotivoLlamada.VISITA_PROXIMO_LABORABLE,
    )

    # La respuesta completa. Pydantic comprueba cada campo con el esquema,
    # y extra="forbid" impide añadir ninguno que no esté declarado.
    return ListadoLlamadasResponse(
        generado_en=ahora.astimezone(ZONA_MADRID),
        dia_visitas=dia,
        reglas=ReglasListado(
            horas_recordatorio_gate=reglas[CLAVE_RECORDATORIO_GATE],
            horas_seguimiento_presupuesto=reglas[CLAVE_SEGUIMIENTO],
        ),
        gates_sin_decision=gates,
        seguimientos_por_abrir=por_abrir,
        seguimientos_abiertos=abiertos,
        visitas_sin_confirmar=sin_confirmar,
        visitas_proximo_laborable=proximo,
    )


# ======================================================================
# La operación completa
# ======================================================================


def obtener_listado() -> ListadoLlamadasResponse:
    """
    Devuelve la lista de llamadas de este momento.

    Orden (plan, 1.8): modo de la transacción -> reloj y reglas (503 si
    falta alguna, ANTES de leer ningún dato personal) -> los cinco
    apartados.

    Excepción que puede lanzar (el adaptador la traduce a HTTP):
      ConfiguracionIncompleta  503  configuracion_incompleta (sin log)
    """
    # get_db_connection(): conexión de solo lectura; al salir del "with"
    # hace rollback y la devuelve al pool, también si hay una excepción.
    with get_db_connection() as conn:
        # El cursor envía las consultas y lee sus resultados.
        cursor = conn.cursor()

        # 1. PRIMERA orden de la transacción: REPEATABLE READ (una sola
        #    foto para las cinco consultas) y READ ONLY (Postgres rechaza
        #    cualquier escritura). Tiene que ir antes de cualquier otra
        #    consulta; si no, Postgres da error.
        cursor.execute(SQL_MODO_TRANSACCION)

        # 2. El reloj: now() de Postgres. Dentro de la transacción no
        #    cambia, así que es el mismo "ahora" para todos los plazos.
        cursor.execute("SELECT now();")
        # fetchone()[0]: la única columna de la única fila.
        ahora = cursor.fetchone()[0]

        # 3. Las dos reglas. SQL: clave y valor de las filas cuya clave está
        #    en la lista; "= ANY(%s)" es "igual a alguno de la lista".
        cursor.execute(
            "SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);",
            ([CLAVE_RECORDATORIO_GATE, CLAVE_SEGUIMIENTO],),
        )
        # dict(...) sobre pares (clave, valor) crea el diccionario.
        reglas, problemas = validar_reglas_listado(dict(cursor.fetchall()))
        # Con algún problema: 503, SIN escribir ningún log (P3). Lanzarla
        # dentro del "with" es seguro: no hay nada que confirmar.
        if problemas:
            raise ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIGURACION, problemas)

        # 4. Los cinco apartados, en la misma transacción.
        listado = leer_listado(cursor, reglas, ahora)
        # Se cierra el cursor; la conexión la devuelve el "with" al pool.
        cursor.close()

    return listado
