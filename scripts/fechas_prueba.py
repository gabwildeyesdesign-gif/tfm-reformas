"""
Fechas propias de los scripts de verificación que crean visitas (Bloque
B5-0 del plan del Agente 2, docs/Plan_Agente2_Calendar.txt, P21 y C7).

Por qué existe: con D31, POST /visits, POST /resultados-llamada y su alias
POST /gate-decisions rechazan (409 hueco_ocupado) una visita que se solape
con OTRA visita activa ('solicitada' o 'confirmada'), sea de quien sea.
Antes, los scripts pedían sus visitas "el próximo lunes" a horas fijas: dos
scripts (o un script frente a una visita real de Gabi) podían pedir el mismo
hueco y fallar según el orden.

Lo que da este módulo:
  - Un BLOQUE de semanas lejanas para cada script, sin solaparse con los de
    los demás: el script i usa las semanas SEMANA_INICIAL + 4*i a
    SEMANA_INICIAL + 4*i + 3 (contadas desde la semana de hoy).
  - Dias: los días laborables de ese bloque, uno detrás de otro, para que
    cada oportunidad de un script tenga su propio día.
  - exigir_libres: ANTES de crear nada, comprueba que en los tramos que el
    script va a usar no hay ninguna visita activa AJENA. Si la hay, FALLO
    con la explicación y el script se para (código 1). NUNCA borra nada:
    una visita ajena no se toca (regla de borrados de CLAUDE.md).

No es código de producción: es un módulo de scripts/ que importan los
scripts con "import fechas_prueba" (Python busca primero en la carpeta del
script que se ejecuta).
"""

# sys: para parar el script con código 1 si un hueco está ocupado.
import sys
# date, datetime, time y timedelta: días, instantes y diferencias.
from datetime import date, datetime, time, timedelta
# La zona de Madrid (con su cambio de hora).
from zoneinfo import ZoneInfo

# psycopg2: la conexión propia, de solo lectura, de la comprobación.
import psycopg2

# La cadena de conexión del .env (el script ya ha puesto la raíz del
# repositorio en sys.path antes de importar este módulo).
from app.config import DATABASE_URL

# La zona horaria de las visitas.
MADRID = ZoneInfo("Europe/Madrid")

# La primera semana de los bloques, contada desde la semana de hoy: lejos de
# las visitas reales, que se piden para los próximos días (P21).
SEMANA_INICIAL = 30
# Semanas de cada bloque: 20 días laborables, más de los que usa el script
# que más días necesita (check_resultados_llamada_http, 11).
SEMANAS_POR_SCRIPT = 4

# Los scripts con bloque propio, en orden: la posición da el bloque. Un
# script nuevo se añade AL FINAL, para no mover los bloques de los demás.
SCRIPTS = (
    "check_visits_http",
    "check_gate_decisions_http",
    "check_resultados_llamada_http",
    "check_resultados_llamada_service",
    "check_ficha_oportunidad_http",
    "check_followup_tasks_http",
    "check_disponibilidad_service",
)

# Los estados de visita que ocupan un hueco (D31).
ESTADOS_ACTIVOS = ("solicitada", "confirmada")


def lunes_de_prueba(script: str, hoy: date) -> date:
    """El primer lunes del bloque del script."""
    # El lunes de la semana de hoy (weekday(): lunes = 0).
    lunes_de_hoy = hoy - timedelta(days=hoy.weekday())
    # index da la posición del script en SCRIPTS (ValueError si no está).
    semanas = SEMANA_INICIAL + SEMANAS_POR_SCRIPT * SCRIPTS.index(script)
    # Ese número de semanas después.
    return lunes_de_hoy + timedelta(weeks=semanas)


def tramo_del_bloque(script: str, hoy: date) -> tuple[datetime, datetime]:
    """El bloque entero del script, de su primer lunes a las 00:00 al lunes siguiente al bloque."""
    # El primer día del bloque.
    lunes = lunes_de_prueba(script, hoy)
    # Desde las 00:00 de ese lunes hasta las 00:00 del día siguiente al bloque.
    return (datetime.combine(lunes, time(0, 0), tzinfo=MADRID),
            datetime.combine(lunes + timedelta(weeks=SEMANAS_POR_SCRIPT), time(0, 0), tzinfo=MADRID))


class Dias:
    """Los días laborables del bloque de un script, uno detrás de otro."""

    def __init__(self, script: str, hoy: date):
        # El nombre, para el mensaje de error.
        self.script = script
        # El primer lunes del bloque.
        self.lunes = lunes_de_prueba(script, hoy)
        # Cuántos días laborables se han dado ya.
        self.dados = 0

    def siguiente(self) -> date:
        """El siguiente día laborable del bloque; RuntimeError si se acaban."""
        # 5 laborables por semana: el máximo del bloque.
        if self.dados >= 5 * SEMANAS_POR_SCRIPT:
            # Un script que necesita más días tiene que pedir un bloque mayor.
            raise RuntimeError(f"{self.script}: se han acabado los días de su bloque de fechas")
        # Semana y día dentro de la semana (divmod: cociente y resto).
        semana, dia = divmod(self.dados, 5)
        # Se cuenta este.
        self.dados += 1
        # Lunes del bloque + semanas + días (de lunes a viernes).
        return self.lunes + timedelta(weeks=semana, days=dia)


def exigir_libres(script: str, patron_propio: str, tramos: list[tuple[datetime, datetime]]) -> None:
    """
    Comprueba, en SOLO LECTURA, que ninguna visita activa ajena ocupa los
    tramos (desde, hasta) que va a usar el script. Una visita ocupa
    [inicio, inicio + duracion_visita_min), así que se solapa con un tramo si
    empieza antes de su final y termina después de su principio.
    (Nota 2026-10-13, D32: desde paso14 una visita ocupa además
    desplazamiento_min tras su fin, y la de prueba también; los dos lados se
    alargan con duración + desplazamiento, como choca() de reglas_visita.)

    Ajena =la de un cliente cuyo email no cumple el patrón del script. Las
    propias (de una ejecución anterior cortada) no cuentan: las borra la
    limpieza del propio script.

    Si hay alguna ajena: FALLO con la explicación y sys.exit(1), sin borrar
    nada. Si no: una línea [INFO], que no cuenta como comprobación (los
    recuentos de los scripts no cambian).
    """
    # Conexión propia, de solo lectura (Postgres rechazaría cualquier escritura).
    cn = psycopg2.connect(DATABASE_URL)
    # set_session(readonly=True): cada transacción de esta conexión es READ ONLY.
    cn.set_session(readonly=True)
    # try / finally: la conexión se cierra siempre.
    try:
        # El cursor, cerrado al salir.
        with cn.cursor() as cur:
            # SQL: la duración de una visita, en minutos.
            cur.execute("SELECT valor FROM reglas_negocio WHERE clave = 'duracion_visita_min';")
            # La única fila (si faltara, el script no puede comprobar nada: se para).
            fila = cur.fetchone()
            # Sin la regla no se sabe cuánto ocupa una visita.
            if fila is None:
                # Se explica y se para.
                print(f"  [FALLO] {script}: falta la regla duracion_visita_min; no se pueden comprobar los huecos.")
                # Código 1, sin crear nada.
                sys.exit(1)
            # Minutos como entero.
            duracion = int(fila[0])
            # D32 (2026-10-13): y el desplazamiento hasta la siguiente visita.
            # Si la fila no existe (antes de paso14), 0: el choque de D31.
            cur.execute("SELECT valor FROM reglas_negocio WHERE clave = 'desplazamiento_min';")
            # La fila, o None.
            fila_despl = cur.fetchone()
            # Lo que ocupa una visita contando el desplazamiento (D32, choca()).
            duracion += int(fila_despl[0]) if fila_despl is not None else 0
            # Las visitas activas que tocan alguno de los tramos.
            encontradas = {}
            # Un tramo cada vez.
            for desde, hasta in tramos:
                # D32: una visita ajena que empiece poco DESPUÉS del tramo
                # también choca (la de prueba ocupa duración + desplazamiento
                # desde su inicio): por eso "hasta" se alarga lo mismo.
                # SQL: visitas activas que se solapan con el tramo, con su
                # cliente (LEFT JOIN: un lead puede no tener cliente) y si es
                # propia por el patrón del email.
                cur.execute(
                    """
                    SELECT v.id, v.fecha_propuesta, v.estado, coalesce(c.email LIKE %(patron)s, false)
                    FROM visitas v
                    JOIN oportunidades o ON o.id = v.oportunidad_id
                    JOIN leads l ON l.id = o.lead_id
                    LEFT JOIN clientes c ON c.id = l.cliente_id
                    WHERE v.estado = ANY(%(activos)s)
                      AND v.fecha_propuesta < %(hasta)s + make_interval(mins => %(dur)s)
                      AND v.fecha_propuesta + make_interval(mins => %(dur)s) > %(desde)s;
                    """,
                    {"patron": patron_propio, "activos": list(ESTADOS_ACTIVOS), "hasta": hasta, "desde": desde,
                     "dur": duracion},
                )
                # Se guardan por id (una visita puede tocar dos tramos).
                for vid, inicio, estado, propia in cur.fetchall():
                    # id -> (inicio, estado, propia).
                    encontradas[vid] = (inicio, estado, propia)
        # Se cierra la transacción de lectura.
        cn.rollback()
    # La conexión se cierra siempre.
    finally:
        # Cerrada.
        cn.close()
    # Las ajenas, ordenadas por fecha.
    ajenas = sorted((v for v in encontradas.items() if not v[1][2]), key=lambda v: v[1][0])
    # Las propias, solo para la línea de información.
    propias = len(encontradas) - len(ajenas)
    # Alguna ajena: FALLO con explicación y se para sin crear ni borrar nada.
    if ajenas:
        # Una línea por visita ajena: id, fecha en Madrid y estado.
        for vid, (inicio, estado, _) in ajenas:
            # La explicación.
            print(f"  [FALLO] {script}: el hueco de prueba lo ocupa la visita AJENA {vid} "
                  f"({inicio.astimezone(MADRID):%Y-%m-%d %H:%M}, {estado}).")
        # Qué hacer, y que no se ha borrado nada.
        print(f"  [FALLO] {script}: no se crea ni se borra nada. Hay que mover o cancelar esa visita a mano, "
              "o cambiar las fechas de prueba (scripts/fechas_prueba.py).")
        # Código 1.
        sys.exit(1)
    # Todo libre: una línea de información (no cuenta como comprobación).
    print(f"  [INFO] {script}: {len(tramos)} tramo(s) de prueba sin visitas activas ajenas "
          f"(propias de una ejecución anterior: {propias}).")
