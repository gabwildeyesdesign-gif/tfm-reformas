"""
Verificación SIN servidor del resultado de las llamadas (plan
docs/Plan_Resultado_Llamadas.txt, sección 8.1 con sus notas).

Todo ocurre en UNA transacción que termina en ROLLBACK, con la conexión
ConexionSinCommit (CLAUDE.md, regla del 2026-10-08): el cursor de la prueba
se pasa al código (deducir_tipo_llamada, registrar_resultado_con_cursor), y
si el código, o una versión rota, llamara a commit(), saldría CommitProhibido
y contaría como FALLO, sin confirmar nada.

  S0  autoprueba de ConexionSinCommit (rama else: "se ha tragado").
  S1  deducir_tipo_llamada en todos sus casos, con datos propios.
  S2  los criterios de b) llegan de condicion_seguimiento (ast): ningún
      texto copiado en tipo_llamada.py ni en el servicio.
  S3  el 503 de las reglas de visita, con su log y su "origen" (P12): por la
      ruta nueva, "resultados_llamada"; por el alias, "gate_decisions".
  S4  los estados se importan, no se escriben (ast).
  S5  los cinco resultados, con registrar_resultado_con_cursor: su
      respuesta y lo que escriben; un commit() dentro sería FALLO.

Las 4 preguntas: (1) escribe en tablas reales SOLO dentro de esta
transacción, que se deshace siempre; (2) pasa su cursor al código, por eso
ConexionSinCommit; (3) si se corta, Postgres deshace la transacción abierta,
y al final se comprueba que no queda el cliente de prueba; (4) no toca el
8000 ni ninguna fila real; los cambios en memoria de nombres de reglas se
restauran en finally.
"""

# ast: comprobaciones de estructura. re: normalizar espacios. sys: consola y
# salida. Path: rutas. datetime/timedelta/ZoneInfo: fechas en Madrid.
import ast
import re
# sys: consola y salida. Path: rutas.
import sys
# Fechas en Madrid.
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# La raíz del repositorio (carpeta padre de scripts/) en sys.path.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2 y su clase de conexión; Json para los JSONB.
import psycopg2
import psycopg2.extensions
from psycopg2.extras import Json

# Los módulos enteros, para cambiar nombres de reglas en memoria.
import app.services.reglas_visita as reglas_visita
import app.services.tipo_llamada as tipo_llamada
# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL
# Lo que se prueba.
from app.schemas.resultados_llamada import ResultadoLlamadaCreate
from app.services.condicion_seguimiento import CRITERIOS_SEGUIMIENTO
from app.services.resultados_llamada_service import registrar_resultado_con_cursor
from app.services.reglas_visita import ConfiguracionIncompleta

# La marca propia (dominio reservado).
EMAIL = "check-resultado-svc@example.com"
# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si la condición es verdadera; [FALLO] (y se apunta) si no."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta: se suma y se imprime.
    if condicion:
        ok += 1
        print(f"  [OK] {titulo}")
    # Incorrecta: se apunta y se imprime con el detalle.
    else:
        # Incorrecta: se apunta y se imprime con el detalle.
        fallos.append(titulo)
        print(f"  [FALLO] {titulo} {detalle}")


# La excepción propia de la defensa.
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en la conexión de prueba."""


# La conexión con commit() prohibido (regla de CLAUDE.md del 2026-10-08).
class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión normal salvo commit(), que lanza CommitProhibido."""

    def commit(self):
        # No se confirma nada: error, y el ROLLBACK final lo deshace todo.
        raise CommitProhibido("commit() en una conexión de prueba: la prueba termina siempre en ROLLBACK")


# Fechas: el lunes de dentro de dos semanas (siempre futuro y laborable).
MADRID = ZoneInfo("Europe/Madrid")
HOY = datetime.now(MADRID).date()
LUNES = HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7) + timedelta(days=7)


def instante(dia: date, hh: int, mm: int) -> datetime:
    """Ese día a esa hora, en Madrid."""
    # combine con la zona: el desfase correcto de ese día.
    return datetime.combine(dia, datetime.min.time().replace(hour=hh, minute=mm), tzinfo=MADRID)


# ======================================================================
print("S0. Autoprueba de ConexionSinCommit")
# ======================================================================
# La conexión de toda la prueba, con commit() prohibido.
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
cur = cn.cursor()
# try/except/else: sin la excepción, FALLO explícito.
try:
    cn.commit()
# La esperada.
except CommitProhibido:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# commit() no lanzó nada: la defensa no existe.
else:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False, "(sin error: la excepción se ha tragado)")


def oportunidad(estado, gate=None, horas_presupuesto=None, contacto=False):
    """
    Una oportunidad propia en este estado. gate None: sin presupuesto; True
    o False: con presupuesto (con o sin Gate) creado hace horas_presupuesto
    horas (0 si no se dice). contacto: fecha_ultimo_contacto escrita.
    """
    # SQL: la oportunidad, del lead de prueba, con ese estado.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado, fecha_ultimo_contacto) "
                "VALUES (%s, 'bano', %s, CASE WHEN %s THEN now() END) RETURNING id;", (LEAD, estado, contacto))
    op = cur.fetchone()[0]
    # Con presupuesto: sin importes que importen aquí, con o sin Gate.
    if gate is not None:
        # SQL: el presupuesto, creado hace esas horas.
        cur.execute("INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva, "
                    "requiere_aprobacion, iva_pct_aplicado, created_at) "
                    "VALUES (%s, 1210, 2420, %s, 21, now() - make_interval(hours => %s));",
                    (op, gate, horas_presupuesto or 0))
    return op


def visita(op, estado, cuando, texto="visita de prueba svc"):
    """Una visita propia en esa oportunidad. Devuelve su id."""
    # SQL: la visita, con su estado y su fecha.
    cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                "VALUES (%s, %s, %s, %s) RETURNING id;", (op, cuando, estado, texto))
    return cur.fetchone()[0]


# Lo que el código cambia en memoria; se guarda para restaurarlo en finally.
CLAVE_SEG_REAL = tipo_llamada.CLAVE_SEGUIMIENTO
CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL = reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS

# try/finally: pase lo que pase, nombres restaurados y ROLLBACK.
try:
    # Datos de partida: un cliente y un lead propios.
    # SQL: el cliente, con la marca.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba svc', %s, '600000000') RETURNING id;", (EMAIL,))
    CLIENTE = cur.fetchone()[0]
    # SQL: su lead (m2 dentro del CHECK de leads).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_svc', %s) RETURNING id;",
                (CLIENTE, Json({"m2": 10})))
    LEAD = cur.fetchone()[0]
    # SQL: el now() de la transacción (el de la deducción).
    cur.execute("SELECT now();")
    AHORA = cur.fetchone()[0]

    # ==================================================================
    print("\nS1. deducir_tipo_llamada")
    # ==================================================================
    # Una oportunidad por caso.
    op_gate = oportunidad("pendiente_aprobacion", gate=True)
    op_c = oportunidad("seguimiento_pendiente", gate=False, horas_presupuesto=60)
    op_b = oportunidad("presupuesto_enviado", gate=False, horas_presupuesto=50)
    op_reciente = oportunidad("presupuesto_enviado", gate=False, horas_presupuesto=1)
    # Los que no cumplen b) por otro criterio, uno por criterio.
    op_contacto = oportunidad("presupuesto_enviado", gate=False, horas_presupuesto=50, contacto=True)
    op_con_gate = oportunidad("presupuesto_enviado", gate=True, horas_presupuesto=50)
    # Sin presupuesto (solo posible a mano) y con una visita 'completada'.
    op_sin_pres = oportunidad("presupuesto_enviado")
    op_visita = oportunidad("presupuesto_enviado", gate=False, horas_presupuesto=50)
    visita(op_visita, "completada", instante(LUNES - timedelta(days=21), 10, 0))
    # Una confirmación y un estado sin llamada.
    op_conf = oportunidad("visita_agendada", gate=False, horas_presupuesto=50)
    v_conf = visita(op_conf, "solicitada", instante(LUNES, 10, 0))
    op_perdida = oportunidad("perdida", gate=False, horas_presupuesto=50)
    # (oportunidad, estado, tipo esperado, motivo esperado)
    CASOS = [
        (op_gate, "pendiente_aprobacion", "gate", None),
        (op_c, "seguimiento_pendiente", "seguimiento", None),
        (op_b, "presupuesto_enviado", "seguimiento", None),
        (op_reciente, "presupuesto_enviado", None, "plazo_no_cumplido"),
        (op_contacto, "presupuesto_enviado", None, "contacto_registrado"),
        (op_con_gate, "presupuesto_enviado", None, "con_gate"),
        (op_sin_pres, "presupuesto_enviado", None, "sin_presupuesto"),
        (op_visita, "presupuesto_enviado", None, "visita_existente"),
        (op_conf, "visita_agendada", "confirmacion_visita", None),
        (op_perdida, "perdida", None, "estado_no_permitido"),
    ]
    # Cada caso, con el cursor de la prueba.
    for op, estado, tipo, motivo in CASOS:
        d = tipo_llamada.deducir_tipo_llamada(cur, op, estado, AHORA)
        comprobar(f"{estado:<22} -> tipo {tipo}, motivo {motivo}",
                  (d.tipo.value if d.tipo else None, d.motivo_no) == (tipo, motivo), f"({d})")
    # La confirmación trae la visita pedida y su fecha.
    d = tipo_llamada.deducir_tipo_llamada(cur, op_conf, "visita_agendada", AHORA)
    comprobar("confirmación: visita_id y fecha_pedida de la 'solicitada'",
              d.visita_id == v_conf and d.fecha_pedida == instante(LUNES, 10, 0), f"({d})")
    # La regla de 48 h rota (nombre falso EN MEMORIA): configuracion_incompleta.
    tipo_llamada.CLAVE_SEGUIMIENTO = "regla_que_no_existe"
    d = tipo_llamada.deducir_tipo_llamada(cur, op_b, "presupuesto_enviado", AHORA)
    tipo_llamada.CLAVE_SEGUIMIENTO = CLAVE_SEG_REAL
    comprobar("regla de 48 h que falta -> configuracion_incompleta con su problema",
              d.tipo is None and d.motivo_no == "configuracion_incompleta" and d.faltan
              and "regla_que_no_existe" in d.faltan[0], f"({d})")

    # ==================================================================
    print("\nS2. Los criterios de b) llegan de condicion_seguimiento (ast)")
    # ==================================================================
    # Los árboles de los dos archivos.
    arbol_tipo = ast.parse((RAIZ_REPO / "app/services/tipo_llamada.py").read_text(encoding="utf-8"))
    texto_servicio = (RAIZ_REPO / "app/services/resultados_llamada_service.py").read_text(encoding="utf-8")
    # Lo que tipo_llamada.py importa de condicion_seguimiento.
    importados = {a.name for n in arbol_tipo.body if isinstance(n, ast.ImportFrom)
                  and n.module == "app.services.condicion_seguimiento" for a in n.names}
    comprobar("tipo_llamada importa columnas_criterios, parametros_condicion y primer_criterio_incumplido",
              {"columnas_criterios", "parametros_condicion", "primer_criterio_incumplido"} <= importados, f"({importados})")

    def sin_espacios(t):
        """El texto con cualquier tramo de espacios reducido a uno."""
        return re.sub(r"\s+", " ", t)

    # Ninguna expresión de los criterios escrita en los dos archivos.
    textos = {"tipo_llamada.py": sin_espacios((RAIZ_REPO / "app/services/tipo_llamada.py").read_text(encoding="utf-8")),
              "resultados_llamada_service.py": sin_espacios(texto_servicio)}
    copias = [(archivo, m) for m, expresion in CRITERIOS_SEGUIMIENTO for archivo, t in textos.items()
              if sin_espacios(expresion) in t]
    comprobar("ninguna expresión de los seis criterios copiada", copias == [], f"({copias})")

    # ==================================================================
    print("\nS3. 503 de las reglas de visita: log conservado y su origen (P12)")
    # ==================================================================
    # Un nombre de regla falso EN MEMORIA (la fila real no se toca).
    reglas_visita.CLAVE_DURACION = "regla_de_visita_que_no_existe"
    reglas_visita.CLAVES_REGLAS = tuple(reglas_visita.CLAVE_DURACION if c == CLAVE_DURACION_REAL else c
                                        for c in CLAVES_REGLAS_REAL)
    # Un Gate por ruta: la nueva y el alias (solo_gate).
    for nombre, solo_gate, origen in (("ruta nueva", False, "resultados_llamada"), ("alias", True, "gate_decisions")):
        op = oportunidad("pendiente_aprobacion", gate=True)
        data = ResultadoLlamadaCreate(oportunidad_id=op, resultado="visita_acordada", fecha=str(LUNES),
                                      hora="10:00", informe="prueba 503")
        # El servicio DEVUELVE el 503 (diferido): el log ya está escrito.
        respuesta, rechazo = registrar_resultado_con_cursor(cur, data, solo_gate)
        comprobar(f"{nombre}: devuelve un ConfiguracionIncompleta diferido y ninguna respuesta",
                  respuesta is None and isinstance(rechazo, ConfiguracionIncompleta), f"({respuesta}, {rechazo})")
        # SQL: los logs de configuración incompleta de esa oportunidad.
        cur.execute("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                    "AND accion = 'visita_configuracion_incompleta';", (op,))
        logs = [f[0] for f in cur.fetchall()]
        comprobar(f"{nombre}: 1 log con origen '{origen}'", len(logs) == 1 and logs[0].get("origen") == origen, f"({logs})")
        # SQL: nada más escrito (ni resultado ni visita) en esa oportunidad.
        cur.execute("SELECT (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %s), "
                    "(SELECT count(*) FROM visitas WHERE oportunidad_id = %s);", (op, op))
        comprobar(f"{nombre}: ningún resultado ni visita escritos", cur.fetchone() == (0, 0))
    # Se restauran los nombres (también en el finally, por si acaso).
    reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS = CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL

    # ==================================================================
    print("\nS4. Los estados se importan, no se escriben (ast)")
    # ==================================================================
    # Los textos de estado que ya define otro módulo.
    ESTADOS = {"solicitada", "confirmada", "cancelada", "seguimiento_pendiente", "visita_agendada", "presupuesto_enviado"}
    for archivo in ("app/services/tipo_llamada.py", "app/services/resultados_llamada_service.py"):
        arbol = ast.parse((RAIZ_REPO / archivo).read_text(encoding="utf-8"))
        # Las asignaciones de primer nivel cuyo valor es uno de esos textos.
        copiadas = [n.targets[0].id for n in arbol.body if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Constant) and n.value.value in ESTADOS]
        comprobar(f"{archivo}: ningún estado de visita/seguimiento escrito a mano", copiadas == [], f"({copiadas})")

    # ==================================================================
    print("\nS5. Los cinco resultados con registrar_resultado_con_cursor")
    # ==================================================================
    # (resultado, oportunidad, campos, tipo, estado final, visitas esperadas)
    # Dos confirmaciones más, cada una con su visita pedida.
    op_conf2 = oportunidad("visita_agendada", gate=False, horas_presupuesto=50)
    visita(op_conf2, "solicitada", instante(LUNES, 10, 0))
    op_conf3 = oportunidad("visita_agendada", gate=False, horas_presupuesto=50)
    visita(op_conf3, "solicitada", instante(LUNES, 10, 0))
    # Los cinco casos, uno por resultado.
    CINCO = [
        ("visita_acordada", op_c, {"fecha": str(LUNES), "hora": "11:00"}, "seguimiento", "visita_agendada", ["confirmada"]),
        ("descartar", op_b, {"motivo": "sin_decision"}, "seguimiento", "perdida", []),
        ("confirmar_hora", op_conf, {"fecha": str(LUNES), "hora": "10:00"}, "confirmacion_visita", "visita_agendada", ["confirmada"]),
        ("otra_hora", op_conf2, {"fecha": str(LUNES), "hora": "17:00"}, "confirmacion_visita", "visita_agendada",
         ["cancelada", "confirmada"]),
        ("cliente_cancela", op_conf3, {"motivo": "plazo"}, "confirmacion_visita", "perdida", ["cancelada"]),
    ]
    # Cada uno, con el cursor de la prueba.
    for resultado, op, campos, tipo, estado_final, estados_visita in CINCO:
        data = ResultadoLlamadaCreate(oportunidad_id=op, resultado=resultado, informe="prueba svc", **campos)
        # try: un commit() dentro sería CommitProhibido -> FALLO contado.
        try:
            respuesta, rechazo = registrar_resultado_con_cursor(cur, data)
        except CommitProhibido:
            comprobar(f"{resultado}: sin commit() dentro", False, "(CommitProhibido)")
            continue
        # La respuesta: creado, su tipo y el estado final.
        comprobar(f"{resultado}: creado, tipo {tipo}, oportunidad '{estado_final}'",
                  rechazo is None and respuesta.creado and respuesta.tipo_llamada.value == tipo
                  and respuesta.estado_oportunidad == estado_final, f"({respuesta}, {rechazo})")
        # SQL: la fila, las visitas y el estado, tal como quedaron.
        cur.execute("SELECT tipo_llamada, decision, visita_id IS NOT NULL, visita_cancelada_id IS NOT NULL "
                    "FROM decisiones_gate WHERE oportunidad_id = %s;", (op,))
        fila = cur.fetchone()
        cur.execute("SELECT estado FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (op,))
        visitas = [f[0] for f in cur.fetchall()]
        # Con visita confirmada: visita_id; con una cancelada: visita_cancelada_id.
        comprobar(f"{resultado}: fila ({tipo}, {resultado}) y visitas {estados_visita}",
                  fila == (tipo, resultado, "confirmada" in estados_visita, "cancelada" in estados_visita)
                  and visitas == estados_visita, f"({fila}, {visitas})")
finally:
    # Siempre: nombres reales en memoria y ROLLBACK de todo.
    tipo_llamada.CLAVE_SEGUIMIENTO = CLAVE_SEG_REAL
    reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS = CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL
    cn.rollback()

# Fuera de la transacción: no queda el cliente de prueba.
# SQL: clientes con la marca.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# Y los nombres de reglas, otra vez los reales.
comprobar("nombres de reglas restaurados en memoria",
          tipo_llamada.CLAVE_SEGUIMIENTO == CLAVE_SEG_REAL and reglas_visita.CLAVE_DURACION == CLAVE_DURACION_REAL)
# Se cierra la transacción de lectura y la conexión.
cn.rollback()
cn.close()

# Resumen y código de salida (1 si hubo fallos; lo mira la suite).
total = ok + len(fallos)
# El resumen, entre dos líneas de "=".
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con fallos, se listan y el código de salida es 1.
if fallos:
    print("FALLOS:")
    # Uno por línea, y el código de salida 1 (lo mira la suite).
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
