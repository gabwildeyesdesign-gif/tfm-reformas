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
# Se pone la raíz en la posición 0 de la lista de carpetas donde Python busca módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: conexión propia para mirar y limpiar.
import psycopg2

# "import ... as ...": el módulo entero con un nombre corto. Hace falta el
# MÓDULO (no solo sus funciones) para poder cambiar sus constantes en memoria.
import app.services.reglas_visita as reglas_visita
# DATABASE_URL: la cadena de conexión a Supabase, leída del .env.
from app.config import DATABASE_URL
# El pool de conexiones que usan los servicios (init_pool / close_pool).
from app.db import connection as db
# Los esquemas de entrada de cada endpoint.
from app.schemas.gate_decisions import GateDecisionCreate
# LeadCreate: el esquema de entrada de POST /leads.
from app.schemas.leads import LeadCreate
# VisitaCreate: el esquema de entrada de POST /visits.
from app.schemas.visits import VisitaCreate
# Los servicios: crear lead, calcular, pedir visita y registrar decisión.
from app.services.estimate_service import calculate_estimate
# registrar_decision: la función del servicio de /gate-decisions que se prueba.
from app.services.gate_decisions_service import registrar_decision
# create_lead: la función que da de alta un lead (la usa lead_propio).
from app.services.leads_service import create_lead
# solicitar_visita: la función del servicio de /visits (caso 2).
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
# fallos: lista vacía; cada comprobación que falle añade aquí su texto.
fallos = []


# comprobar: se define una vez y se usa en todas las comprobaciones.
def comprobar(titulo, condicion, detalle=""):
    """Anota una comprobación: [OK] si condicion es verdadera, [FALLO] si no."""
    # global ok: dentro de la función se modifica la variable ok de fuera, no una copia.
    global ok
    # Si la condición es verdadera, la comprobación pasa:
    if condicion:
        # se suma 1 al contador de correctas...
        ok += 1
        # ...y se imprime [OK] con el título y el detalle.
        print(f"  [OK]    {titulo}  {detalle}")
    # Si no (else), la comprobación falla:
    else:
        # se guarda el texto del fallo para el resumen final...
        fallos.append(f"{titulo} {detalle}")
        # ...y se imprime [FALLO].
        print(f"  [FALLO] {titulo}  {detalle}")


# Conexión propia (aparte del pool de los servicios) y su cursor.
cn = psycopg2.connect(DATABASE_URL)
# El cursor: el objeto con el que se envían las consultas por esa conexión.
cur = cn.cursor()


# consultar: atajo para las lecturas de una sola fila.
def consultar(sql, params=()):
    """SELECT de una fila; el commit cierra la transacción de lectura para
    que la siguiente consulta vea lo último que hayan escrito los servicios."""
    # Se envía la consulta; params rellena los %s de forma segura (sin pegar texto en el SQL).
    cur.execute(sql, params)
    # fetchone(): la primera fila del resultado, como tupla (o None si no hay ninguna).
    fila = cur.fetchone()
    # commit: cierra la transacción de lectura.
    cn.commit()
    # Se devuelve la fila a quien llamó.
    return fila


# limpiar_propio: se llama al empezar (restos) y al terminar.
def limpiar_propio():
    """Solo lo propio, en el orden de las claves foráneas: logs ->
    decisiones_gate -> visitas -> presupuestos -> oportunidades -> leads ->
    clientes. Todo en una transacción (un solo commit al final)."""
    # Subconsulta con los ids de las oportunidades propias.
    ops = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
    # 1. Los logs de las oportunidades propias.
    cur.execute(f"DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id IN ({ops});", {"p": PATRON})
    # 2. Sus decisiones del Gate (apuntan a visitas: van antes que ellas).
    cur.execute(f"DELETE FROM decisiones_gate WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    # 3. Sus visitas.
    cur.execute(f"DELETE FROM visitas WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    # 4. Sus presupuestos.
    cur.execute(f"DELETE FROM presupuestos WHERE oportunidad_id IN ({ops});", {"p": PATRON})
    # 5. Las oportunidades.
    cur.execute(f"DELETE FROM oportunidades WHERE id IN ({ops});", {"p": PATRON})
    # 6. Los leads de los clientes propios.
    cur.execute("DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s);", {"p": PATRON})
    # 7. Los clientes propios.
    cur.execute("DELETE FROM clientes WHERE email LIKE %(p)s;", {"p": PATRON})
    # Un solo commit: los siete borrados se confirman juntos.
    cn.commit()


# lead_propio: prepara un lead de prueba con su presupuesto.
def lead_propio(estructural):
    """Crea un lead propio y calcula su presupuesto, llamando a los
    servicios. estructural=True activa el Gate. Devuelve (token, oportunidad_id)."""
    # Token único de esta ejecución; sus 8 primeros caracteres van en el email (la marca propia).
    token = str(uuid.uuid4())
    # create_lead da de alta cliente, lead y oportunidad, como POST /leads, pero sin HTTP.
    alta = create_lead(LeadCreate(
        nombre="Prueba servicio gate", email=f"check-gate-svc-{token[:8]}@example.com",
        telefono="600000000", tipo_reforma="bano", m2=Decimal("6"), nivel_acabados="medio",
        incluye_cambios_estructurales=estructural, fotos=[], lead_token=token,
    ))
    # calculate_estimate calcula el presupuesto (con Gate si estructural=True).
    calculo = calculate_estimate(alta.oportunidad_id)
    # Se enseña el estado, para ver en la salida que el lead quedó como se esperaba.
    print(f"  lead propio: oportunidad {alta.oportunidad_id}, estado tras calcular: {calculo.status}")
    # Devuelve dos valores a la vez (una tupla): el token y el id de la oportunidad.
    return token, alta.oportunidad_id


# logs_config: lee los logs del 503 de una oportunidad.
def logs_config(op):
    """Detalles (JSON) de los logs visita_configuracion_incompleta de una oportunidad."""
    # Los logs de esa acción para esa oportunidad, en orden de creación.
    cur.execute(
        "SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
        "AND accion = 'visita_configuracion_incompleta' ORDER BY id;",
        (op,),
    )
    # fetchall() da todas las filas; [f[0] for f in ...] se queda con la única columna de cada una (el detalle).
    filas = [f[0] for f in cur.fetchall()]
    # commit: cierra la transacción de lectura.
    cn.commit()
    # Lista de detalles (diccionarios), vacía si no hay ninguno.
    return filas


# esperar_503: comprueba que una llamada termina en el 503 de configuración.
def esperar_503(titulo, llamada):
    """
    Ejecuta llamada() y comprueba que sale ConfiguracionIncompleta con el
    motivo y la clave que falta. Tres salidas posibles del try:
      - la excepción esperada  -> se comprueba su contenido;
      - otra excepción         -> FALLO, se dice cuál;
      - NINGUNA excepción      -> rama else: FALLO explícito ("se ha
        tragado"), regla de CLAUDE.md (caso D16).
    """
    # try: se intenta la llamada; si lanza una excepción, Python salta al except que corresponda.
    try:
        # La llamada que se prueba (llega como una función sin argumentos, una lambda).
        llamada()
    # Caso esperado: sale ConfiguracionIncompleta; 'error' es la excepción capturada.
    except ConfiguracionIncompleta as error:
        # Se enseña qué salió, con su motivo y la lista de lo que falta.
        print(f"  excepción: {type(error).__name__} motivo={error.motivo} faltan={error.faltan}")
        # Primera comprobación: el motivo estable es configuracion_incompleta.
        comprobar(f"{titulo}: sale ConfiguracionIncompleta con motivo configuracion_incompleta",
                  error.motivo == "configuracion_incompleta")
        # Segunda: 'faltan' nombra la clave falsa que se puso en memoria.
        comprobar(f"{titulo}: 'faltan' nombra la clave que falta", f"{CLAVE_FALSA}: no existe" in error.faltan)
    # Cualquier otra excepción: FALLO, diciendo cuál salió.
    except Exception as error:
        comprobar(f"{titulo}: sale ConfiguracionIncompleta", False, f"(salió {type(error).__name__}: {error})")
    # else de un try: solo se ejecuta si NO salió ninguna excepción.
    else:
        comprobar(f"{titulo}: sale ConfiguracionIncompleta", False, "(la excepción se ha tragado: no salió nada)")


# Restos de una ejecución anterior cortada (solo lo propio).
limpiar_propio()
# Los servicios piden conexiones al pool: hay que abrirlo (y cerrarlo al final).
db.init_pool()
# Se guarda el nombre real ANTES de tocar nada, para restaurarlo seguro.
clave_real = reglas_visita.CLAVE_DURACION
# La tupla con las cinco claves reales, también guardada para restaurarla.
claves_reales = reglas_visita.CLAVES_REGLAS
# try/finally: pase lo que pase dentro, el bloque finally restaura las claves y limpia.
try:
    # Título de la sección en la salida ("=" * 78 repite el signo 78 veces).
    print("=" * 78)
    print("PREPARACIÓN: tres leads propios")
    print("=" * 78)
    # A: con Gate, para visita_acordada. B: sin Gate, para /visits. C: con
    # Gate, para descartar.
    _, op_a = lead_propio(estructural=True)
    # B: sin Gate (queda en presupuesto_enviado), para probar /visits.
    token_b, op_b = lead_propio(estructural=False)
    # C: con Gate, para el descarte del caso 3 ('_' recoge el token, que no se usa).
    _, op_c = lead_propio(estructural=True)

    # Cambio SOLO EN MEMORIA: leer_reglas_visita() lee estas constantes del
    # módulo reglas_visita cada vez que se ejecuta, así que ve el nombre
    # falso. tuple(... for c in ...): una tupla nueva con la clave cambiada.
    reglas_visita.CLAVE_DURACION = CLAVE_FALSA
    # La tupla de las cinco claves, con la real sustituida por la falsa.
    reglas_visita.CLAVES_REGLAS = tuple(CLAVE_FALSA if c == clave_real else c for c in claves_reales)

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    # Título del caso 1.
    print("1. POST /gate-decisions, visita_acordada, con la configuración rota")
    print("=" * 78)
    # Se pide una visita_acordada para A: debe salir el 503.
    esperar_503("gate-decisions", lambda: registrar_decision(GateDecisionCreate(
        oportunidad_id=op_a, decision="visita_acordada", fecha=FECHA, hora="08:30", informe="prueba 503")))
    # Los logs de configuración incompleta que han quedado en A.
    logs = logs_config(op_a)
    # Se enseñan tal cual, para poder leerlos en la salida.
    print(f"  logs visita_configuracion_incompleta: {logs}")
    # Debe haber exactamente uno: se conservó aunque la petición falló.
    comprobar("gate-decisions: el log SE CONSERVA (rechazo tras el commit), exactamente 1", len(logs) == 1)
    # Y debe llevar el origen de este endpoint y la clave en la lista de problemas.
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
    # Se enseña la fila para poder leerla en la salida.
    print(f"  (decisiones, visitas, logs de decisión, estado, fecha_ultimo_contacto) = {fila}")
    # fila[:3]: los tres primeros valores (los recuentos); deben ser 0, 0, 0.
    comprobar("gate-decisions: 0 decisiones, 0 visitas, 0 logs de decisión", fila[:3] == (0, 0, 0))
    # fila[3] y fila[4]: el estado y el contacto, sin cambios.
    comprobar("gate-decisions: la oportunidad sigue en pendiente_aprobacion y sin contacto",
              fila[3] == "pendiente_aprobacion" and fila[4] is None)

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    # Título del caso 2.
    print("2. POST /visits por el mismo camino")
    print("=" * 78)
    # Se pide una visita por /visits para B: debe salir el mismo 503.
    esperar_503("visits", lambda: solicitar_visita(VisitaCreate(
        lead_token=token_b, fecha=FECHA, hora="08:30", texto_cliente="prueba 503")))
    # Los logs del 503 en B.
    logs = logs_config(op_b)
    # Se enseñan en la salida.
    print(f"  logs visita_configuracion_incompleta: {logs}")
    # Exactamente uno, firmado con el origen de /visits.
    comprobar("visits: el log se conserva, exactamente 1, con origen 'visits'",
              len(logs) == 1 and logs[0].get("origen") == "visits", f"({logs})")
    # [0]: la consulta devuelve una tupla de un valor (el recuento); se saca el número.
    n_visitas = consultar("SELECT count(*) FROM visitas WHERE oportunidad_id = %s;", (op_b,))[0]
    # Ninguna visita creada.
    comprobar("visits: no se ha creado ninguna visita", n_visitas == 0, f"({n_visitas})")

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    # Título del caso 3.
    print("3. POST /gate-decisions, descartar, con la configuración IGUAL de rota")
    print("=" * 78)
    # Aquí NO se espera excepción: descartar no lee las reglas de visita.
    try:
        # Se registra un descarte para C, con la configuración todavía rota.
        respuesta = registrar_decision(GateDecisionCreate(
            oportunidad_id=op_c, decision="descartar", motivo="no_contesta", informe="No contesta en tres intentos."))
        # Se enseña la respuesta del servicio.
        print(f"  respuesta: {respuesta}")
        # Debe crearse (creado=True) y dejar la oportunidad en 'perdida'.
        comprobar("descartar: se registra (creado=true, estado perdida)",
                  respuesta.creado is True and respuesta.estado_oportunidad == "perdida")
    # Si salió cualquier excepción, el descarte no se registró: FALLO.
    except Exception as error:
        comprobar("descartar: se registra", False, f"(salió {type(error).__name__}: {error})")
    # Y no debe haber ningún log de configuración incompleta en C.
    comprobar("descartar: ningún log de configuración incompleta", logs_config(op_c) == [])
# finally: se ejecuta SIEMPRE, haya ido bien o mal lo de arriba.
finally:
    # Se restauran SIEMPRE los nombres reales, aunque algo haya fallado.
    reglas_visita.CLAVE_DURACION = clave_real
    # La tupla de claves real, también restaurada.
    reglas_visita.CLAVES_REGLAS = claves_reales
    # Comprobación de que la restauración ha funcionado.
    comprobar("los nombres reales de las claves quedan restaurados en memoria",
              reglas_visita.CLAVE_DURACION == "duracion_visita_min" and CLAVE_FALSA not in reglas_visita.CLAVES_REGLAS)
    # Se cierra el pool de conexiones de los servicios.
    db.close_pool()
    # Se borra todo lo propio.
    limpiar_propio()
    # Se cuentan los clientes de prueba que queden (deben ser 0).
    restos = consultar("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON,))[0]
    # Se cierra la conexión propia.
    cn.close()
    # Se enseña el recuento.
    print(f"  limpieza: clientes de prueba restantes = {restos}")
    # Comprobación final de la limpieza.
    comprobar("no queda ningún dato de prueba", restos == 0)

# Resumen y código de salida (1 si algo falló, para la suite).
total = ok + len(fallos)
# Línea de separación y resultado: correctas / total.
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Si hubo algún fallo...
if fallos:
    # ...se imprime la lista...
    print("FALLOS:")
    # (un bucle for: repite la línea de debajo para cada texto de fallos)
    for f in fallos:
        print(f"  - {f}")
    # ...y el script termina con código 1, que la suite interpreta como FALLO.
    sys.exit(1)
