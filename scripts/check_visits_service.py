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
  2a. _leer_reglas() con un cursor de mentira (sin base de datos): una
      regla que falta, una con decimales y una franja al revés deben
      salir las tres como problemas.
  2b. Configuración incompleta de verdad (503): contra la base de datos
      real, con un lead propio (emails check-visits-svc-%@example.com).
      El nombre de UNA clave se cambia EN MEMORIA por uno que no existe;
      no se toca ninguna fila compartida de reglas_negocio. Debe salir
      ConfiguracionIncompleta, el log debe CONSERVARSE (el rechazo se lanza
      después del commit), y no debe crearse ninguna visita.
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

import app.services.visits_service as visits_service
from app.config import DATABASE_URL
from app.db import connection as db
from app.schemas.leads import LeadCreate
from app.schemas.visits import VisitaCreate
from app.services.estimate_service import calculate_estimate
from app.services.leads_service import create_lead
from app.services.visits_service import ConfiguracionIncompleta, combinar_fecha_hora_madrid

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

print("\n" + "=" * 78)
print("2a. _leer_reglas() CON UN CURSOR DE MENTIRA (sin base de datos)")
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
    (visits_service.CLAVE_MANANA_INICIO, Decimal("510.50")),  # con decimales
    (visits_service.CLAVE_MANANA_FIN, Decimal("810.00")),
    (visits_service.CLAVE_TARDE_INICIO, Decimal("1200.00")),  # al revés: empieza
    (visits_service.CLAVE_TARDE_FIN, Decimal("1020.00")),     # después de acabar
    # duracion_visita_min: falta
]
reglas, problemas = visits_service._leer_reglas(CursorDeMentira(reglas_rotas))
for p in problemas:
    print(f"  problema: {p}")
comprobar("detecta la regla que falta", any("duracion_visita_min: no existe" in p for p in problemas))
comprobar("detecta el valor con decimales", any("visita_manana_inicio_min: valor no válido" in p for p in problemas))
comprobar("detecta la franja al revés", any("visita_tarde_inicio_min (1200) no es menor" in p for p in problemas))
reglas_ok, problemas_ok = visits_service._leer_reglas(CursorDeMentira([
    (visits_service.CLAVE_MANANA_INICIO, Decimal("510.00")), (visits_service.CLAVE_MANANA_FIN, Decimal("810.00")),
    (visits_service.CLAVE_TARDE_INICIO, Decimal("1020.00")), (visits_service.CLAVE_TARDE_FIN, Decimal("1200.00")),
    (visits_service.CLAVE_DURACION, Decimal("60.00")),
]))
comprobar("con las 5 reglas correctas: 0 problemas y valores enteros",
          problemas_ok == [] and reglas_ok[visits_service.CLAVE_DURACION] == 60, f"({reglas_ok})")

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
    clave_real = visits_service.CLAVE_DURACION
    CLAVE_FALSA = "clave_inexistente_check_visits"
    visits_service.CLAVE_DURACION = CLAVE_FALSA
    visits_service.CLAVES_REGLAS = tuple(CLAVE_FALSA if c == clave_real else c for c in visits_service.CLAVES_REGLAS)

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
        visits_service.CLAVE_DURACION = clave_real
        visits_service.CLAVES_REGLAS = tuple(clave_real if c == CLAVE_FALSA else c for c in visits_service.CLAVES_REGLAS)

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
