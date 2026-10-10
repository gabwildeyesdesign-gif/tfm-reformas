"""
Verificación SIN servidor HTTP de app/services/ficha_oportunidad_service.py
(plan: docs/Plan_Endpoint_Ficha_Oportunidad.txt, sección 8.1).

  S0. La conexión de prueba rechaza commit() (CommitProhibido).
  S1. estado_tras (pura): la tabla acción -> estado de 5.1, caso por caso.
  S2. construir_historial y historial_cuadra (puras): nunca un evento
      inventado; un log que no se entiende no sale y no cuadra; el orden
      recibido se conserva (el desempate por id lo pone el ORDER BY de la
      consulta 4 y se prueba por HTTP, en check_ficha_oportunidad_http).
  S3. Reutilización sin copia, con ast.parse.
  S4. REPEATABLE READ de verdad: un cambio CONFIRMADO por otra conexión en
      mitad de la ficha no se ve en ella.
  S5. 404 desde el servicio, con la rama else que marca FALLO.

Marca propia: emails svc-ficha-<8 caracteres>@example.com.

LAS 4 PREGUNTAS (regla de CLAUDE.md, para la base de datos REAL):
  (1) ¿Escribe en una tabla real? Solo la S4, y solo filas PROPIAS: un
      cliente con la marca, su lead, su oportunidad, su presupuesto y sus
      logs. Las escribe y confirma la conexión de la PRUEBA (cn y
      cn_cambio), nunca la del código. S0, S1, S2, S3 y S5 no escriben.
  (2) ¿Puede el código bajo prueba (también una versión rota) confirmar,
      cerrar o reutilizar la conexión? En la S4 recibe una conexión propia
      abierta con connection_factory=ConexionSinCommit: si llama a
      commit(), sale CommitProhibido y se cuenta como FALLO. No puede
      cerrarla (el "with" falso se la devuelve al script, que hace
      ROLLBACK y la cierra) y no la ve fuera de la S4.
      get_transactional_connection NO se sustituye: una versión rota que
      escriba por el pool (N9b) solo puede escribir sobre la oportunidad
      propia que se le pide, y la comprobación "la ficha no escribe ningún
      log" de la S4 lo detecta; la limpieza la borra por la marca. La S5
      usa el pool real, con ids que no existen.
  (3) Si se corta a mitad, ¿cómo vuelve todo a su sitio y quién lo
      comprueba? Quedan filas propias con la marca: limpiar() las borra al
      EMPEZAR la siguiente ejecución, y la última comprobación cuenta 0
      restos. La transacción de la conexión del código muere con ella (no
      ha confirmado nada).
  (4) ¿Toca algo en uso? No arranca ningún servidor (ni el 8000 ni
      ningún otro puerto) y solo pide la ficha de ids propios o
      inexistentes; ninguna fila ajena se lee ni se cambia.
"""

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
# ast: para leer los servicios como árbol (S3).
import ast
# re: para buscar "detalle" sin "->>" detrás en los textos SQL (S3).
import re
# sys: sys.path, la salida y el código de salida.
import sys
# uuid: la marca aleatoria de esta ejecución.
import uuid
# contextmanager: para escribir la conexión falsa de la S4 como un "with".
from contextlib import contextmanager
# datetime, timedelta y timezone: fechas fijas para las funciones puras.
from datetime import datetime, timedelta, timezone
# Path: rutas de archivos.
from pathlib import Path

# La raíz del repositorio es la carpeta padre de scripts/. Se pone la
# primera en sys.path para que "import app..." encuentre el paquete.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: conexiones directas propias; Json convierte un dict en JSONB.
import psycopg2
from psycopg2.extras import Json

# La URL de la base de datos (nunca se imprime).
from app.config import DATABASE_URL
# El pool del backend (la S5 usa get_db_connection real).
from app.db import connection as db
# Lo que se prueba.
from app.services import ficha_oportunidad_service as servicio
# El texto del modo de la transacción, de su ÚNICO sitio (el listado).
from app.services.listado_llamadas_service import SQL_MODO_TRANSACCION

# ----------------------------------------------------------------------
# Contadores y marca
# ----------------------------------------------------------------------
# Comprobaciones correctas y títulos de las que fallan.
ok = 0
fallos = []
# Patrón LIKE de los clientes de ESTE script.
PATRON_EMAIL = "svc-ficha-%@example.com"


def comprobar(titulo, condicion_ok, detalle=""):
    """Imprime [OK] o [FALLO] y lleva la cuenta."""
    # global: el contador es el de fuera de la función.
    global ok
    # Correcta: suma; fallida: se apunta su título.
    if condicion_ok:
        ok += 1
    else:
        fallos.append(titulo)
    # La línea del resultado, con el detalle si lo hay.
    print(f"  [{'OK' if condicion_ok else 'FALLO'}] {titulo} {detalle}")


# ----------------------------------------------------------------------
# Conexión de prueba SIN commit (regla de CLAUDE.md desde el 2026-10-08)
# ----------------------------------------------------------------------
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en una conexión de prueba."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión normal, salvo commit(), que lanza un error. rollback()
    funciona como siempre."""

    def commit(self):
        # No se confirma NADA: la transacción sigue abierta para el ROLLBACK.
        raise CommitProhibido("commit() en una conexión de prueba")


# ----------------------------------------------------------------------
# Lo "propio": lo que cuelga de los clientes con la marca
# ----------------------------------------------------------------------
# Subconsultas encadenadas: clientes propios -> leads -> oportunidades.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPS = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# Para cada tabla, la condición de sus filas propias.
PROPIO = {
    "logs": f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})",
    "decisiones_gate": f"oportunidad_id IN ({SQL_OPS})",
    "visitas": f"oportunidad_id IN ({SQL_OPS})",
    "presupuestos": f"oportunidad_id IN ({SQL_OPS})",
    "oportunidades": f"id IN ({SQL_OPS})",
    "leads": f"id IN ({SQL_LEADS})",
    "clientes": f"id IN ({SQL_CLIENTES})",
}

# Conexión propia del script (NUNCA se pasa al código bajo prueba).
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def limpiar():
    """Borra SOLO lo propio, en el orden de las claves foráneas (el del
    diccionario PROPIO), en una sola transacción."""
    for tabla, condicion in PROPIO.items():
        # SQL: borra las filas propias de esa tabla.
        cur.execute(f"DELETE FROM {tabla} WHERE {condicion};", {"patron": PATRON_EMAIL})
    cn.commit()


def contar(tabla):
    """Filas propias de una tabla."""
    # SQL: cuántas filas propias tiene.
    cur.execute(f"SELECT count(*) FROM {tabla} WHERE {PROPIO[tabla]};", {"patron": PATRON_EMAIL})
    n = cur.fetchone()[0]
    # commit: cierra la transacción de lectura.
    cn.commit()
    return n


# Restos de una ejecución anterior cortada (solo lo propio).
limpiar()

# ======================================================================
print("S0. La conexión de prueba rechaza commit()")
# ======================================================================
# connection_factory: psycopg2 crea la conexión con esta clase.
conexion_prueba = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# try/except/else: sin la excepción, FALLO explícito (regla de CLAUDE.md).
try:
    conexion_prueba.commit()
except CommitProhibido:
    comprobar("commit() lanza CommitProhibido", True)
else:
    comprobar("commit() lanza CommitProhibido", False, "(sin error: la excepción se ha tragado)")
finally:
    # No había nada que deshacer; se cierra.
    conexion_prueba.rollback()
    conexion_prueba.close()

# ======================================================================
print("S1. estado_tras: la tabla de 5.1 (valores copiados del PLAN)")
# ======================================================================
# (acción, estado del detalle, decisión del detalle) -> estado esperado,
# escrito a mano desde la tabla 5.1 del plan, no desde el código.
CASOS_ESTADO = [
    (("presupuesto_calculado", "presupuesto_enviado", None), "presupuesto_enviado"),
    (("presupuesto_calculado", "pendiente_aprobacion", None), "pendiente_aprobacion"),
    (("visita_solicitada", None, None), "visita_agendada"),
    (("gate_decision_registrada", None, "visita_acordada"), "visita_agendada"),
    (("gate_decision_registrada", None, "descartar"), "perdida"),
    (("seguimiento_abierto", None, None), "seguimiento_pendiente"),
    # No interpretables: acciones que no cambian el estado...
    (("visita_sustituida", None, None), None),
    (("presupuesto_requiere_revision", None, None), None),
    (("visita_configuracion_incompleta", None, None), None),
    (("error_no_controlado", None, None), None),
    # ... y detalles sin la clave o con un valor desconocido.
    (("presupuesto_calculado", None, None), None),
    (("presupuesto_calculado", "ganada", None), None),
    (("gate_decision_registrada", None, None), None),
    (("gate_decision_registrada", None, "otra_cosa"), None),
]
for argumentos, esperado in CASOS_ESTADO:
    obtenido = servicio.estado_tras(*argumentos)
    # Un Enum (str) se compara con su texto; None con None.
    comprobar(f"estado_tras{argumentos} -> {esperado}", obtenido == esperado, f"({obtenido!r})")

# ======================================================================
print("S2. construir_historial: nada inventado y el orden recibido")
# ======================================================================
# Instantes fijos en UTC (octubre: Madrid +02:00).
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)


def pares(eventos):
    """[(evento, estado)] de una lista de EventoHistorial, como textos."""
    return [(e.evento.value, e.estado.value) for e in eventos]


# Sin logs y en 'nueva': solo el alta, y cuadra.
eventos, cuadra = servicio.construir_historial(T0, [], "nueva")
comprobar("sin logs y 'nueva' -> [alta/nueva], cuadra", pares(eventos) == [("alta", "nueva")] and cuadra is True,
          f"({pares(eventos)}, {cuadra})")
# El alta sale en hora de Madrid.
comprobar("el alta sale en hora de Madrid (+02:00)", eventos[0].fecha.isoformat() == "2026-10-01T10:00:00+02:00",
          f"({eventos[0].fecha.isoformat()})")
# Sin logs y 'ganada' (puesta a mano): solo el alta, SIN evento añadido.
eventos, cuadra = servicio.construir_historial(T0, [], "ganada")
comprobar("sin logs y 'ganada' -> [alta/nueva], NO cuadra, nada añadido",
          pares(eventos) == [("alta", "nueva")] and cuadra is False, f"({pares(eventos)}, {cuadra})")
# Un log que no se puede interpretar: no sale, y no cuadra.
eventos, cuadra = servicio.construir_historial(T0, [("presupuesto_calculado", T1, None, None)], "nueva")
comprobar("log sin 'estado' -> no sale y NO cuadra", pares(eventos) == [("alta", "nueva")] and cuadra is False,
          f"({pares(eventos)}, {cuadra})")
# Dos logs de cambio: salen en el orden recibido, y cuadra con el último.
LOGS = [("presupuesto_calculado", T1, "presupuesto_enviado", None), ("seguimiento_abierto", T2, None, None)]
eventos, cuadra = servicio.construir_historial(T0, LOGS, "seguimiento_pendiente")
comprobar("[calculado, seguimiento] -> en ese orden, cuadra",
          pares(eventos) == [("alta", "nueva"), ("presupuesto_calculado", "presupuesto_enviado"),
                             ("seguimiento_abierto", "seguimiento_pendiente")] and cuadra is True,
          f"({pares(eventos)}, {cuadra})")
# Mismo instante: se conserva el orden recibido (el del ORDER BY ..., id).
MISMO = [("presupuesto_calculado", T1, "pendiente_aprobacion", None),
         ("gate_decision_registrada", T1, None, "descartar")]
eventos, cuadra = servicio.construir_historial(T0, MISMO, "perdida")
comprobar("a igual fecha, el orden recibido se conserva",
          [e for e, _ in pares(eventos)] == ["alta", "presupuesto_calculado", "gate_decision_registrada"]
          and cuadra is True, f"({pares(eventos)}, {cuadra})")
# El estado actual no es el del último evento: no cuadra, y nada añadido.
eventos, cuadra = servicio.construir_historial(T0, LOGS[:1], "ganada")
comprobar("estado actual distinto del último evento -> NO cuadra, 2 eventos",
          len(eventos) == 2 and cuadra is False, f"({pares(eventos)}, {cuadra})")

# ======================================================================
print("S3. Reutilización sin copia (ast.parse)")
# ======================================================================
# Los tres archivos, leídos como árbol.
RUTAS = {
    "ficha": RAIZ_REPO / "app/services/ficha_oportunidad_service.py",
    "aviso": RAIZ_REPO / "app/services/aviso_gate_service.py",
    "datos": RAIZ_REPO / "app/services/datos_oportunidad.py",
}
ARBOLES = {nombre: ast.parse(ruta.read_text(encoding="utf-8")) for nombre, ruta in RUTAS.items()}


def definidas(arbol):
    """Nombres de las funciones y constantes de primer nivel del archivo."""
    nombres = {n.name for n in arbol.body if isinstance(n, ast.FunctionDef)}
    # Las asignaciones de primer nivel (constantes como SQL_MODO_TRANSACCION).
    nombres |= {t.id for n in arbol.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    return nombres


def importados_de(arbol, modulo):
    """Nombres importados de ese módulo (from modulo import ...)."""
    return {a.name for n in arbol.body if isinstance(n, ast.ImportFrom) and n.module == modulo for a in n.names}


# Ni la ficha ni el aviso definen las dos funciones compartidas.
for nombre in ("ficha", "aviso"):
    repetidas = definidas(ARBOLES[nombre]) & {"a_madrid", "deducir_sin_iva"}
    comprobar(f"{nombre}: no define a_madrid ni deducir_sin_iva", not repetidas, f"({sorted(repetidas)})")
# Las importan de datos_oportunidad (la ficha usa a_madrid y, para el sin
# IVA, campos_presupuesto, que llama a deducir_sin_iva).
DE_DATOS_FICHA = importados_de(ARBOLES["ficha"], "app.services.datos_oportunidad")
comprobar("ficha: importa a_madrid y campos_presupuesto de datos_oportunidad",
          {"a_madrid", "campos_presupuesto"} <= DE_DATOS_FICHA, f"({sorted(DE_DATOS_FICHA)})")
DE_DATOS_AVISO = importados_de(ARBOLES["aviso"], "app.services.datos_oportunidad")
comprobar("aviso: importa a_madrid y deducir_sin_iva de datos_oportunidad",
          {"a_madrid", "deducir_sin_iva"} <= DE_DATOS_AVISO, f"({sorted(DE_DATOS_AVISO)})")
# SQL_MODO_TRANSACCION: importada del listado, no definida en la ficha (P9).
comprobar("ficha: SQL_MODO_TRANSACCION importada del listado, no copiada",
          "SQL_MODO_TRANSACCION" in importados_de(ARBOLES["ficha"], "app.services.listado_llamadas_service")
          and "SQL_MODO_TRANSACCION" not in definidas(ARBOLES["ficha"]))
# Los textos SQL de la ficha (las constantes con "FROM "): "detalle" solo
# aparece seguido de "->>" (nunca el detalle entero, C2).
TEXTOS_SQL = [n.value for n in ast.walk(ARBOLES["ficha"])
              if isinstance(n, ast.Constant) and isinstance(n.value, str) and "FROM " in n.value]
SUELTOS = [t for t in TEXTOS_SQL if re.search(r"detalle(?!\s*->>)", t)]
comprobar("ficha: ningún texto SQL lee 'detalle' sin '->>'", TEXTOS_SQL and not SUELTOS,
          f"({len(TEXTOS_SQL)} textos SQL, {len(SUELTOS)} con el detalle entero)")
# Ninguno de los tres importa fastapi ni fastmcp (regla de services/).
for nombre, arbol in ARBOLES.items():
    modulos = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module}
    modulos |= {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    prohibidos = sorted(m for m in modulos if m.split(".")[0] in ("fastapi", "fastmcp"))
    comprobar(f"{nombre}: no importa fastapi ni fastmcp", not prohibidos, f"({prohibidos})")

# El pool del backend: la S5 lo usa, y una versión rota que escriba por
# get_transactional_connection (N9b) también lo usaría en la S4.
db.init_pool()
try:
    # ==================================================================
    print("S4. REPEATABLE READ de verdad (un cambio en medio no se ve)")
    # ==================================================================
    # Datos PROPIOS, confirmados por la conexión de la prueba: un cliente
    # con la marca, su lead (con 'contacto'), su oportunidad en
    # 'presupuesto_enviado', su presupuesto y el log del cálculo.
    EMAIL = f"svc-ficha-{uuid.uuid4().hex[:8]}@example.com"
    # SQL: el cliente propio; RETURNING id devuelve su id.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Ficha Servicio", EMAIL, "611000040"))
    cliente_id = cur.fetchone()[0]
    # SQL: su lead, con la reforma y el contacto en el JSONB.
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_ficha', '[]'::jsonb, %s) RETURNING id;",
                (cliente_id, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                   "incluye_cambios_estructurales": False,
                                   "contacto": {"nombre": "Ficha Servicio", "email": EMAIL,
                                                "telefono": "611000040"}})))
    lead_id = cur.fetchone()[0]
    # SQL: su oportunidad, ya en 'presupuesto_enviado'.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) "
                "VALUES (%s, 'bano', 'presupuesto_enviado') RETURNING id;", (lead_id,))
    OP = cur.fetchone()[0]
    # SQL: su presupuesto sin Gate (importes con IVA 21).
    cur.execute("INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva, "
                "requiere_aprobacion, iva_pct_aplicado) VALUES (%s, 1210.00, 2420.00, false, 21);", (OP,))
    # SQL: el log del cálculo, con el estado en su detalle.
    cur.execute("INSERT INTO logs (entity_type, entity_id, accion, detalle) "
                "VALUES ('oportunidad', %s, 'presupuesto_calculado', %s);", (OP, Json({"estado": "presupuesto_enviado"})))
    # commit: la conexión del código (otra sesión) tiene que verlos.
    cn.commit()
    print(f"  oportunidad propia {OP}")

    # Lo que anota el cursor con gancho: las órdenes enviadas por el
    # servicio y los dos ajustes de la transacción.
    ORDENES = []
    AJUSTES = []

    class CursorConGancho(psycopg2.extensions.cursor):
        """Un cursor normal que anota cada orden y, justo después de la
        PRIMERA consulta tras el SET (la segunda orden), hace el gancho."""

        def execute(self, sql, parametros=None):
            # Se anota y se ejecuta como siempre.
            ORDENES.append(sql)
            resultado = super().execute(sql, parametros)
            # Solo una vez, tras la segunda orden.
            if len(ORDENES) == 2:
                # (a) El modo de ESTA transacción, por otro cursor de la MISMA
                # conexión (así no se pierde la fila que el servicio va a
                # leer con fetchone).
                with self.connection.cursor(cursor_factory=psycopg2.extensions.cursor) as otro:
                    # SQL: el nivel de aislamiento y si es de solo lectura.
                    otro.execute("SELECT current_setting('transaction_isolation'), "
                                 "current_setting('transaction_read_only');")
                    AJUSTES.extend(otro.fetchone())
                # (b) Por OTRA conexión propia: abre el seguimiento de la
                # oportunidad propia y lo CONFIRMA, en mitad de la ficha.
                cn_cambio = psycopg2.connect(DATABASE_URL)
                try:
                    with cn_cambio.cursor() as c:
                        # SQL: cambia el estado de la oportunidad propia.
                        c.execute("UPDATE oportunidades SET estado = 'seguimiento_pendiente' WHERE id = %s;", (OP,))
                        # SQL: y escribe su log, como haría create-followup-task.
                        c.execute("INSERT INTO logs (entity_type, entity_id, accion, detalle) "
                                  "VALUES ('oportunidad', %s, 'seguimiento_abierto', '{}'::jsonb);", (OP,))
                    cn_cambio.commit()
                finally:
                    cn_cambio.close()
            return resultado

    # La conexión que recibe el código: sin commit, con el cursor de gancho
    # como cursor por defecto (conn.cursor() sin argumentos lo usa).
    conn_codigo = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
    conn_codigo.cursor_factory = CursorConGancho

    @contextmanager
    def conexion_de_prueba():
        """Sustituye a get_db_connection: entrega la conexión de la prueba
        y, al salir, la deshace (como la real), sin cerrarla."""
        try:
            yield conn_codigo
        finally:
            conn_codigo.rollback()

    # Se guarda la real y se pone la de prueba EN EL MÓDULO del servicio.
    original = servicio.get_db_connection
    servicio.get_db_connection = conexion_de_prueba
    # La ficha, con la conexión de la prueba; un commit() se cuenta como
    # FALLO y deja ficha en None.
    try:
        ficha = servicio.obtener_ficha(OP)
    except CommitProhibido:
        ficha = None
        comprobar("la ficha no llama a commit()", False, "(CommitProhibido)")
    # Cualquier otra excepción (una versión rota que rechaza la ficha, por
    # ejemplo): un FALLO con su nombre, y el script sigue para dar un
    # recuento exacto.
    except Exception as error:
        ficha = None
        comprobar("la ficha de la S4 termina sin excepción", False, f"({type(error).__name__}: {error})")
    finally:
        # Se restaura la real y se cierra la conexión de la prueba.
        servicio.get_db_connection = original
        conn_codigo.close()

    # La primera orden es el texto exacto del modo de la transacción.
    comprobar("la primera orden es SQL_MODO_TRANSACCION", ORDENES[:1] == [SQL_MODO_TRANSACCION],
              f"({ORDENES[0] if ORDENES else None!r})")
    # El modo de la transacción, leído dentro de ella.
    comprobar("transacción 'repeatable read' y read_only 'on'", AJUSTES == ["repeatable read", "on"], f"({AJUSTES})")
    # El cambio de en medio se confirmó de verdad (si no, la prueba no
    # demostraría nada).
    # SQL: el estado de la oportunidad propia, ahora.
    cur.execute("SELECT estado FROM oportunidades WHERE id = %s;", (OP,))
    estado_ahora = cur.fetchone()[0]
    cn.commit()
    comprobar("el cambio de en medio quedó confirmado", estado_ahora == "seguimiento_pendiente", f"({estado_ahora})")
    # La ficha es la foto de ANTES del cambio, coherente consigo misma.
    if ficha is not None:
        comprobar("la ficha trae el estado de antes", ficha.estado_oportunidad == "presupuesto_enviado",
                  f"({ficha.estado_oportunidad.value})")
        comprobar("la ficha trae el historial de antes y cuadra",
                  pares(ficha.historial) == [("alta", "nueva"), ("presupuesto_calculado", "presupuesto_enviado")]
                  and ficha.historial_cuadra is True,
                  f"({pares(ficha.historial)}, {ficha.historial_cuadra})")
    # La ficha no ha escrito ningún log: solo el del cálculo y el del gancho.
    comprobar("la ficha no escribe ningún log (2 propios: cálculo y gancho)", contar("logs") == 2,
              f"({contar('logs')})")

    # ==================================================================
    print("S5. 404 desde el servicio (pool real, ids inexistentes)")
    # ==================================================================
    # SQL: el id de oportunidad más alto (lectura).
    cur.execute("SELECT max(id) FROM oportunidades;")
    INEXISTENTE = cur.fetchone()[0] + 1_000_000
    cn.commit()
    # Un id que no existe y otro que no cabe en INTEGER: los dos, 404.
    for id_pedido in (INEXISTENTE, 2147483648):
        try:
            servicio.obtener_ficha(id_pedido)
        except servicio.OportunidadNoEncontrada as error:
            comprobar(f"id {id_pedido} -> OportunidadNoEncontrada", error.motivo == "oportunidad_no_encontrada",
                      f"({error.motivo})")
        # Otra excepción distinta de la esperada: FALLO con su nombre.
        except Exception as error:
            comprobar(f"id {id_pedido} -> OportunidadNoEncontrada", False, f"({type(error).__name__}: {error})")
        else:
            comprobar(f"id {id_pedido} -> OportunidadNoEncontrada", False, "(sin error: la excepción se ha tragado)")
finally:
    # Pase lo que pase: se cierra el pool y se borra lo propio.
    db.close_pool()
    limpiar()
    comprobar("no queda ningún dato de prueba", contar("clientes") == 0 and contar("logs") == 0)
    cn.close()

# Resultado y código de salida (0 solo si todo está bien).
total = ok + len(fallos)
print(f"\nRESULTADO: {ok}/{total} correctas")
if fallos:
    print("FALLAN:", fallos)
sys.exit(0 if not fallos else 1)
