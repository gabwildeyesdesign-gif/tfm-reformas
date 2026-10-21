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
  S3  el 503 de las reglas de visita, con su log y su "origen" (P12),
      "resultados_llamada". (Cambio 2026-10-22: el caso del alias, con
      "gate_decisions", se quitó con él, bloque 5.)
  S4  los estados se importan, no se escriben (ast).
  S5  los cinco resultados, con registrar_resultado_con_cursor: su
      respuesta y lo que escriben; un commit() dentro sería FALLO. Desde D33
      (2026-10-14), también fecha_fin_visita (fin = inicio + la duración
      leída aquí de reglas_negocio; None sin visita).
  S7  D33, P1 (plan docs/Plan_Fecha_Fin_Visita_D33.txt): la repetición de un
      resultado CON visita lee las reglas para su fin; rotas, 503 diferido
      con su log y nada más escrito. Sin visita, la repetición no las lee
      (200 aunque estén rotas). (Cambio 2026-10-22: sin el caso del alias.)
  S6  (desde el 2026-10-22) el alias ya no existe: ni su router, ni su
      include_router, ni el parámetro solo_gate (ast).
  S8  (desde el 2026-10-22, U9 y U10 de check_gate_decisions_service,
      retirado) con commit REAL y fuera del ROLLBACK: el log del 503 se
      conserva tras el commit, y un descarte nuevo con las reglas rotas
      se registra sin log de configuración.

Las 4 preguntas: (1) S0-S7 escriben en tablas reales SOLO dentro de una
transacción que se deshace siempre; S8 (desde el 2026-10-22) SÍ confirma:
dos Gates propios (emails check-resultado-svc-u-<8>@example.com), su log
del 503 y un descarte, borrados por la marca al terminar; (2) S0-S7 pasan
su cursor al código, por eso ConexionSinCommit; S8 no pasa ninguna conexión
(los servicios usan el pool); (3) si se corta, Postgres deshace la
transacción abierta y S8 borra sus restos al empezar; al final se
comprueba que no queda ningún cliente de prueba; (4) no toca el 8000 ni
ninguna fila real; los cambios en memoria de nombres de reglas se
restauran en finally.
"""

# ast: comprobaciones de estructura. re: normalizar espacios. sys: consola y
# salida. Path: rutas. datetime/timedelta/ZoneInfo: fechas en Madrid.
import ast
import re
# sys: consola y salida. Path: rutas.
import sys
# uuid: tokens únicos para los leads de S8.
import uuid
# Decimal: m2 exacto para LeadCreate (S8).
from decimal import Decimal
# Fechas en Madrid (timezone: el fin esperado se suma en UTC, D33).
from datetime import date, datetime, timedelta, timezone
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
# El pool de los servicios (S8 los llama con su commit real).
from app.db import connection as db
# Lo que se prueba.
from app.schemas.leads import LeadCreate
from app.schemas.resultados_llamada import ResultadoLlamadaCreate
from app.services.condicion_seguimiento import CRITERIOS_SEGUIMIENTO
# S8: los servicios de alta y cálculo (para sus Gates) y el registro con commit.
from app.services.estimate_service import calculate_estimate
from app.services.leads_service import create_lead
from app.services.resultados_llamada_service import registrar_resultado, registrar_resultado_con_cursor
from app.services.reglas_visita import ConfiguracionIncompleta

# B5-0 (P21): las fechas propias de este script, en scripts/fechas_prueba.py.
import fechas_prueba

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
# B5-0 (2026-10-12, P21): los días laborables del bloque de semanas lejanas
# de este script (fechas_prueba.py). El lunes es el primero (antes, "el lunes
# de dentro de dos semanas"); op_conf sigue en él. op_c, op_conf2 y
# op_conf3 tienen cada una su día: antes las tres confirmaciones tenían su
# visita pedida el lunes a las 10:00, y con D31 se solaparían entre sí.
DIAS = fechas_prueba.Dias("check_resultados_llamada_service", HOY)
LUNES = DIAS.siguiente()
# Los días propios de op_c, op_conf2 y op_conf3.
D_C, D_CONF2, D_CONF3 = DIAS.siguiente(), DIAS.siguiente(), DIAS.siguiente()
# D33: el día de S7 (el del alias, D_P1_ALIAS, se quitó con él el
# 2026-10-22). Y el de U9 (S8), el siguiente del bloque: no mueve ninguno
# de los anteriores.
D_P1_NUEVA, D_U9 = DIAS.siguiente(), DIAS.siguiente()
# B5-0: ninguna visita activa AJENA en el bloque (las de este script nunca
# se confirman: todo acaba en ROLLBACK).
fechas_prueba.exigir_libres("check_resultados_llamada_service", EMAIL,
                            [fechas_prueba.tramo_del_bloque("check_resultados_llamada_service", HOY)])


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
    # D33: la duración REAL de la visita, leída aquí aparte (no con el código
    # probado), para el fin esperado.
    cur.execute("SELECT valor FROM reglas_negocio WHERE clave = 'duracion_visita_min';")
    DURACION = int(cur.fetchone()[0])

    def fin_esperado(inicio_visita):
        """D33: inicio + DURACION minutos REALES (en UTC), de vuelta a Madrid."""
        # La suma en UTC (gotcha del reloj de pared) y el desfase de Madrid.
        return (inicio_visita.astimezone(timezone.utc) + timedelta(minutes=DURACION)).astimezone(MADRID)

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
    # D32: a las 09:45, de la rejilla (antes 10:00): confirmar_hora (S5) exige
    # la hora pedida, y una pedida fuera de la rejilla daría 422 (K3 del plan).
    v_conf = visita(op_conf, "solicitada", instante(LUNES, 9, 45))
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
              d.visita_id == v_conf and d.fecha_pedida == instante(LUNES, 9, 45), f"({d})")
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
    # Un Gate por la ruta (el del alias, con solo_gate, se quitó el 2026-10-22).
    for nombre, origen in (("ruta nueva", "resultados_llamada"),):
        op = oportunidad("pendiente_aprobacion", gate=True)
        data = ResultadoLlamadaCreate(oportunidad_id=op, resultado="visita_acordada", fecha=str(LUNES),
                                      hora="10:00", informe="prueba 503")
        # El servicio DEVUELVE el 503 (diferido): el log ya está escrito.
        respuesta, rechazo = registrar_resultado_con_cursor(cur, data)
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
    # B5-0: cada una en su día propio (antes, las dos el lunes).
    visita(op_conf2, "solicitada", instante(D_CONF2, 10, 0))
    op_conf3 = oportunidad("visita_agendada", gate=False, horas_presupuesto=50)
    visita(op_conf3, "solicitada", instante(D_CONF3, 10, 0))
    # Los cinco casos, uno por resultado.
    CINCO = [
        ("visita_acordada", op_c, {"fecha": str(D_C), "hora": "11:00"}, "seguimiento", "visita_agendada", ["confirmada"]),
        ("descartar", op_b, {"motivo": "sin_decision"}, "seguimiento", "perdida", []),
        # D32: la hora pedida, 09:45.
        ("confirmar_hora", op_conf, {"fecha": str(LUNES), "hora": "09:45"}, "confirmacion_visita", "visita_agendada", ["confirmada"]),
        ("otra_hora", op_conf2, {"fecha": str(D_CONF2), "hora": "17:00"}, "confirmacion_visita", "visita_agendada",
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
        # D33: con visita confirmada, su fin (mismo instante y desfase que el
        # esperado); sin ella, fecha_visita y fecha_fin_visita None.
        # Sin respuesta (un rechazo): FALLO contado, nunca una traza.
        if respuesta is None:
            fin_bien = False
        # Con visita confirmada: el fin esperado y el mismo desfase.
        elif "confirmada" in estados_visita:
            fin_bien = (respuesta.fecha_visita is not None and respuesta.fecha_fin_visita == fin_esperado(respuesta.fecha_visita)
                        and respuesta.fecha_fin_visita.utcoffset() == respuesta.fecha_visita.utcoffset())
        # Sin visita confirmada: los dos None.
        else:
            fin_bien = respuesta.fecha_visita is None and respuesta.fecha_fin_visita is None
        comprobar(f"{resultado}: fecha_fin_visita " + (f"= fecha_visita + {DURACION} min" if "confirmada" in estados_visita
                                                       else "None, como fecha_visita"),
                  fin_bien, f"({respuesta.fecha_visita}, {respuesta.fecha_fin_visita})")

    # ==================================================================
    print("\nS6. El alias ya no existe (ast; bloque 5, 2026-10-22)")
    # ==================================================================
    # Antes S6 comprobaba que el alias llegaba al servicio NUEVO (N6). Desde
    # el 2026-10-22 comprueba que el alias se ha ido de verdad.
    comprobar("app/api/gate_decisions.py no existe", not (RAIZ_REPO / "app/api/gate_decisions.py").exists())
    # Lo que importa main.py: ningún app.api.gate_decisions.
    arbol_main = ast.parse((RAIZ_REPO / "app/main.py").read_text(encoding="utf-8"))
    rutas_api = {a.name for n in ast.walk(arbol_main) if isinstance(n, ast.ImportFrom)
                 and n.module == "app.api" for a in n.names}
    comprobar("app/main.py no importa app.api.gate_decisions", "gate_decisions" not in rutas_api, f"({sorted(rutas_api)})")
    # Los parámetros de las funciones del servicio: ninguno se llama solo_gate.
    arbol_srv = ast.parse(texto_servicio)
    con_solo_gate = [f.name for f in ast.walk(arbol_srv) if isinstance(f, ast.FunctionDef)
                     and any(a.arg == "solo_gate" for a in f.args.args)]
    comprobar("ninguna función del servicio tiene el parámetro solo_gate", con_solo_gate == [], f"({con_solo_gate})")
    # Y no existe ningún servicio viejo al que pudiera llamar.
    comprobar("app/services/gate_decisions_service.py no existe",
              not (RAIZ_REPO / "app/services/gate_decisions_service.py").exists())

    # ==================================================================
    print("\nS7. D33, P1: la repetición con visita lee las reglas para su fin")
    # ==================================================================
    def contar(op):
        """(resultados, visitas, logs de configuración incompleta) de la oportunidad."""
        # SQL: los tres recuentos de esa oportunidad.
        cur.execute("SELECT (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s), "
                    "(SELECT count(*) FROM visitas WHERE oportunidad_id = %(op)s), "
                    "(SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s "
                    "AND accion = 'visita_configuracion_incompleta');", {"op": op})
        # La única fila: la tupla de los tres recuentos.
        return cur.fetchone()

    # Las dos ayudas de S7: romper y restaurar los nombres de las reglas.
    def romper_reglas():
        """Un nombre de regla falso EN MEMORIA (la fila real no se toca), como en S3."""
        # La duración con un nombre que no existe, también dentro de la tupla.
        reglas_visita.CLAVE_DURACION = "regla_de_visita_que_no_existe"
        reglas_visita.CLAVES_REGLAS = tuple(reglas_visita.CLAVE_DURACION if c == CLAVE_DURACION_REAL else c
                                            for c in CLAVES_REGLAS_REAL)

    # Deshace romper_reglas().
    def restaurar_reglas():
        """Los nombres reales otra vez (también en el finally, por si acaso)."""
        # Los dos, a la vez.
        reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS = CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL

    # (nombre, origen del log, día). El caso del alias se quitó el 2026-10-22.
    for nombre, origen, dia in (("ruta nueva", "resultados_llamada", D_P1_NUEVA),):
        # Un Gate con su visita acordada (con las reglas bien).
        op = oportunidad("pendiente_aprobacion", gate=True)
        data = ResultadoLlamadaCreate(oportunidad_id=op, resultado="visita_acordada", fecha=str(dia),
                                      hora="09:45", informe="prueba D33")
        # El registro de partida (con las reglas bien): el 201.
        primera, _ = registrar_resultado_con_cursor(cur, data)
        # Sin el 201 no hay repetición que probar: se cuenta como FALLO.
        comprobar(f"{nombre}: el registro de partida es nuevo (creado)", primera is not None and primera.creado,
                  f"({primera})")
        # Los recuentos justo antes de la repetición.
        antes = contar(op)
        # La MISMA petición con las reglas rotas: una repetición que las necesita.
        romper_reglas()
        respuesta, rechazo = registrar_resultado_con_cursor(cur, data)
        restaurar_reglas()
        # Tiene que salir el 503 diferido, sin respuesta.
        comprobar(f"{nombre}: repetición con las reglas rotas -> ConfiguracionIncompleta diferido, sin respuesta",
                  respuesta is None and isinstance(rechazo, ConfiguracionIncompleta), f"({respuesta}, {rechazo})")
        # SQL: el log de esa oportunidad, con su origen.
        cur.execute("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                    "AND accion = 'visita_configuracion_incompleta';", (op,))
        logs = [f[0] for f in cur.fetchall()]
        # Un log más y ni un resultado ni una visita más.
        comprobar(f"{nombre}: 1 log con origen '{origen}' y nada más escrito",
                  len(logs) == 1 and logs[0].get("origen") == origen and contar(op) == (antes[0], antes[1], antes[2] + 1),
                  f"({logs}, {antes} -> {contar(op)})")
        # Con las reglas bien: repetición con el mismo fin que el registro.
        respuesta, rechazo = registrar_resultado_con_cursor(cur, data)
        comprobar(f"{nombre}: con las reglas bien, repetición (creado False) con el mismo fecha_fin_visita",
                  rechazo is None and respuesta is not None and respuesta.creado is False and primera is not None
                  and respuesta.fecha_fin_visita == primera.fecha_fin_visita == fin_esperado(primera.fecha_visita),
                  f"({respuesta}, {rechazo})")
    # Sin visita (descartar): la repetición no lee las reglas.
    op = oportunidad("pendiente_aprobacion", gate=True)
    data = ResultadoLlamadaCreate(oportunidad_id=op, resultado="descartar", motivo="precio", informe="prueba D33")
    registrar_resultado_con_cursor(cur, data)
    # La repetición con las reglas rotas.
    romper_reglas()
    respuesta, rechazo = registrar_resultado_con_cursor(cur, data)
    restaurar_reglas()
    # Sin visita no se leen: repetición normal, sin fin y sin log.
    comprobar("descartar: repetición con las reglas rotas -> repetición (creado False), fin None, ningún log",
              rechazo is None and respuesta is not None and respuesta.creado is False
              and respuesta.fecha_fin_visita is None and contar(op)[2] == 0, f"({respuesta}, {rechazo})")
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

# ======================================================================
print("\nS8. Con commit REAL: U9 y U10 (bloque 5, 2026-10-22)")
# ======================================================================
# Lo que solo vigilaba check_gate_decisions_service, retirado con el alias:
#   U9  el 503 de las reglas de visita por registrar_resultado, con SU
#       commit: el log SE CONSERVA y la oportunidad no cambia.
#   U10 un descarte NUEVO con las reglas igual de rotas SÍ se registra y
#       no deja ningún log de configuración (descartar no las lee).
# Aquí no vale el ROLLBACK de arriba: lo que se prueba es justo el commit.
# Por eso, datos propios con su marca, creados por los servicios y borrados
# al empezar (restos de una ejecución cortada) y al terminar.
PATRON_U = "check-resultado-svc-u-%@example.com"
# Una conexión normal, solo para mirar y limpiar (no se pasa al código).
cn_u = psycopg2.connect(DATABASE_URL)
cur_u = cn_u.cursor()


def limpiar_u():
    """Borra SOLO lo de la marca PATRON_U, de lo que apunta a lo apuntado."""
    # Las oportunidades propias, como subconsulta.
    ops = ("SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id "
           "JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %(p)s")
    # Un DELETE por tabla, en el orden de las claves foráneas.
    for sql in (f"DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id IN ({ops});",
                f"DELETE FROM decisiones_gate WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM visitas WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM presupuestos WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM oportunidades WHERE id IN ({ops});",
                "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s);",
                "DELETE FROM clientes WHERE email LIKE %(p)s;"):
        # SQL: el DELETE de esa tabla, solo con la marca.
        cur_u.execute(sql, {"p": PATRON_U})
    # Un solo commit para los siete.
    cn_u.commit()


def leer_u(sql, params):
    """Una fila de un SELECT por cn_u, y commit para ver lo último confirmado."""
    # SQL: el que llega (textos fijos de este archivo).
    cur_u.execute(sql, params)
    fila = cur_u.fetchone()
    # commit: cierra la transacción de lectura.
    cn_u.commit()
    return fila


def gate_u():
    """Un Gate propio por los servicios (como POST /leads y /calculate-estimate)."""
    # Token único; sus 8 primeros caracteres van en el email (la marca).
    token = str(uuid.uuid4())
    alta = create_lead(LeadCreate(
        nombre="Prueba svc U", email=f"check-resultado-svc-u-{token[:8]}@example.com", telefono="600000000",
        tipo_reforma="bano", m2=Decimal("6"), nivel_acabados="medio", incluye_cambios_estructurales=True,
        fotos=[], lead_token=token))
    # Con cambios estructurales queda con Gate, en 'pendiente_aprobacion'.
    calculate_estimate(alta.oportunidad_id)
    return alta.oportunidad_id


# La foto de U9/U10: resultados, visitas, logs de configuración, logs de
# decisión, estado y contacto de una oportunidad.
SQL_FOTO_U = ("SELECT (SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s), "
              "(SELECT count(*) FROM visitas WHERE oportunidad_id = %(op)s), "
              "(SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s "
              "AND accion = 'visita_configuracion_incompleta'), "
              "(SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s "
              "AND accion = 'gate_decision_registrada'), "
              "o.estado, o.fecha_ultimo_contacto FROM oportunidades o WHERE o.id = %(op)s;")

# Restos de una ejecución cortada; después, el pool de los servicios.
limpiar_u()
db.init_pool()
# try/finally: pase lo que pase, reglas restauradas, pool cerrado y limpieza.
try:
    # Dos Gates propios, ya confirmados por los servicios.
    op_u9, op_u10 = gate_u(), gate_u()
    # Las reglas rotas EN MEMORIA, como en S3 y S7 (la fila real no se toca).
    romper_reglas()
    # U9: visita_acordada por registrar_resultado (con su commit): 503.
    try:
        registrar_resultado(ResultadoLlamadaCreate(oportunidad_id=op_u9, resultado="visita_acordada",
                                                   fecha=str(D_U9), hora="09:45", informe="prueba U9"))
    # La esperada.
    except ConfiguracionIncompleta as error:
        comprobar("U9: registrar_resultado -> ConfiguracionIncompleta (503) y 'faltan' nombra la regla rota",
                  error.motivo == "configuracion_incompleta"
                  and any("regla_de_visita_que_no_existe" in f for f in error.faltan), f"({error.motivo} {error.faltan})")
    # Otra excepción: FALLO, diciendo cuál.
    except Exception as error:
        comprobar("U9: registrar_resultado -> ConfiguracionIncompleta (503)", False,
                  f"(salió {type(error).__name__}: {error})")
    # Ninguna: FALLO explícito (regla de CLAUDE.md, caso D16).
    else:
        comprobar("U9: registrar_resultado -> ConfiguracionIncompleta (503)", False,
                  "(la excepción se ha tragado: no salió nada)")
    # Lo CONFIRMADO, leído por otra conexión: el log sí, y nada más.
    foto = leer_u(SQL_FOTO_U, {"op": op_u9})
    comprobar("U9: el log del 503 SE CONSERVA tras el commit (exactamente 1)", foto[2] == 1, f"({foto})")
    # SQL: el detalle de ese log (origen y lista de problemas).
    detalle = leer_u("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                     "AND accion = 'visita_configuracion_incompleta' ORDER BY id LIMIT 1;", (op_u9,))
    detalle = detalle[0] if detalle else {}
    comprobar("U9: el log lleva origen 'resultados_llamada' y la regla rota en 'problemas'",
              detalle.get("origen") == "resultados_llamada"
              and any("regla_de_visita_que_no_existe" in p for p in detalle.get("problemas", [])), f"({detalle})")
    comprobar("U9: ni resultado, ni visita, ni log de decisión; sigue en 'pendiente_aprobacion' y sin contacto",
              foto[:2] == (0, 0) and foto[3] == 0 and foto[4] == "pendiente_aprobacion" and foto[5] is None, f"({foto})")
    # U10: un descarte NUEVO con las reglas todavía rotas.
    try:
        r10 = registrar_resultado(ResultadoLlamadaCreate(oportunidad_id=op_u10, resultado="descartar",
                                                         motivo="no_contesta", informe="prueba U10"))
    # Cualquier excepción: no se registró (se enseña cuál).
    except Exception as error:
        r10 = None
        print(f"  U10: salió {type(error).__name__}: {error}")
    # Creado, y la oportunidad en 'perdida'.
    comprobar("U10: descartar nuevo con las reglas rotas -> creado, 'perdida'",
              r10 is not None and r10.creado is True and r10.estado_oportunidad == "perdida", f"({r10})")
    # Lo confirmado: su fila y su log de decisión, ningún log de configuración.
    foto = leer_u(SQL_FOTO_U, {"op": op_u10})
    comprobar("U10: 1 resultado, 0 visitas, 0 logs de configuración y 1 log de decisión",
              foto[:4] == (1, 0, 0, 1), f"({foto})")
finally:
    # Siempre: los nombres reales, el pool cerrado y lo propio borrado.
    restaurar_reglas()
    db.close_pool()
    limpiar_u()
    # SQL: los clientes con la marca que queden (tienen que ser 0).
    restos = leer_u("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON_U,))[0]
    comprobar("S8: no queda ningún dato propio (marca check-resultado-svc-u-)", restos == 0, f"({restos})")
    cn_u.close()

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
