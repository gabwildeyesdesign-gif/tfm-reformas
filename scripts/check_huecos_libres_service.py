"""
Verificación SIN servidor del servicio de huecos libres (D32, Bloque B3,
plan docs/Plan_Horario_Visitas_D32.txt, secciones 3, 7 y 13):
app/services/huecos_libres_service.py y app/schemas/huecos_libres.py.

  S0  autoprueba de ConexionSinCommit (rama else: "se ha tragado").
  S1  etiqueta_dia: el texto EXACTO del desplegable (las dos franjas, solo
      tarde, solo mañana) y el día de la semana en español.
  S2  ConsultaHuecos (la query): sin fecha vale; "2026-10-15" vale;
      "15/10/2026", "2026-02-30", "mañana", "" y un número no valen; un
      parámetro de más no vale.
  S3  los modelos de respuesta rechazan un campo de más (extra="forbid": N7).
  S4  consultar_dias y consultar_dia con una conexión de prueba que CUENTA
      las órdenes: exactamente 4 en cada modo (el modo, now(), las reglas y
      UNA consulta de visitas), la primera es el modo de la transacción, y
      ningún commit() (N10: una consulta por día daría más). D45
      (2026-10-23): las dos llamadas pasan el canal (parámetro
      obligatorio); se usa el de administración, el de GET /huecos-libres.
  S5  estructura (ast): el servicio llama a disponibilidad() y no calcula
      horas por su cuenta (ni while, ni range con paso, ni choca/rejilla).
  S6  rendimiento (13): 300 visitas propias insertadas en UNA transacción
      con ROLLBACK (ConexionSinCommit); disponibilidad() de 60 laborables
      con UNA consulta, su tiempo y el EXPLAIN ANALYZE de esa consulta
      ([INFO]: se enseñan, no se exigen).

Las 4 preguntas: (1) escribe en tablas reales SOLO en la S6, dentro de una
transacción que se deshace siempre; (2) pasa su conexión al código (S4) y su
cursor (S6), por eso ConexionSinCommit; (3) si se corta, Postgres deshace
la transacción abierta; al final se comprueba que no queda el cliente de
prueba; (4) no toca el 8000 ni ninguna fila real.
"""

# ast: comprobaciones de estructura del código (S5).
import ast
# sys: consola en UTF-8 y código de salida.
import sys
# time: medir el tiempo (S6).
import time
# contextmanager: escribir el sustituto de get_db_connection (S4).
from contextlib import contextmanager
# Fechas.
from datetime import date, datetime, timedelta
# Path: rutas.
from pathlib import Path

# La raíz del repositorio (carpeta padre de scripts/) en sys.path.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda de módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: hablar con PostgreSQL.
import psycopg2
# La clase base de las conexiones y de los cursores.
import psycopg2.extensions
# Json: un diccionario de Python a una columna JSONB.
from psycopg2.extras import Json
# El error de validación de Pydantic (S2 y S3).
from pydantic import ValidationError

# Lo que se prueba.
from app.services import huecos_libres_service as servicio
# Las reglas de visita (disponibilidad, para la S6).
import app.services.reglas_visita as rv
# Los esquemas.
from app.schemas.huecos_libres import (ConsultaHuecos, DiaConHuecos, DiaConHuecosFormulario, HuecosDiaResponse,
                                       HuecosDiasResponse)
# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL

# Las fechas propias de este script (su bloque de semanas lejanas).
import fechas_prueba

# La marca propia (dominio reservado).
EMAIL = "check-huecos-svc@example.com"
# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si la condición es verdadera; [FALLO] (y se apunta) si no."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta.
    if condicion:
        # Una más.
        ok += 1
        # Se enseña.
        print(f"  [OK]    {titulo}")
    # Incorrecta.
    else:
        # Para el resumen.
        fallos.append(f"{titulo} {detalle}")
        # En su sitio.
        print(f"  [FALLO] {titulo} {detalle}")


# La excepción propia de la defensa.
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en la conexión de prueba."""


# La conexión con commit() prohibido (regla de CLAUDE.md del 2026-10-08).
class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión normal salvo commit(), que lanza CommitProhibido."""

    def commit(self):
        # No se confirma nada.
        raise CommitProhibido("commit() en una conexión de prueba: la prueba termina siempre en ROLLBACK")


# Las órdenes SQL que pasan por los cursores contadores (S4).
ORDENES = []


class CursorContador(psycopg2.extensions.cursor):
    """Un cursor normal que apunta cada orden antes de ejecutarla."""

    def execute(self, sql, params=None):
        # Se apunta el texto de la orden.
        ORDENES.append(" ".join(str(sql).split()))
        # Y se ejecuta de verdad.
        return super().execute(sql, params)


def malas(constructor, **campos):
    """True si construir el modelo con esos campos da ValidationError."""
    # try/except/else: sin el error, False (la rama else).
    try:
        # Se intenta construir.
        constructor(**campos)
    # El error esperado.
    except ValidationError:
        # Rechazado.
        return True
    # Sin error: se aceptó.
    else:
        # Aceptado.
        return False


# ======================================================================
print("S0. Autoprueba de ConexionSinCommit")
# ======================================================================
# Una conexión de prueba.
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# try/except/else: sin la excepción, FALLO explícito.
try:
    # Debe lanzar CommitProhibido.
    cn.commit()
# La esperada.
except CommitProhibido:
    # Correcto.
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# No lanzó nada.
else:
    # FALLO explícito.
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False, "(la excepción se ha tragado)")

# ======================================================================
print("\nS1. etiqueta_dia y el día de la semana")
# ======================================================================
# Un jueves (2026-10-15) con las dos franjas.
e = servicio.etiqueta_dia(date(2026, 10, 15), {"manana": ["09:45", "11:00"], "tarde": ["18:15"]})
# Se enseña.
print(f"    {e}")
# Exacta (la del plan, 3.3).
comprobar("dos franjas: 'Jueves 15/10 — mañana: 09:45 · 11:00 — tarde: 18:15'",
          e == "Jueves 15/10 — mañana: 09:45 · 11:00 — tarde: 18:15", f"({e})")
# Solo tarde.
e = servicio.etiqueta_dia(date(2026, 10, 15), {"manana": [], "tarde": ["17:00", "18:15"]})
# La franja vacía no se escribe.
comprobar("solo tarde: 'Jueves 15/10 — tarde: 17:00 · 18:15'", e == "Jueves 15/10 — tarde: 17:00 · 18:15", f"({e})")
# Solo mañana, un lunes de enero (cero delante en día y mes).
e = servicio.etiqueta_dia(date(2027, 1, 4), {"manana": ["08:30"], "tarde": []})
# Exacta.
comprobar("solo mañana: 'Lunes 04/01 — mañana: 08:30'", e == "Lunes 04/01 — mañana: 08:30", f"({e})")
# El día de la semana: 2026-10-12 es lunes; la semana entera.
semana = [servicio.DIAS_SEMANA[(date(2026, 10, 12) + timedelta(days=i)).weekday()] for i in range(7)]
# En español y en minúscula, con tildes.
comprobar("días de la semana: lunes ... domingo, con tildes",
          semana == ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"], f"({semana})")

# ======================================================================
print("\nS2. ConsultaHuecos (la query)")
# ======================================================================
# Sin fecha: modo días.
comprobar("sin fecha: vale (fecha None)", ConsultaHuecos().fecha is None)
# Con una fecha correcta.
comprobar("fecha '2026-10-15': vale", ConsultaHuecos(fecha="2026-10-15").fecha == date(2026, 10, 15))
# Las que no valen.
for valor in ("15/10/2026", "2026-02-30", "mañana", "", 20261015, "2026-10-15T10:00"):
    # Cada una, rechazada.
    comprobar(f"fecha {valor!r}: rechazada (422)", malas(ConsultaHuecos, fecha=valor))
# Un parámetro de más.
comprobar("un parámetro de más (dias=3): rechazado (422)", malas(ConsultaHuecos, dias=3))

# ======================================================================
print("\nS3. Los modelos de respuesta rechazan un campo de más (N7)")
# ======================================================================
# Un día válido como base.
DIA_OK = dict(fecha=date(2026, 10, 15), dia_semana="jueves", manana=["09:45"], tarde=[], num_horas_libres=1)
# El día del agente con un id de más.
comprobar("DiaConHuecos con oportunidad_id de más: rechazado", malas(DiaConHuecos, **DIA_OK, oportunidad_id=5))
# El del formulario con un id de visita de más.
comprobar("DiaConHuecosFormulario con visita_id de más: rechazado",
          malas(DiaConHuecosFormulario, **DIA_OK, etiqueta="x", visita_id=7))
# La respuesta de días con una clave de más.
comprobar("HuecosDiasResponse con ocupadas de más: rechazado",
          malas(HuecosDiasResponse, modo="dias", generado_en=datetime.now(rv.ZONA_MADRID), dias=[], ocupadas=[]))
# La de un día con una clave de más.
comprobar("HuecosDiaResponse con cliente de más: rechazado",
          malas(HuecosDiaResponse, modo="dia", generado_en=datetime.now(rv.ZONA_MADRID), fecha=date(2026, 10, 15),
                dia_semana="jueves", manana=[], tarde=[], horas=[], sin_horas_motivo="dia_completo", cliente="x"))

# ======================================================================
print("\nS4. Las órdenes de cada modo (UNA consulta de visitas, ningún commit)")
# ======================================================================
# La conexión que entrega el sustituto: commit() prohibido y cursor contador.
conn_codigo = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# Sus cursores apuntan cada orden.
conn_codigo.cursor_factory = CursorContador


@contextmanager
def conexion_de_prueba():
    """Sustituye a get_db_connection: entrega la conexión de la prueba y, al
    salir, la deshace (como la real), sin cerrarla."""
    # El código bajo prueba usa conn_codigo dentro de su "with".
    try:
        # Se entrega.
        yield conn_codigo
    # Al salir, pase lo que pase, se deshace.
    finally:
        # Nunca se confirma.
        conn_codigo.rollback()


# Se guarda la real y se pone la de prueba EN EL MÓDULO del servicio.
original = servicio.get_db_connection
# La sustitución.
servicio.get_db_connection = conexion_de_prueba
# try/finally: la real vuelve siempre.
try:
    # Los dos modos: (nombre, llamada).
    # D45: con el canal de administración (sin él, TypeError).
    for nombre, llamada in (("días", lambda: servicio.consultar_dias(rv.CANAL_ADMINISTRACION)),
                            ("un día", lambda: servicio.consultar_dia(date.today() + timedelta(days=30),
                                                                     rv.CANAL_ADMINISTRACION))):
        # Se vacía la lista de órdenes.
        ORDENES.clear()
        # try: un commit() se cuenta como FALLO.
        try:
            # La consulta.
            respuesta = llamada()
        # Un commit() dentro.
        except CommitProhibido:
            # FALLO contado.
            comprobar(f"modo {nombre}: sin commit()", False, "(CommitProhibido)")
            # Sin respuesta.
            respuesta = None
        # Se enseñan las órdenes (solo su principio).
        print(f"    modo {nombre}: {len(ORDENES)} órdenes: {[o[:40] for o in ORDENES]}")
        # Exactamente 4: el modo, now(), las reglas y las visitas.
        comprobar(f"modo {nombre}: exactamente 4 órdenes (modo, now(), reglas, UNA de visitas)", len(ORDENES) == 4,
                  f"({len(ORDENES)})")
        # La primera, el modo de la transacción.
        comprobar(f"modo {nombre}: la primera orden es REPEATABLE READ, READ ONLY",
                  bool(ORDENES) and ORDENES[0] == servicio.SQL_MODO_TRANSACCION, f"({ORDENES[:1]})")
        # Una sola orden lee visitas.
        comprobar(f"modo {nombre}: una sola orden lee visitas",
                  sum(1 for o in ORDENES if "FROM visitas" in o) == 1, f"({ORDENES})")
        # Hay respuesta del modelo correcto.
        comprobar(f"modo {nombre}: respuesta del modelo de su modo",
                  isinstance(respuesta, HuecosDiasResponse if nombre == "días" else HuecosDiaResponse),
                  f"({type(respuesta).__name__})")
# Siempre: la real vuelve y la de prueba se cierra.
finally:
    # Se restaura.
    servicio.get_db_connection = original
    # Se cierra.
    conn_codigo.close()

# ======================================================================
print("\nS5. Estructura del servicio (ast)")
# ======================================================================
# El árbol del servicio.
arbol = ast.parse((RAIZ_REPO / "app/services/huecos_libres_service.py").read_text(encoding="utf-8"))
# Las llamadas por nombre.
llamadas = {n.func.id for n in ast.walk(arbol) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
# Llama a disponibilidad.
comprobar("el servicio llama a disponibilidad()", "disponibilidad" in llamadas, f"({sorted(llamadas)})")
# Ni while ni choca ni rejilla (no calcula horas por su cuenta).
comprobar("el servicio no tiene while ni llama a choca() ni a rejilla()",
          not any(isinstance(n, ast.While) for n in ast.walk(arbol)) and not ({"choca", "rejilla"} & llamadas),
          f"({sorted(llamadas)})")
# range solo con uno o dos argumentos (nunca con un paso de minutos).
pasos = [n.lineno for n in ast.walk(arbol) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
         and n.func.id == "range" and len(n.args) == 3]
# Ninguno.
comprobar("ningún range con paso (ninguna rejilla propia)", pasos == [], f"(líneas {pasos})")

# ======================================================================
print("\nS6. Rendimiento con 300 visitas propias (ROLLBACK)")
# ======================================================================
# El bloque de fechas del script.
HOY = datetime.now(rv.ZONA_MADRID).date()
# El primer lunes del bloque.
LUNES = fechas_prueba.lunes_de_prueba("check_huecos_libres_service", HOY)
# Antes de crear nada: ninguna visita activa ajena en el bloque.
fechas_prueba.exigir_libres("check_huecos_libres_service", EMAIL,
                            [fechas_prueba.tramo_del_bloque("check_huecos_libres_service", HOY)])
# El cursor de la conexión de S0 (commit prohibido).
cur = cn.cursor()
# try/finally: pase lo que pase, ROLLBACK.
try:
    # SQL: el cliente, con la marca.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba huecos', %s, '600000000') RETURNING id;",
                (EMAIL,))
    # Su id.
    cliente = cur.fetchone()[0]
    # SQL: su lead.
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_huecos', %s) RETURNING id;",
                (cliente, Json({"m2": 10})))
    # Su id.
    lead = cur.fetchone()[0]
    # SQL: 300 oportunidades con UNA visita 'confirmada' cada una (el índice
    # parcial solo deja una activa por oportunidad), repartidas por los 20
    # laborables del bloque a horas de la rejilla y fuera de ella. Con
    # generate_series: una sola orden. ::int: make_interval pide enteros
    # (row_number da bigint).
    cur.execute(
        """
        WITH ops AS (
            INSERT INTO oportunidades (lead_id, tipo_reforma, estado)
            SELECT %(lead)s, 'bano', 'visita_agendada' FROM generate_series(1, 300)
            RETURNING id
        )
        INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente)
        SELECT id, %(lunes)s::timestamptz + make_interval(days => ((n - 1) %% 20) / 5 * 7 + ((n - 1) %% 20) %% 5,
                                                          mins => 510 + ((n - 1) / 20) * 37),
               'confirmada', 'prueba rendimiento'
        FROM (SELECT id, (row_number() OVER (ORDER BY id))::int AS n FROM ops) AS t;
        """,
        {"lead": lead, "lunes": datetime.combine(LUNES, datetime.min.time(), tzinfo=rv.ZONA_MADRID)})
    # Cuántas se insertaron.
    print(f"    visitas propias insertadas: {cur.rowcount}")
    # Trescientas.
    comprobar("300 visitas propias insertadas (dentro de la transacción)", cur.rowcount == 300, f"({cur.rowcount})")
    # Las reglas reales y el reloj.
    reglas, problemas = rv.leer_reglas_visita(cur)
    # SQL: el reloj.
    cur.execute("SELECT now();")
    # El ahora.
    ahora = cur.fetchone()[0]
    # 60 laborables desde el lunes del bloque.
    dias = [d for d in (LUNES + timedelta(days=i) for i in range(90)) if d.weekday() < 5][:60]
    # Un cursor que cuenta, envolviendo el real.
    contador = {"n": 0}

    class Envoltorio:
        """Cuenta los execute y los pasa al cursor real."""

        def execute(self, sql, params=None):
            # Uno más.
            contador["n"] += 1
            # Al real.
            return cur.execute(sql, params)

        def fetchall(self):
            # Del real.
            return cur.fetchall()

    # El tiempo de disponibilidad() con 60 días y las 300 visitas.
    t0 = time.perf_counter()
    # El cálculo.
    resultado = rv.disponibilidad(Envoltorio(), dias, ahora, reglas) if not problemas else {}
    # Milisegundos.
    ms = (time.perf_counter() - t0) * 1000
    # [INFO]: el tiempo.
    print(f"  [INFO]  disponibilidad() de 60 laborables con 300 visitas propias: {ms:.0f} ms, {contador['n']} consulta(s)")
    # Una sola consulta.
    comprobar("60 días con 300 visitas: UNA consulta", contador["n"] == 1 and len(resultado) == 60,
              f"({contador['n']}, {len(resultado)} días)")
    # SQL: EXPLAIN ANALYZE de la misma consulta (la ventana de los 60 días).
    cur.execute(
        """
        EXPLAIN ANALYZE SELECT fecha_propuesta FROM visitas
        WHERE estado = ANY(%s) AND NOT (id = ANY(%s::int[]))
          AND fecha_propuesta >= %s AND fecha_propuesta < %s;
        """,
        (["solicitada", "confirmada"], [], datetime.combine(dias[0], datetime.min.time(), tzinfo=rv.ZONA_MADRID),
         datetime.combine(dias[-1] + timedelta(days=1), datetime.min.time(), tzinfo=rv.ZONA_MADRID)))
    # Cada línea del plan, como [INFO].
    for (linea,) in cur.fetchall():
        # Se enseña.
        print(f"  [INFO]  {linea}")
# Siempre: ROLLBACK de todo.
finally:
    # Se deshace.
    cn.rollback()

# Fuera de la transacción: no queda el cliente de prueba.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
# Cero.
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# Se cierra la lectura.
cn.rollback()
# Y la conexión.
cn.close()

# Resumen y código de salida (1 si hubo fallos).
total = ok + len(fallos)
# Separador.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{total} correctas")
# Separador.
print("=" * 78)
# Con fallos, se listan y el código de salida es 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
