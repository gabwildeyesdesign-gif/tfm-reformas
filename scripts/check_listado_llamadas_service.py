"""
Verificación SIN servidor de app/services/listado_llamadas_service.py (plan:
docs/Plan_Endpoint_Listado_WF3.txt, sección 6.2, S1-S7; S0 y la conexión de
S5 añadidas el 2026-10-10, docs/Plan_Resumen_Lista_Diaria.txt, sección 8.0).

  S0. Autoprueba de ConexionSinCommit: commit() tiene que lanzar
      CommitProhibido (regla de CLAUDE.md, conexiones de prueba sin commit).
  S1. siguiente_laborable con fechas FIJAS (no caducan).
  S2. limites_dia_madrid a los dos lados del cambio de hora (fechas FIJAS),
      y los domingos de 25 y 23 horas.
  S3. validar_reglas_listado con diccionarios: falta, 0, -5, 24.50, 8760,
      8761 y 99999999 (P6, corregida el 2026-10-07).
  S4. contacto_de: con y sin la clave 'contacto'.
  S5. LÍMITE EXACTO del plazo y de los extremos del día, contra la base de
      datos, dentro de UNA transacción que termina en ROLLBACK: no queda
      ninguna fila. Marca propia: emails check-listado-svc-<8>@example.com.
      La conexión es una ConexionSinCommit: si leer_listado (o una versión
      rota) llama a commit(), sale CommitProhibido, se cuenta como FALLO y
      el ROLLBACK lo deshace todo.
  S6. Modo de la transacción de obtener_listado: repeatable read y read
      only (con leer_listado sustituida EN MEMORIA por un espía).
  S7. obtener_listado lanza ConfiguracionIncompleta (503) con una clave
      cambiada EN MEMORIA y con un valor fuera de rango inyectado EN
      MEMORIA; la fila real de reglas_negocio no se toca. Si NO sale la
      excepción, FALLO explícito ("la excepción se ha tragado").

Código de salida: 0 si todo está bien, 1 si hay algún fallo.
"""

# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# uuid: para que cada ejecución tenga su propia marca.
import uuid
# Fechas: date (un día), datetime (un instante), timedelta (una duración)
# y timezone (para escribir instantes fijos en UTC).
from datetime import date, datetime, timedelta, timezone
# Decimal: los valores de reglas_negocio llegan como Decimal ('48.00').
from decimal import Decimal
# Path: para calcular la raíz del repositorio.
from pathlib import Path

# La raíz del repositorio es la carpeta padre de scripts/; se añade a
# sys.path para que "import app..." funcione.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Tildes bien en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: para la transacción propia de S5.
import psycopg2
# Json: diccionario de Python -> valor JSONB.
from psycopg2.extras import Json

# La cadena de conexión (nunca se imprime).
from app.config import DATABASE_URL
# El pool del backend: obtener_listado usa get_db_connection(), que lo necesita.
from app.db import connection as db
# Lo que se prueba: el módulo entero (para los cambios en memoria) y la
# excepción del 503.
from app.services import listado_llamadas_service as servicio
from app.services.reglas_visita import ConfiguracionIncompleta

# Patrón SQL (LIKE) de los clientes de ESTE script.
PATRON = "check-listado-svc-%@example.com"
# Atajos a las dos claves y a UTC.
G, S = servicio.CLAVE_RECORDATORIO_GATE, servicio.CLAVE_SEGUIMIENTO
UTC = timezone.utc

# Contador de correctas y lista de fallos.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si condicion es verdadera, [FALLO] si no; se cuenta."""
    # global: se modifica la variable ok del archivo.
    global ok
    # Verdadera: se suma una correcta y se enseña [OK].
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    # Falsa: se apunta su título en la lista de fallos y se enseña [FALLO].
    else:
        fallos.append(titulo)
        print(f"    [FALLO] {titulo}  {detalle}")


# ======================================================================
# Conexión de prueba sin commit (regla de CLAUDE.md desde el 2026-10-08)
# ======================================================================
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en una conexión de prueba."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión de psycopg2 normal, salvo commit(), que lanza un error.
    rollback() funciona como siempre (es lo que usa la prueba)."""

    def commit(self):
        # No se confirma NADA: se lanza el error y la transacción sigue
        # abierta, para que el ROLLBACK de la prueba la deshaga.
        raise CommitProhibido("commit() en una conexión de prueba: la prueba termina siempre en ROLLBACK")


# ======================================================================
print("S0. Autoprueba de ConexionSinCommit")
# ======================================================================
# connection_factory: psycopg2 crea la conexión con esta clase.
conexion_prueba = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# try/except/else: sin la excepción, FALLO explícito (regla de CLAUDE.md).
try:
    conexion_prueba.commit()
# La esperada: la defensa funciona.
except CommitProhibido:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# else: commit() no lanzó nada -> la defensa no existe.
else:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False,
              "(sin error: la excepción se ha tragado)")
# Siempre: se cierra (no había nada que deshacer).
finally:
    conexion_prueba.rollback()
    conexion_prueba.close()

# ======================================================================
print("\nS1. siguiente_laborable (fechas fijas)")
# ======================================================================
# (hoy, esperado, texto). 2026-10-05 es lunes; 2026-10-23, viernes.
# Se recorre una tupla de casos; cada caso es otra tupla de tres valores.
for hoy, esperado, texto in (
    # Entre semana: el día siguiente.
    (date(2026, 10, 5), date(2026, 10, 6), "lunes 05/10 -> martes 06/10"),
    (date(2026, 10, 8), date(2026, 10, 9), "jueves 08/10 -> viernes 09/10"),
    # Viernes, sábado y domingo: el lunes siguiente.
    (date(2026, 10, 23), date(2026, 10, 26), "viernes 23/10 -> lunes 26/10"),
    (date(2026, 10, 24), date(2026, 10, 26), "sábado 24/10 -> lunes 26/10"),
    (date(2026, 10, 25), date(2026, 10, 26), "domingo 25/10 -> lunes 26/10"),
):
    # Se llama a la función del servicio y se compara con lo esperado.
    resultado = servicio.siguiente_laborable(hoy)
    comprobar(texto, resultado == esperado, f"({resultado})")

# ======================================================================
print("\nS2. limites_dia_madrid (fechas fijas, a los dos lados del cambio de hora)")
# ======================================================================
# Los instantes esperados se escriben en UTC: no dependen de ninguna zona.
# Cada caso: (día pedido, inicio esperado en UTC, fin esperado en UTC, texto).
for dia, inicio_utc, fin_utc, texto in (
    # 23/10/2026, horario de verano (+02:00): las 00:00 de Madrid son las 22:00 UTC del día anterior.
    (date(2026, 10, 23), datetime(2026, 10, 22, 22, tzinfo=UTC), datetime(2026, 10, 23, 22, tzinfo=UTC),
     "viernes 23/10/2026 (+02:00)"),
    # 26/10/2026, ya en invierno (+01:00): las 00:00 de Madrid son las 23:00 UTC del día anterior.
    (date(2026, 10, 26), datetime(2026, 10, 25, 23, tzinfo=UTC), datetime(2026, 10, 26, 23, tzinfo=UTC),
     "lunes 26/10/2026 (+01:00)"),
    # 29/03/2027, otra vez en verano (+02:00), tras el cambio de marzo.
    (date(2027, 3, 29), datetime(2027, 3, 28, 22, tzinfo=UTC), datetime(2027, 3, 29, 22, tzinfo=UTC),
     "lunes 29/03/2027 (+02:00)"),
):
    # Los dos extremos que calcula el servicio para ese día.
    inicio, fin = servicio.limites_dia_madrid(dia)
    # Comparar dos datetime con zona compara el INSTANTE real.
    comprobar(f"{texto}: inicio", inicio == inicio_utc, f"({inicio.isoformat()})")
    comprobar(f"{texto}: fin", fin == fin_utc, f"({fin.isoformat()})")
# Los días del cambio: 25 h (octubre) y 23 h (marzo). Con "+24 h" fallarían.
# OJO: se resta en UTC. Python, al restar dos datetime con el MISMO objeto
# de zona (los dos ZONA_MADRID), resta la hora "de reloj de pared" e ignora
# el desfase: daría 24 h aunque el día dure 25. Pasados a UTC, la resta da
# la duración real. (El servicio no resta nunca: Postgres compara instantes.)
# Domingo 25/10/2026 (fin del horario de verano): tiene que durar 25 horas.
inicio, fin = servicio.limites_dia_madrid(date(2026, 10, 25))
duracion = fin.astimezone(UTC) - inicio.astimezone(UTC)
comprobar("domingo 25/10/2026 mide 25 horas", duracion == timedelta(hours=25), f"({duracion})")
# Domingo 28/03/2027 (empieza el horario de verano): tiene que durar 23 horas.
inicio, fin = servicio.limites_dia_madrid(date(2027, 3, 28))
duracion = fin.astimezone(UTC) - inicio.astimezone(UTC)
comprobar("domingo 28/03/2027 mide 23 horas", duracion == timedelta(hours=23), f"({duracion})")

# ======================================================================
print("\nS3. validar_reglas_listado (diccionarios, sin base de datos)")
# ======================================================================
# Las dos bien: sin problemas y las dos como int.
reglas, problemas = servicio.validar_reglas_listado({G: Decimal("24.00"), S: Decimal("48.00")})
comprobar("24 y 48 -> sin problemas, como int", problemas == [] and reglas == {G: 24, S: 48}, f"({reglas})")
# Falta una: el diccionario solo trae la del Gate.
reglas, problemas = servicio.validar_reglas_listado({G: Decimal("24.00")})
# Tiene que salir UN problema y empezar por "<clave de seguimiento>: no existe".
comprobar("falta la de seguimiento -> problema que la nombra",
          len(problemas) == 1 and problemas[0].startswith(f"{S}: no existe"), f"({problemas})")
# Valores no válidos para la regla de seguimiento (P6: entero entre 1 y 8760).
for valor in ("0.00", "-5.00", "24.50", "8761.00", "99999999.00"):
    # La regla del Gate bien y la de seguimiento con el valor malo.
    reglas, problemas = servicio.validar_reglas_listado({G: Decimal("24.00"), S: Decimal(valor)})
    # Un solo problema, y que nombre la clave de seguimiento.
    comprobar(f"{valor} -> problema que nombra la clave", len(problemas) == 1 and problemas[0].startswith(S),
              f"({problemas})")
# El máximo exacto es válido.
reglas, problemas = servicio.validar_reglas_listado({G: Decimal("24.00"), S: Decimal("8760.00")})
comprobar("8760 -> válido", problemas == [] and reglas[S] == 8760)
# El máximo vale también para la otra regla.
reglas, problemas = servicio.validar_reglas_listado({G: Decimal("8761.00"), S: Decimal("48.00")})
comprobar("8761 en la regla del Gate -> problema que la nombra",
          len(problemas) == 1 and problemas[0].startswith(G), f"({problemas})")

# ======================================================================
print("\nS4. contacto_de")
# ======================================================================
# Con la clave 'contacto': salen sus datos con origen "solicitud".
c = servicio.contacto_de(True, "Ana", "600111222")
# .origen es un Enum; .value es su texto ("solicitud").
comprobar("con 'contacto' -> sus datos y origen 'solicitud'",
          (c.nombre, c.telefono, c.origen.value) == ("Ana", "600111222", "solicitud"))
# Aunque lleguen valores, sin la clave salen None: nunca se rellenan.
c = servicio.contacto_de(False, "Ana", "600111222")
comprobar("sin 'contacto' -> None, None y origen 'no_disponible'",
          (c.nombre, c.telefono, c.origen.value) == (None, None, "no_disponible"))

# ======================================================================
print("\nS5. Límite EXACTO, en una transacción que termina en ROLLBACK")
# ======================================================================
# Conexión directa propia (no la del pool) y su cursor. Todo lo que se
# haga con ella queda en UNA transacción, que el finally deshace.
# ConexionSinCommit: ni leer_listado ni una versión rota de él pueden
# confirmar nada de lo que se inserta aquí (ver S0).
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
cur = cn.cursor()
# La defensa tiene que estar en ESTA conexión, la que recibe el código, no
# solo en la de S0.
comprobar("la conexión de la S5 es ConexionSinCommit", isinstance(cn, ConexionSinCommit),
          f"({type(cn).__name__})")
# try/except/finally: pase lo que pase dentro, el finally hace el ROLLBACK.
try:
    # SQL: el reloj de ESTA transacción. Dentro de ella now() no cambia,
    # así que el límite es exacto al microsegundo.
    cur.execute("SELECT now();")
    ahora = cur.fetchone()[0]
    # El próximo laborable y sus límites, con las funciones ya probadas.
    dia = servicio.siguiente_laborable(ahora.astimezone(servicio.ZONA_MADRID).date())
    inicio, fin = servicio.limites_dia_madrid(dia)
    # 8 caracteres aleatorios para el email propio de esta ejecución.
    marca = uuid.uuid4().hex[:8]
    # SQL: el cliente propio (email con la marca); RETURNING id da su id.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Servicio Listado", f"check-listado-svc-{marca}@example.com", "600000009"))
    cliente = cur.fetchone()[0]
    # SQL: un lead con 'contacto' para todas las oportunidades (lead_id no
    # es UNIQUE en oportunidades). Json(...) convierte el diccionario en el
    # valor JSONB de datos_estructurados; RETURNING id da el id del lead.
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_listado', '[]'::jsonb, %s) RETURNING id;",
                (cliente, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                "incluye_cambios_estructurales": False,
                                "contacto": {"nombre": "Servicio Listado", "email": "x@example.com",
                                             "telefono": "600000009"}})))
    lead = cur.fetchone()[0]

    def oportunidad(estado, presupuesto_creado=None):
        """Crea una oportunidad propia en ese estado y, si se pide, su
        presupuesto con created_at FIJO. Devuelve su id."""
        # SQL: la oportunidad, en el estado pedido; RETURNING id da su id.
        cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', %s) "
                    "RETURNING id;", (lead, estado))
        op = cur.fetchone()[0]
        # Solo si se pidió una fecha de presupuesto.
        if presupuesto_creado is not None:
            # SQL: su presupuesto (sin importes: no hacen falta) con la fecha fijada.
            cur.execute("INSERT INTO presupuestos (oportunidad_id, created_at) VALUES (%s, %s);",
                        (op, presupuesto_creado))
        return op

    def visita(op, fecha, estado):
        """Inserta una visita propia en esa oportunidad."""
        # SQL: la visita, con un texto fijo (texto_cliente es obligatorio).
        cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                    "VALUES (%s, %s, %s, 'prueba de límite');", (op, fecha, estado))

    # Un microsegundo: la unidad más pequeña de timestamptz.
    us = timedelta(microseconds=1)
    # Gates: uno con 24 h EXACTAS (no debe salir) y otro con 24 h + 1 µs (sí).
    g_exacto = oportunidad("pendiente_aprobacion", ahora - timedelta(hours=24))
    g_dentro = oportunidad("pendiente_aprobacion", ahora - timedelta(hours=24) - us)
    # Seguimientos: lo mismo con 48 h.
    s_exacto = oportunidad("presupuesto_enviado", ahora - timedelta(hours=48))
    s_dentro = oportunidad("presupuesto_enviado", ahora - timedelta(hours=48) - us)
    # Visitas en los extremos del día (cada una en su oportunidad, por el
    # índice de una visita activa por oportunidad).
    # En el inicio exacto del día (00:00 de Madrid): dentro de e).
    v_inicio = oportunidad("visita_agendada")
    visita(v_inicio, inicio, "solicitada")
    # En el fin exacto (00:00 del día siguiente): fuera de e), y por ser
    # 'solicitada', dentro de d).
    v_fin = oportunidad("visita_agendada")
    visita(v_fin, fin, "solicitada")

    # Las cinco consultas con el MISMO cursor y el mismo now().
    listado = servicio.leer_listado(cur, {G: 24, S: 48}, ahora)
    # Los ids de cada apartado, para buscar los propios. getattr(listado, n)
    # lee el atributo cuyo nombre es el texto n (por ejemplo, la lista
    # listado.gates_sin_decision).
    ids = {n: [e.oportunidad_id for e in getattr(listado, n)] for n in
           ("gates_sin_decision", "seguimientos_por_abrir", "visitas_sin_confirmar", "visitas_proximo_laborable")}
    # Los límites del plazo: exacto fuera, un microsegundo más dentro.
    comprobar("Gate de 24 h exactas -> NO sale (límite estricto)", g_exacto not in ids["gates_sin_decision"])
    comprobar("Gate de 24 h + 1 µs -> sale", g_dentro in ids["gates_sin_decision"])
    comprobar("seguimiento de 48 h exactas -> NO sale", s_exacto not in ids["seguimientos_por_abrir"])
    comprobar("seguimiento de 48 h + 1 µs -> sale", s_dentro in ids["seguimientos_por_abrir"])
    # Los extremos del día: el inicio entra en e) y no en d)...
    comprobar(f"visita en el inicio exacto del {dia} -> sale en e)", v_inicio in ids["visitas_proximo_laborable"])
    comprobar("  ... y NO en d)", v_inicio not in ids["visitas_sin_confirmar"])
    # ...y el fin no entra en e), pero sí en d).
    comprobar("visita en el fin exacto (00:00 del día siguiente) -> NO sale en e)",
              v_fin not in ids["visitas_proximo_laborable"])
    comprobar("  ... y sí en d) (es 'solicitada')", v_fin in ids["visitas_sin_confirmar"])
# El código bajo prueba intentó confirmar: FALLO contado (no una traza), y
# el finally lo deshace todo. Las comprobaciones de arriba no llegan a
# ejecutarse, así que el total baja (es lo esperado).
except CommitProhibido:
    comprobar("leer_listado no llama a commit()", False, "(llamó a commit(); el ROLLBACK lo deshace)")
finally:
    # ROLLBACK: nada de lo de arriba queda en la base de datos.
    cn.rollback()
# SQL: comprobación de que no queda ningún cliente con la marca.
cur.execute("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON,))
restos = cur.fetchone()[0]
# Se cierra esa lectura y la conexión.
cn.rollback()
cn.close()
comprobar("tras el ROLLBACK no queda ningún dato propio", restos == 0, f"({restos})")

# ======================================================================
print("\nS6. Modo de la transacción de obtener_listado")
# ======================================================================
# obtener_listado usa el pool del backend.
db.init_pool()


class Visto(Exception):
    """Excepción propia con lo que vio el espía."""


def espia(cursor, reglas, ahora):
    """Sustituye a leer_listado: lee el modo de la transacción y corta."""
    # SQL: el nivel de aislamiento de la transacción en curso.
    cursor.execute("SHOW transaction_isolation;")
    aislamiento = cursor.fetchone()[0]
    # SQL: si la transacción es de solo lectura ("on") o no ("off").
    cursor.execute("SHOW transaction_read_only;")
    # Se corta con la excepción propia, que lleva los dos valores.
    raise Visto(aislamiento, cursor.fetchone()[0])


# Se guarda la función real y se pone el espía en su lugar (EN MEMORIA:
# el archivo del servicio no cambia).
original_leer = servicio.leer_listado
servicio.leer_listado = espia
# obtener_listado fija el modo y, al llamar a "leer_listado", llama al espía.
try:
    servicio.obtener_listado()
# Si sale lo esperado, se miran los dos valores.
except Visto as visto:
    comprobar("aislamiento = repeatable read", visto.args[0] == "repeatable read", f"({visto.args[0]})")
    comprobar("transaction_read_only = on", visto.args[1] == "on", f"({visto.args[1]})")
# else: obtener_listado terminó sin pasar por el espía.
else:
    comprobar("el espía se ejecutó", False, "(la excepción se ha tragado)")
finally:
    # Se restaura SIEMPRE la función real.
    servicio.leer_listado = original_leer

# ======================================================================
print("\nS7. obtener_listado -> ConfiguracionIncompleta (503), cambios EN MEMORIA")
# ======================================================================
# a) Una clave con un nombre falso: la fila real no se toca.
# Nombre falso y único; se guarda el real para restaurarlo.
falsa = f"clave_falsa_{uuid.uuid4().hex}"
real = servicio.CLAVE_SEGUIMIENTO
servicio.CLAVE_SEGUIMIENTO = falsa
# Con la clave falsa, la regla "no existe" y tiene que salir el 503.
try:
    servicio.obtener_listado()
# Lo esperado: ConfiguracionIncompleta, con la clave falsa en "faltan".
except ConfiguracionIncompleta as error:
    comprobar("clave falsa -> ConfiguracionIncompleta, motivo configuracion_incompleta",
              error.motivo == "configuracion_incompleta")
    comprobar("  faltan nombra la clave falsa", any(falsa in f for f in error.faltan), f"({error.faltan})")
# Si no salió ninguna excepción: FALLO explícito.
else:
    comprobar("clave falsa -> ConfiguracionIncompleta", False, "(la excepción se ha tragado)")
# Siempre: se restaura el nombre real.
finally:
    servicio.CLAVE_SEGUIMIENTO = real

# b) Valor fuera de rango inyectado: se envuelve validar_reglas_listado
#    para que reciba 99999999 en lugar del valor leído y llame a la real.
original_validar = servicio.validar_reglas_listado


def inyectar(leidas):
    """Copia lo leído, cambia el valor de seguimiento y valida de verdad."""
    # dict(leidas): una COPIA, para no modificar el diccionario original.
    leidas = dict(leidas)
    # Se sustituye el valor de seguimiento por el mayor entero de NUMERIC(10,2).
    leidas[S] = Decimal("99999999.00")
    # Y se valida con la función real.
    return original_validar(leidas)


# Se pone el envoltorio en lugar de la función real (EN MEMORIA).
servicio.validar_reglas_listado = inyectar
try:
    servicio.obtener_listado()
# Lo esperado: el 503 controlado, con el rango en "faltan".
except ConfiguracionIncompleta as error:
    comprobar("99999999 -> ConfiguracionIncompleta (no un error de Postgres)",
              error.motivo == "configuracion_incompleta")
    comprobar("  faltan dice el rango 1-8760", any("entre 1 y 8760" in f for f in error.faltan), f"({error.faltan})")
# Cualquier otra excepción (DatetimeFieldOverflow, N12) es un FALLO.
except Exception as error:
    comprobar("99999999 -> ConfiguracionIncompleta", False, f"(salió {type(error).__name__}: {error})")
# Ninguna excepción: también FALLO.
else:
    comprobar("99999999 -> ConfiguracionIncompleta", False, "(la excepción se ha tragado)")
# Siempre: se restaura la función real.
finally:
    servicio.validar_reglas_listado = original_validar

# Se cierra el pool.
db.close_pool()

# Resumen final y código de salida: 1 si algo falló (para la suite).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con algún fallo: se listan y se sale con código 1.
if fallos:
    # Título de cada comprobación fallida, una por línea.
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    # Código de salida 1: la suite lo cuenta como fallo sin leer la salida.
    sys.exit(1)
