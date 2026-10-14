"""
Verificación de las piezas de app/services/visits_service.py que se
pueden probar SIN servidor (plan: docs/Plan_Endpoint_Visits.txt, 8.1).

Bloque C (esta versión):
  1. Cambio de hora. combinar_fecha_hora_madrid() con fechas FIJAS, no
     relativas a hoy, porque lo que se prueba es el calendario:
       2026-10-23 08:30 -> +02:00 (horario de verano)
       2026-10-26 08:30 -> +01:00 (horario de invierno; el cambio de hora
                                   es el domingo 25/10/2026)
     Se comprueba también el instante en UTC, que es lo que guarda
     Postgres: 06:30 y 07:30. Con un desfase fijo +02:00 escrito a mano,
     el caso del 26/10 daría 06:30 en UTC, una hora antes de lo pedido.

Bloque D:
  2a. leer_reglas_visita() con un cursor de mentira (sin base de datos): una
      regla que falta, una con decimales y una franja al revés deben
      salir las tres como problemas.
  2b. Configuración incompleta de verdad (503): contra la base de datos
      real, con un lead propio (emails check-visits-svc-%@example.com).
      El nombre de UNA clave se cambia EN MEMORIA por uno que no existe;
      no se toca ninguna fila compartida de reglas_negocio. Debe salir
      ConfiguracionIncompleta, el log debe CONSERVARSE (el rechazo se lanza
      después del commit), y no debe crearse ninguna visita.

D33 (2026-10-14, plan docs/Plan_Fecha_Fin_Visita_D33.txt):
  1b. fin_de_visita() con duraciones distintas de la real y cruzando el
      cambio de hora (sin base de datos), y con ast: sin números escritos en
      la función, y los dos servicios la usan sin sumar por su cuenta.
"""

import sys
from datetime import date, time, timedelta, timezone
from pathlib import Path

# El paquete "app" está en la raíz del repositorio (misma técnica que los
# demás check_*.py).
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import uuid
from decimal import Decimal

import psycopg2

import app.services.reglas_visita as reglas_visita
import app.services.visits_service as visits_service
from app.config import DATABASE_URL
from app.db import connection as db
from app.schemas.leads import LeadCreate
from app.schemas.visits import VisitaCreate
from app.services.estimate_service import calculate_estimate
from app.services.leads_service import create_lead
# Desde el 2026-09-30 las reglas de fecha y de configuración viven en
# reglas_visita.py (compartidas con /gate-decisions); este script las busca
# allí. Lo que se comprueba no cambia: solo dónde se mira.
from app.services.reglas_visita import ConfiguracionIncompleta, combinar_fecha_hora_madrid

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"  [FALLO] {titulo}  {detalle}")


print("=" * 78)
print("1. CAMBIO DE HORA (fechas fijas, Europe/Madrid)")
print("=" * 78)
# (día, hora, desfase esperado, instante esperado en UTC)
CASOS = [
    (date(2026, 10, 23), time(8, 30), timedelta(hours=2), "2026-10-23T06:30:00+00:00"),
    (date(2026, 10, 26), time(8, 30), timedelta(hours=1), "2026-10-26T07:30:00+00:00"),
]
for dia, hora, desfase, utc_esperado in CASOS:
    resultado = combinar_fecha_hora_madrid(dia, hora)
    # utcoffset(): cuánto se separa esa hora local de UTC en ESE día.
    # astimezone(timezone.utc): el mismo instante expresado en UTC.
    en_utc = resultado.astimezone(timezone.utc).isoformat()
    print(f"  {dia} {hora:%H:%M} -> {resultado.isoformat()}  | UTC {en_utc}")
    comprobar(f"{dia}: desfase {desfase}", resultado.utcoffset() == desfase, f"(obtenido {resultado.utcoffset()})")
    comprobar(f"{dia}: en UTC {utc_esperado}", en_utc == utc_esperado)
    comprobar(f"{dia}: la hora local sigue siendo {hora:%H:%M}", (resultado.hour, resultado.minute) == (hora.hour, hora.minute))

# D33: la sección nueva, con su cabecera.
print("\n" + "=" * 78)
print("1b. D33: fin_de_visita() (función pura) y quién la usa (ast)")
print("=" * 78)
# D33 (2026-10-14, plan docs/Plan_Fecha_Fin_Visita_D33.txt): fecha_fin_visita
# = inicio + duracion_visita_min. Duraciones DISTINTAS de la real (60): una
# copia con un 60 fijo pasaría las pruebas HTTP, pero no estas. Lo esperado
# está escrito a mano, no calculado con el código probado.
# (inicio en Madrid, duración en minutos, fin esperado como lo escribe la API)
CASOS_FIN = [
    # Verano (+02:00), la duración de hoy.
    (combinar_fecha_hora_madrid(date(2026, 10, 16), time(11, 0)), 60, "2026-10-16T12:00:00+02:00"),
    # Invierno (+01:00), una duración rara (47).
    (combinar_fecha_hora_madrid(date(2026, 10, 26), time(8, 30)), 47, "2026-10-26T09:17:00+01:00"),
    # La última de la tarde con 90.
    (combinar_fecha_hora_madrid(date(2026, 10, 16), time(18, 15)), 90, "2026-10-16T19:45:00+02:00"),
    # Cruza el cambio de hora (25/10/2026, 03:00 -> 02:00): 01:30+02:00 más
    # 90 min REALES son las 02:00+01:00. Una suma "de reloj de pared" daría
    # 03:00+01:00, una hora de más (no pasa en la franja, pero es la regla).
    (combinar_fecha_hora_madrid(date(2026, 10, 25), time(1, 30)), 90, "2026-10-25T02:00:00+01:00"),
]
# Cada caso, con unas reglas que solo tienen la duración (la función no
# necesita más).
for inicio_caso, minutos_caso, esperado_caso in CASOS_FIN:
    # isoformat(): el mismo texto que escribe la API ("...T12:00:00+02:00").
    # try: una versión rota que lea otra regla (KeyError) o que falle de
    # cualquier otro modo cuenta como FALLO, nunca para el script.
    try:
        obtenido = reglas_visita.fin_de_visita(inicio_caso, {reglas_visita.CLAVE_DURACION: minutos_caso}).isoformat()
    # La excepción, como texto, en el lugar del resultado.
    except Exception as error:
        obtenido = f"{type(error).__name__}: {error}"
    # Mismo texto: mismo instante, mismo desfase y mismo formato.
    comprobar(f"{inicio_caso.isoformat()} + {minutos_caso} min -> {esperado_caso}", obtenido == esperado_caso,
              f"(obtenido {obtenido})")

# ast: estructura, sin ejecutar nada.
import ast

# El árbol de reglas_visita.py y, dentro, la función fin_de_visita.
arbol_reglas = ast.parse((RAIZ_REPO / "app/services/reglas_visita.py").read_text(encoding="utf-8"))
funcion_fin = [n for n in arbol_reglas.body if isinstance(n, ast.FunctionDef) and n.name == "fin_de_visita"]
# Los números enteros escritos dentro de su cuerpo (bool es un int: se excluye).
numeros = [n.value for f in funcion_fin for n in ast.walk(f)
           if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool)]
comprobar("ast: fin_de_visita existe y no tiene ningún número escrito (nunca un 60)",
          len(funcion_fin) == 1 and numeros == [], f"({len(funcion_fin)} funciones, números {numeros})")
# Los dos servicios: ni una suma propia (timedelta) y fin_de_visita en sus dos respuestas.
for archivo_servicio in ("app/services/visits_service.py", "app/services/resultados_llamada_service.py"):
    # El árbol del servicio.
    arbol_servicio = ast.parse((RAIZ_REPO / archivo_servicio).read_text(encoding="utf-8"))
    # Cualquier nombre o atributo "timedelta".
    sumas = [n for n in ast.walk(arbol_servicio) if (isinstance(n, ast.Name) and n.id == "timedelta")
             or (isinstance(n, ast.Attribute) and n.attr == "timedelta")]
    # Las llamadas a fin_de_visita (dos: la repetición y la escritura).
    llamadas = [n for n in ast.walk(arbol_servicio) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "fin_de_visita"]
    comprobar(f"ast: {archivo_servicio} no usa timedelta y llama 2 veces a fin_de_visita",
              sumas == [] and len(llamadas) == 2, f"({len(sumas)} timedelta, {len(llamadas)} llamadas)")

print("\n" + "=" * 78)
print("2a. leer_reglas_visita() CON UN CURSOR DE MENTIRA (sin base de datos)")
print("=" * 78)


class CursorDeMentira:
    """
    Imita lo mínimo de un cursor de psycopg2: execute() no hace nada y
    fetchall() devuelve las filas que se le dieron. Así se prueba la
    validación de las reglas sin tocar la base de datos.
    """

    def __init__(self, filas):
        self.filas = filas

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self.filas


reglas_rotas = [
    (reglas_visita.CLAVE_MANANA_INICIO, Decimal("510.50")),  # con decimales
    (reglas_visita.CLAVE_MANANA_FIN, Decimal("810.00")),
    (reglas_visita.CLAVE_TARDE_INICIO, Decimal("1200.00")),  # al revés: empieza
    (reglas_visita.CLAVE_TARDE_FIN, Decimal("1020.00")),     # después de acabar
    # duracion_visita_min: falta
]
reglas, problemas = reglas_visita.leer_reglas_visita(CursorDeMentira(reglas_rotas))
for p in problemas:
    print(f"  problema: {p}")
comprobar("detecta la regla que falta", any("duracion_visita_min: no existe" in p for p in problemas))
comprobar("detecta el valor con decimales", any("visita_manana_inicio_min: valor no válido" in p for p in problemas))
comprobar("detecta la franja al revés", any("visita_tarde_inicio_min (1200) no es menor" in p for p in problemas))
# B5 (2026-10-12, D31): desde paso13 son SEIS reglas; la sexta,
# paso_huecos_min, es el paso de las horas libres que ofrece el 409
# hueco_ocupado. Sin ella, leer_reglas_visita da un problema (lo prueba
# scripts/check_disponibilidad_service.py, S6).
# (Nota 2026-10-13, D32: paso_huecos_min ya NO se lee; desde paso14 son
# SIETE reglas: las cinco de paso9, desplazamiento_min y dias_oferta_visita.
# Lo prueba check_disponibilidad_service, S6.)
reglas_ok, problemas_ok = reglas_visita.leer_reglas_visita(CursorDeMentira([
    # Las dos franjas (08:30-13:30 y 17:00-20:00).
    (reglas_visita.CLAVE_MANANA_INICIO, Decimal("510.00")), (reglas_visita.CLAVE_MANANA_FIN, Decimal("810.00")),
    # La tarde.
    (reglas_visita.CLAVE_TARDE_INICIO, Decimal("1020.00")), (reglas_visita.CLAVE_TARDE_FIN, Decimal("1200.00")),
    # La duración y las dos de D32.
    (reglas_visita.CLAVE_DURACION, Decimal("60.00")), (reglas_visita.CLAVE_DESPLAZAMIENTO, Decimal("15.00")),
    # Los días de oferta.
    (reglas_visita.CLAVE_DIAS_OFERTA, Decimal("10.00")),
]))
# Siete correctas: ningún problema.
comprobar("con las 7 reglas correctas: 0 problemas y valores enteros",
          problemas_ok == [] and reglas_ok[reglas_visita.CLAVE_DURACION] == 60, f"({reglas_ok})")

print("\n" + "=" * 78)
print("2b. CONFIGURACIÓN INCOMPLETA DE VERDAD (503), contra la base de datos")
print("=" * 78)
PATRON = "check-visits-svc-%@example.com"
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def limpiar_propio():
    """Solo lo propio, en el orden de las claves foráneas."""
    ops = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
    cur.execute(f"DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM visitas WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM presupuestos WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM oportunidades WHERE id IN ({ops});", {"p": PATRON})
    cur.execute("DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s);", {"p": PATRON})
    cur.execute("DELETE FROM clientes WHERE email LIKE %(p)s;", {"p": PATRON})
    cn.commit()


limpiar_propio()
db.init_pool()
try:
    token = str(uuid.uuid4())
    alta = create_lead(LeadCreate(
        nombre="Prueba servicio visitas", email=f"check-visits-svc-{token[:8]}@example.com",
        telefono="600000000", tipo_reforma="bano", m2=Decimal("6"), nivel_acabados="medio",
        incluye_cambios_estructurales=False, fotos=[], lead_token=token,
    ))
    calculo = calculate_estimate(alta.oportunidad_id)
    print(f"  lead propio: oportunidad {alta.oportunidad_id}, estado tras calcular: {calculo.status}")

    # Cambio SOLO EN MEMORIA: las funciones del servicio leen estas
    # constantes del módulo en el momento de ejecutarse, así que ven el
    # nombre falso. La fila real duracion_visita_min no se toca.
    clave_real = reglas_visita.CLAVE_DURACION
    CLAVE_FALSA = "clave_inexistente_check_visits"
    reglas_visita.CLAVE_DURACION = CLAVE_FALSA
    reglas_visita.CLAVES_REGLAS = tuple(CLAVE_FALSA if c == clave_real else c for c in reglas_visita.CLAVES_REGLAS)

    # La fecha no llega a validarse: la configuración falla antes.
    peticion = VisitaCreate(lead_token=token, fecha="2030-01-07", hora="08:30", texto_cliente="prueba 503")
    try:
        visits_service.solicitar_visita(peticion)
    except ConfiguracionIncompleta as error:
        print(f"  excepción: {type(error).__name__} motivo={error.motivo} faltan={error.faltan}")
        comprobar("sale ConfiguracionIncompleta con motivo configuracion_incompleta", error.motivo == "configuracion_incompleta")
        comprobar("faltan nombra la clave que falta", f"{CLAVE_FALSA}: no existe" in error.faltan)
    except Exception as error:
        comprobar("sale ConfiguracionIncompleta", False, f"(salió {type(error).__name__}: {error})")
    else:
        # Rama else del try: solo si NO salió ninguna excepción.
        comprobar("sale ConfiguracionIncompleta", False, "(la excepción se ha tragado: no salió nada)")
    finally:
        reglas_visita.CLAVE_DURACION = clave_real
        reglas_visita.CLAVES_REGLAS = tuple(clave_real if c == CLAVE_FALSA else c for c in reglas_visita.CLAVES_REGLAS)

    cur.execute(
        "SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
        "AND accion = 'visita_configuracion_incompleta';",
        (alta.oportunidad_id,),
    )
    logs = [f[0] for f in cur.fetchall()]
    cur.execute("SELECT count(*) FROM visitas WHERE oportunidad_id = %s;", (alta.oportunidad_id,))
    n_visitas = cur.fetchone()[0]
    cur.execute("SELECT estado FROM oportunidades WHERE id = %s;", (alta.oportunidad_id,))
    estado = cur.fetchone()[0]
    cn.commit()
    print(f"  logs visita_configuracion_incompleta: {logs}")
    comprobar("el log de la configuración incompleta SE CONSERVA (rechazo tras el commit)", len(logs) == 1)
    comprobar("no se ha creado ninguna visita", n_visitas == 0)
    comprobar("la oportunidad sigue en presupuesto_enviado", estado == "presupuesto_enviado", f"({estado})")
finally:
    db.close_pool()
    limpiar_propio()
    cur.execute("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON,))
    restos = cur.fetchone()[0]
    cn.commit()
    cn.close()
    print(f"  limpieza: clientes de prueba restantes = {restos}")
    comprobar("no queda ningún dato de prueba", restos == 0)

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
