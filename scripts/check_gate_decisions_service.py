"""
Verificación SIN servidor de la configuración incompleta (503) en los dos
endpoints que comparten las reglas de visita (plan:
docs/Plan_Endpoint_Gate_Decisions.txt, secciones 2.3 y 4.2).

Se llama directamente a las funciones de services/ (sin HTTP), contra la
base de datos real, con datos propios (emails
check-gate-svc-<8 caracteres>@example.com), borrados al terminar.

Cómo se provoca el 503 sin tocar nada compartido: el NOMBRE de una clave de
reglas_visita (CLAVE_DURACION) se cambia EN MEMORIA, solo dentro de este
proceso, por uno que no existe. leer_reglas_visita() busca ese nombre falso
en reglas_negocio, no lo encuentra, y lo da como problema. La fila real
duracion_visita_min no se toca nunca, y el nombre se restaura al terminar
(bloque finally).

Casos:
  1. POST /gate-decisions con visita_acordada: sale ConfiguracionIncompleta,
     el log se CONSERVA (el rechazo se lanza después del commit) con
     origen "gate_decisions", y no se escribe ninguna decisión, visita ni
     cambio en la oportunidad.
  2. POST /visits por el mismo camino: log con origen "visits".
  3. POST /gate-decisions con descartar, con la configuración igual de
     rota: SÍ se registra, porque un descarte no tiene visita y no lee las
     reglas de visita (el 503 es "solo visita_acordada", tabla 1.8).
"""

# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# uuid: tokens únicos en cada ejecución.
import uuid
# Decimal: número decimal exacto (m2 se pasa así a LeadCreate).
from decimal import Decimal
# Path: rutas de archivos independientes del sistema operativo.
from pathlib import Path

# La raíz del repositorio (carpeta padre de scripts/) al principio de
# sys.path, para que "import app..." encuentre el paquete.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: conexión propia para mirar y limpiar.
import psycopg2

# "import ... as ...": el módulo entero con un nombre corto. Hace falta el
# MÓDULO (no solo sus funciones) para poder cambiar sus constantes en memoria.
import app.services.reglas_visita as reglas_visita
from app.config import DATABASE_URL
# El pool de conexiones que usan los servicios (init_pool / close_pool).
from app.db import connection as db
# Los esquemas de entrada de cada endpoint.
from app.schemas.gate_decisions import GateDecisionCreate
from app.schemas.leads import LeadCreate
from app.schemas.visits import VisitaCreate
# Los servicios: crear lead, calcular, pedir visita y registrar decisión.
from app.services.estimate_service import calculate_estimate
from app.services.gate_decisions_service import registrar_decision
from app.services.leads_service import create_lead
from app.services.visits_service import solicitar_visita
# La excepción que se espera en los casos 1 y 2.
from app.services.reglas_visita import ConfiguracionIncompleta

# Marca propia de los datos de este script.
PATRON = "check-gate-svc-%@example.com"
# Nombre falso que sustituye EN MEMORIA a la clave real de la duración.
CLAVE_FALSA = "clave_inexistente_check_gate_svc"
# Lunes 7 de enero de 2030: una fecha futura y laborable. En los casos 1 y
# 2 no llega a validarse (la configuración falla antes).
FECHA = "2030-01-07"

# Número de comprobaciones correctas y lista de las que fallan.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Anota una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"  [FALLO] {titulo}  {detalle}")


# Conexión propia (aparte del pool de los servicios) y su cursor.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def consultar(sql, params=()):
    """SELECT de una fila; el commit cierra la transacción de lectura para
    que la siguiente consulta vea lo último que hayan escrito los servicios."""
    cur.execute(sql, params)
    fila = cur.fetchone()
    cn.commit()
    return fila


def limpiar_propio():
    """Solo lo propio, en el orden de las claves foráneas: logs ->
    decisiones_gate -> visitas -> presupuestos -> oportunidades -> leads ->
    clientes. Todo en una transacción (un solo commit al final)."""
    # Subconsulta con los ids de las oportunidades propias.
    ops = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
    cur.execute(f"DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM decisiones_gate WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM visitas WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM presupuestos WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    cur.execute(f"DELETE FROM oportunidades WHERE id IN ({ops});", {"p": PATRON})
    cur.execute("DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s);", {"p": PATRON})
    cur.execute("DELETE FROM clientes WHERE email LIKE %(p)s;", {"p": PATRON})
    cn.commit()


def lead_propio(estructural):
    """Crea un lead propio y calcula su presupuesto, llamando a los
    servicios. estructural=True activa el Gate. Devuelve (token, oportunidad_id)."""
    token = str(uuid.uuid4())
    alta = create_lead(LeadCreate(
        nombre="Prueba servicio gate", email=f"check-gate-svc-{token[:8]}@example.com",
        telefono="600000000", tipo_reforma="bano", m2=Decimal("6"), nivel_acabados="medio",
        incluye_cambios_estructurales=estructural, fotos=[], lead_token=token,
    ))
    calculo = calculate_estimate(alta.oportunidad_id)
    print(f"  lead propio: oportunidad {alta.oportunidad_id}, estado tras calcular: {calculo.status}")
    return token, alta.oportunidad_id


def logs_config(op):
    """Detalles (JSON) de los logs visita_configuracion_incompleta de una oportunidad."""
    cur.execute(
        "SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
        "AND accion = 'visita_configuracion_incompleta' ORDER BY id;",
        (op,),
    )
    filas = [f[0] for f in cur.fetchall()]
    cn.commit()
    return filas


def esperar_503(titulo, llamada):
    """
    Ejecuta llamada() y comprueba que sale ConfiguracionIncompleta con el
    motivo y la clave que falta. Tres salidas posibles del try:
      - la excepción esperada  -> se comprueba su contenido;
      - otra excepción         -> FALLO, se dice cuál;
      - NINGUNA excepción      -> rama else: FALLO explícito ("se ha
        tragado"), regla de CLAUDE.md (caso D16).
    """
    try:
        llamada()
    except ConfiguracionIncompleta as error:
        print(f"  excepción: {type(error).__name__} motivo={error.motivo} faltan={error.faltan}")
        comprobar(f"{titulo}: sale ConfiguracionIncompleta con motivo configuracion_incompleta",
                  error.motivo == "configuracion_incompleta")
        comprobar(f"{titulo}: 'faltan' nombra la clave que falta", f"{CLAVE_FALSA}: no existe" in error.faltan)
    except Exception as error:
        comprobar(f"{titulo}: sale ConfiguracionIncompleta", False, f"(salió {type(error).__name__}: {error})")
    else:
        comprobar(f"{titulo}: sale ConfiguracionIncompleta", False, "(la excepción se ha tragado: no salió nada)")


# Restos de una ejecución anterior cortada (solo lo propio).
limpiar_propio()
# Los servicios piden conexiones al pool: hay que abrirlo (y cerrarlo al final).
db.init_pool()
# Se guarda el nombre real ANTES de tocar nada, para restaurarlo seguro.
clave_real = reglas_visita.CLAVE_DURACION
claves_reales = reglas_visita.CLAVES_REGLAS
try:
    print("=" * 78)
    print("PREPARACIÓN: tres leads propios")
    print("=" * 78)
    # A: con Gate, para visita_acordada. B: sin Gate, para /visits. C: con
    # Gate, para descartar.
    _, op_a = lead_propio(estructural=True)
    token_b, op_b = lead_propio(estructural=False)
    _, op_c = lead_propio(estructural=True)

    # Cambio SOLO EN MEMORIA: leer_reglas_visita() lee estas constantes del
    # módulo reglas_visita cada vez que se ejecuta, así que ve el nombre
    # falso. tuple(... for c in ...): una tupla nueva con la clave cambiada.
    reglas_visita.CLAVE_DURACION = CLAVE_FALSA
    reglas_visita.CLAVES_REGLAS = tuple(CLAVE_FALSA if c == clave_real else c for c in claves_reales)

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("1. POST /gate-decisions, visita_acordada, con la configuración rota")
    print("=" * 78)
    esperar_503("gate-decisions", lambda: registrar_decision(GateDecisionCreate(
        oportunidad_id=op_a, decision="visita_acordada", fecha=FECHA, hora="08:30", informe="prueba 503")))
    logs = logs_config(op_a)
    print(f"  logs visita_configuracion_incompleta: {logs}")
    comprobar("gate-decisions: el log SE CONSERVA (rechazo tras el commit), exactamente 1", len(logs) == 1)
    comprobar("gate-decisions: el log lleva origen 'gate_decisions' y la clave en problemas",
              len(logs) == 1 and logs[0].get("origen") == "gate_decisions"
              and any(CLAVE_FALSA in p for p in logs[0].get("problemas", [])), f"({logs})")
    # Nada más escrito: ni decisión, ni visita, ni log de decisión, y la
    # oportunidad intacta (estado y fecha_ultimo_contacto).
    fila = consultar(
        """
        SELECT (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM visitas WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s
                  AND accion = 'gate_decision_registrada'),
               o.estado, o.fecha_ultimo_contacto
        FROM oportunidades o WHERE o.id = %(op)s;
        """,
        {"op": op_a},
    )
    print(f"  (decisiones, visitas, logs de decisión, estado, fecha_ultimo_contacto) = {fila}")
    comprobar("gate-decisions: 0 decisiones, 0 visitas, 0 logs de decisión", fila[:3] == (0, 0, 0))
    comprobar("gate-decisions: la oportunidad sigue en pendiente_aprobacion y sin contacto",
              fila[3] == "pendiente_aprobacion" and fila[4] is None)

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("2. POST /visits por el mismo camino")
    print("=" * 78)
    esperar_503("visits", lambda: solicitar_visita(VisitaCreate(
        lead_token=token_b, fecha=FECHA, hora="08:30", texto_cliente="prueba 503")))
    logs = logs_config(op_b)
    print(f"  logs visita_configuracion_incompleta: {logs}")
    comprobar("visits: el log se conserva, exactamente 1, con origen 'visits'",
              len(logs) == 1 and logs[0].get("origen") == "visits", f"({logs})")
    n_visitas = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s;", (op_b,))[0]
    comprobar("visits: no se ha creado ninguna visita", n_visitas == 0, f"({n_visitas})")

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("3. POST /gate-decisions, descartar, con la configuración IGUAL de rota")
    print("=" * 78)
    # Aquí NO se espera excepción: descartar no lee las reglas de visita.
    try:
        respuesta = registrar_decision(GateDecisionCreate(
            oportunidad_id=op_c, decision="descartar", motivo="no_contesta", informe="No contesta en tres intentos."))
        print(f"  respuesta: {respuesta}")
        comprobar("descartar: se registra (creado=true, estado perdida)",
                  respuesta.creado is True and respuesta.estado_oportunidad == "perdida")
    except Exception as error:
        comprobar("descartar: se registra", False, f"(salió {type(error).__name__}: {error})")
    comprobar("descartar: ningún log de configuración incompleta", logs_config(op_c) == [])
finally:
    # Se restauran SIEMPRE los nombres reales, aunque algo haya fallado.
    reglas_visita.CLAVE_DURACION = clave_real
    reglas_visita.CLAVES_REGLAS = claves_reales
    comprobar("los nombres reales de las claves quedan restaurados en memoria",
              reglas_visita.CLAVE_DURACION == "duracion_visita_min" and CLAVE_FALSA not in reglas_visita.CLAVES_REGLAS)
    db.close_pool()
    limpiar_propio()
    restos = consultar("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON,))[0]
    cn.close()
    print(f"  limpieza: clientes de prueba restantes = {restos}")
    comprobar("no queda ningún dato de prueba", restos == 0)

# Resumen y código de salida (1 si algo falló, para la suite).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
