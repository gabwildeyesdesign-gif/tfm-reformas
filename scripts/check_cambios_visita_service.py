"""
Verificación SIN servidor del cambio y la cancelación de una visita
confirmada (D46; plan docs/Plan_Cambio_Visita_D46.txt, sección 8, con el
veredicto del tutor al PASO 0).

Todo ocurre en UNA transacción que termina en ROLLBACK, con la conexión
ConexionSinCommit (CLAUDE.md, regla del 2026-10-08): el cursor de la prueba
se pasa al código (registrar_cambio_con_cursor, leer_ficha y
registrar_resultado_con_cursor), y si el código, o una versión rota,
llamara a commit(), saldría CommitProhibido y contaría como FALLO, sin
confirmar nada (N17). Cada llamada al servicio va en su SAVEPOINT: un error
de Postgres (por ejemplo el índice parcial, N4) cuenta como FALLO sin
abortar el resto.

  S0  autoprueba de ConexionSinCommit (rama else: "se ha tragado").
  S1  motivo_no_modificable, pura, con fechas fijas (precisión 2: el límite
      exacto inicio = ahora ya es "pasada", R5).
  S2  estático (ast): la ficha y el servicio llaman a la MISMA función
      (precisión 3); la hora nueva usa la antelación de ADMINISTRACIÓN; los
      estados se importan; ningún "informe" en los logs.
  S3  N18: para los MISMOS datos, la ficha (visita_modificable) y la ruta
      dicen lo mismo, en cinco casos.
  S4  V1, cambiar: respuesta, visitas, fila, oportunidad y log.
  S5  V4 y V2: la repetición (200 sin escribir) y un segundo cambio.
  S6  V3 y V4: cancelar, su repetición, y lo que ya no se puede.
  S7  V5 y 404: visita_cambiada.
  S8  V6: los 422 de la hora nueva, con la antelación de hoy en dos ramas
      deterministas (R5 de D45) y el 1.er laborable de administración.
  S9  V7: la propia visita no cuenta en el hueco; otra sí (409 con
      huecos_libres).
  S10 V11: el 503 al cambiar, con su log; cancelar sin reglas, 201.
  S11 V13 en el servicio (P5): la repetición de la llamada tras el cambio.
  S12 V10 en el servicio: la ficha tras cambiar y tras cancelar (tipo_cambio
      igual a la accion guardada; historial_cuadra).

Las 4 preguntas: (1) escribe en tablas reales SOLO dentro de una transacción
que se deshace siempre (email check-cambio-svc-<8>@example.com, marca de la
ejecución); (2) pasa su cursor al código, por eso ConexionSinCommit; (3) si
se corta, Postgres deshace la transacción abierta; al final se comprueba que
no queda ningún cliente de prueba; (4) no toca el 8000 ni ninguna fila real;
el nombre de regla cambiado en memoria (S10) se restaura en finally. Los
bloqueos de día (pg_advisory_xact_lock) de sus cambios duran lo que la
transacción (segundos): sus días son los de su bloque de fechas_prueba.py,
salvo el 1.er laborable de S8, que solo se escribe dentro de un SAVEPOINT
que se deshace.

Recuentos: previstos en el plan ANTES de la fase 2 (precisión 5) y reales
después; en el commit, calculados en una orden anterior (D37.5).
"""

# ast: comprobaciones de estructura. sys: consola y salida. uuid: la marca.
import ast
import sys
import uuid
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

# El módulo entero, para cambiar un nombre de regla en memoria (S10).
import app.services.reglas_visita as reglas_visita
# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL
# Lo que se prueba: los dos esquemas de entrada (la ruta nueva y, para S11,
# la de las llamadas).
from app.schemas.cambios_visita import CambioVisitaCreate
from app.schemas.resultados_llamada import ResultadoLlamadaCreate
# El servicio nuevo (la función pura y la operación) y la ficha.
from app.services.cambios_visita_service import motivo_no_modificable, registrar_cambio_con_cursor
from app.services.ficha_oportunidad_service import leer_ficha
# Los rechazos compartidos y el registro de llamadas (S11, P5).
from app.services.reglas_visita import ConfiguracionIncompleta, RechazoNegocio
from app.services.resultados_llamada_service import registrar_resultado_con_cursor

# Las fechas propias de este script (bloque de semanas lejanas).
import fechas_prueba

# La marca propia (dominio reservado) y la de esta ejecución.
EMAIL = f"check-cambio-svc-{uuid.uuid4().hex[:8]}@example.com"
PATRON = "check-cambio-svc-%@example.com"
# Los textos copiados del PLAN y del veredicto del tutor (no del código).
TEXTO_CAMBIADA = "Visita cambiada por teléfono por administración."
MENSAJE_P5 = "Esta visita se ha cambiado o cancelado después de registrar la llamada: vuelve a abrir la ficha."
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


# Fechas: los días laborables del bloque propio, uno por oportunidad.
MADRID = ZoneInfo("Europe/Madrid")
HOY = datetime.now(MADRID).date()
DIAS = fechas_prueba.Dias("check_cambios_visita_service", HOY)
D_S3A, D_S3D, D_S4, D_S6, D_S7, D_S9, D_S10, D_S11 = (DIAS.siguiente() for _ in range(8))
# El sábado de la semana de D_S7 (para fin_de_semana) y ayer.
SABADO, AYER = D_S7 + timedelta(days=5 - D_S7.weekday()), HOY - timedelta(days=1)
# Antes de crear nada: ninguna visita activa AJENA en el bloque.
fechas_prueba.exigir_libres("check_cambios_visita_service", PATRON,
                            [fechas_prueba.tramo_del_bloque("check_cambios_visita_service", HOY)])


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


def oportunidad(estado="visita_agendada", gate=False):
    """
    Una oportunidad propia en ese estado, con su presupuesto (con o sin Gate)
    y los logs que hacen cuadrar su historial: el cálculo y, si está en
    'visita_agendada', una llamada registrada que la dejó así.
    """
    # SQL: la oportunidad, del lead de prueba, con ese estado.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', %s) RETURNING id;",
                (LEAD, estado))
    op = cur.fetchone()[0]
    # SQL: su presupuesto (importes sin importancia aquí).
    cur.execute("INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva, "
                "requiere_aprobacion, iva_pct_aplicado) VALUES (%s, 1210, 2420, %s, 21);", (op, gate))
    # SQL: el log del cálculo (el estado tras calcular).
    cur.execute("INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES ('oportunidad', %s, "
                "'presupuesto_calculado', %s);", (op, Json({"estado": "pendiente_aprobacion" if gate
                                                             else "presupuesto_enviado"})))
    # En 'visita_agendada': el log de la llamada que la dejó así.
    if estado == "visita_agendada":
        # SQL: el log, con el estado en su detalle (lo lee el historial).
        cur.execute("INSERT INTO logs (entity_type, entity_id, accion, detalle) VALUES ('oportunidad', %s, "
                    "'llamada_registrada', %s);", (op, Json({"estado": "visita_agendada"})))
    return op


def visita(op, estado, cuando, texto="visita de prueba svc D46"):
    """Una visita propia en esa oportunidad. Devuelve su id."""
    # SQL: la visita, con su estado y su fecha.
    cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                "VALUES (%s, %s, %s, %s) RETURNING id;", (op, cuando, estado, texto))
    return cur.fetchone()[0]


def datos(op, accion, actual, nueva=None, motivo=None, informe="Informe de prueba svc D46."):
    """El cuerpo de POST /cambios-visita, validado con el esquema real."""
    # La visita que se "ve": su fecha y su hora en Madrid, como texto.
    cuerpo = {"oportunidad_id": op, "accion": accion, "fecha_actual": f"{actual.astimezone(MADRID):%Y-%m-%d}",
              "hora_actual": f"{actual.astimezone(MADRID):%H:%M}", "informe": informe}
    # Al cambiar, la nueva.
    if nueva is not None:
        cuerpo |= {"fecha": f"{nueva:%Y-%m-%d}", "hora": f"{nueva:%H:%M}"}
    # Al cancelar, el motivo.
    if motivo is not None:
        cuerpo["motivo"] = motivo
    # El esquema de la ruta (un 422 aquí sería un fallo del propio script).
    return CambioVisitaCreate(**cuerpo)


def ejecutar(data, deshacer=False):
    """
    registrar_cambio_con_cursor en un SAVEPOINT. Devuelve (respuesta,
    rechazo_diferido, error): error es el rechazo de negocio o cualquier
    otra excepción (un error de Postgres, una segunda barrera, un commit()).
    deshacer=True: lo escrito se deshace (solo se quería la respuesta).
    """
    # SQL: un punto al que volver.
    cur.execute("SAVEPOINT cambio;")
    # try: todo lo que puede salir del servicio.
    try:
        respuesta, diferido = registrar_cambio_con_cursor(cur, data)
    # Un rechazo de negocio (409, 422): no ha escrito nada en Postgres.
    except RechazoNegocio as error:
        # SQL: se suelta el punto de guardado.
        cur.execute("RELEASE SAVEPOINT cambio;")
        return None, None, error
    # Cualquier otra (CommitProhibido, psycopg2.Error, RuntimeError): FALLO
    # contado por quien mira el resultado; la transacción sigue viva.
    except Exception as error:
        # SQL: vuelta al punto de guardado.
        cur.execute("ROLLBACK TO SAVEPOINT cambio;")
        return None, None, error
    # SQL: deshacer lo escrito, o quedárselo.
    cur.execute("ROLLBACK TO SAVEPOINT cambio;" if deshacer else "RELEASE SAVEPOINT cambio;")
    return respuesta, diferido, None


def motivo_de(error):
    """El motivo de un rechazo, o el nombre de la excepción si no lo tiene."""
    # Los rechazos de negocio llevan "motivo"; los demás, su clase.
    return getattr(error, "motivo", type(error).__name__) if error is not None else None


def foto(op):
    """Todo lo que un cambio puede escribir en una oportunidad (dos fotos iguales: nada escrito)."""
    # SQL: cambios, visitas (estado y fecha), logs, estado y fechas.
    cur.execute(
        """
        SELECT (SELECT count(*) FROM cambios_visita WHERE oportunidad_id = %(op)s),
               (SELECT string_agg(estado || '@' || fecha_propuesta::text, ',' ORDER BY id) FROM visitas
                WHERE oportunidad_id = %(op)s),
               (SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s),
               o.estado, o.fecha_ultimo_contacto, o.updated_at
        FROM oportunidades o WHERE o.id = %(op)s;
        """, {"op": op})
    return cur.fetchone()


# Lo que S10 cambia en memoria; se guarda para restaurarlo en finally.
CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL = reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS

# try/finally: pase lo que pase, nombres restaurados y ROLLBACK.
try:
    # Datos de partida: un cliente y un lead propios (con todo lo que lee la ficha).
    # SQL: el cliente, con la marca.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba cambio svc', %s, '600000000') "
                "RETURNING id;", (EMAIL,))
    CLIENTE = cur.fetchone()[0]
    # SQL: su lead, con la reforma y el contacto en el JSONB.
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_cambio_svc', '[]'::jsonb, %s) RETURNING id;",
                (CLIENTE, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                "incluye_cambios_estructurales": False,
                                "contacto": {"nombre": "Prueba cambio svc", "email": EMAIL, "telefono": "600000000"}})))
    LEAD = cur.fetchone()[0]
    # SQL: el now() de la transacción (el que usa el servicio).
    cur.execute("SELECT now();")
    AHORA = cur.fetchone()[0]
    # La duración REAL de la visita y la antelación de administración, leídas
    # aquí aparte (no con el código probado), para lo esperado.
    cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave IN ('duracion_visita_min', "
                "'dias_antelacion_visita_admin');")
    REGLAS_LEIDAS = {clave: int(valor) for clave, valor in cur.fetchall()}
    DURACION = REGLAS_LEIDAS["duracion_visita_min"]

    def fin_esperado(inicio_visita):
        """D33: inicio + DURACION minutos REALES (en UTC), de vuelta a Madrid."""
        # La suma en UTC (gotcha del reloj de pared) y el desfase de Madrid.
        return (inicio_visita.astimezone(timezone.utc) + timedelta(minutes=DURACION)).astimezone(MADRID)

    # ==================================================================
    print("\nS1. motivo_no_modificable, con fechas fijas")
    # ==================================================================
    # Una visita fija y los instantes alrededor (nada depende de hoy).
    A = datetime(2030, 1, 10, 10, 0, tzinfo=MADRID)
    # (título, estado de la oportunidad, estado de la visita, fecha, ahora, esperado)
    for titulo, estado_op, estado_v, fecha_v, ahora_v, esperado in (
            ("confirmada y futura", "visita_agendada", "confirmada", A, A - timedelta(hours=1), None),
            ("inicio = ahora (R5: ya es pasada)", "visita_agendada", "confirmada", A, A, "visita_pasada"),
            ("un minuto después de empezar", "visita_agendada", "confirmada", A, A + timedelta(minutes=1),
             "visita_pasada"),
            ("solo 'solicitada'", "visita_agendada", "solicitada", A, A - timedelta(hours=1), "sin_visita_confirmada"),
            ("sin visita activa", "visita_agendada", None, None, A, "sin_visita_confirmada"),
            ("oportunidad 'perdida' con confirmada futura", "perdida", "confirmada", A, A - timedelta(hours=1),
             "sin_visita_confirmada"),
            ("visita 'cancelada'", "visita_agendada", "cancelada", A, A - timedelta(hours=1), "sin_visita_confirmada")):
        # La función pura, sin base de datos.
        r = motivo_no_modificable(estado_op, estado_v, fecha_v, ahora_v)
        comprobar(f"{titulo} -> {esperado}", r == esperado, f"({r})")

    # ==================================================================
    print("\nS2. Estático (ast)")
    # ==================================================================
    # Los árboles de la ficha y del servicio.
    arbol_ficha = ast.parse((RAIZ_REPO / "app/services/ficha_oportunidad_service.py").read_text(encoding="utf-8"))
    texto_srv = (RAIZ_REPO / "app/services/cambios_visita_service.py").read_text(encoding="utf-8")
    arbol_srv = ast.parse(texto_srv)

    def llamadas_a(arbol, nombre):
        """Las llamadas a la función "nombre" (por su nombre suelto) en un árbol."""
        # Cada nodo Call cuya función es un Name con ese id.
        return [n for n in ast.walk(arbol) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == nombre]

    # La ficha la importa de cambios_visita_service y la llama.
    importada = any(isinstance(n, ast.ImportFrom) and n.module == "app.services.cambios_visita_service"
                    and any(a.name == "motivo_no_modificable" for a in n.names) for n in ast.walk(arbol_ficha))
    comprobar("la ficha importa motivo_no_modificable de cambios_visita_service y la llama (precisión 3)",
              importada and len(llamadas_a(arbol_ficha, "motivo_no_modificable")) == 1)
    # El servicio la llama dentro de registrar_cambio_con_cursor.
    funcion = next((f for f in ast.walk(arbol_srv) if isinstance(f, ast.FunctionDef)
                    and f.name == "registrar_cambio_con_cursor"), None)
    comprobar("registrar_cambio_con_cursor llama a motivo_no_modificable",
              funcion is not None and len(llamadas_a(funcion, "motivo_no_modificable")) == 1)
    # La hora nueva: primer_dia_del_canal con CANAL_ADMINISTRACION (N7, N8).
    canales = [a.id for c in llamadas_a(arbol_srv, "primer_dia_del_canal") for a in c.args if isinstance(a, ast.Name)
               and a.id.startswith("CANAL_")]
    comprobar("la hora nueva usa primer_dia_del_canal(..., CANAL_ADMINISTRACION) y ningún otro canal",
              canales == ["CANAL_ADMINISTRACION"], f"({canales})")
    # Los estados se importan: ninguna asignación de primer nivel con uno.
    ESTADOS = {"solicitada", "confirmada", "cancelada", "visita_agendada", "perdida"}
    copiados = [n.targets[0].id for n in arbol_srv.body if isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Constant) and n.value.value in ESTADOS]
    comprobar("ningún estado de visita u oportunidad escrito a mano en el servicio", copiados == [], f"({copiados})")
    # Ningún diccionario del servicio tiene la clave "informe" (los logs, N12).
    claves_informe = [n.lineno for n in ast.walk(arbol_srv) if isinstance(n, ast.Dict)
                      and any(isinstance(k, ast.Constant) and k.value == "informe" for k in n.keys)]
    comprobar("ningún diccionario del servicio (los detalles de log) lleva la clave 'informe'", claves_informe == [],
              f"(líneas {claves_informe})")

    # ==================================================================
    print("\nS3. N18: la ficha y la ruta dicen lo mismo con los mismos datos")
    # ==================================================================
    # La pasada de hace dos horas, a minuto entero (para que llegue a visita_pasada).
    PASADA = (AHORA - timedelta(hours=2)).astimezone(MADRID).replace(second=0, microsecond=0)
    # (título, oportunidad, la visita que "ve" el operador, si la ruta debe aceptarla)
    # a) confirmada futura; b) confirmada pasada.
    op_a = oportunidad()
    visita(op_a, "confirmada", instante(D_S3A, 8, 30))
    op_b = oportunidad()
    visita(op_b, "confirmada", PASADA)
    # c) solo 'solicitada'; d) 'perdida' con una confirmada futura (a mano).
    op_c = oportunidad()
    visita(op_c, "solicitada", instante(D_S3A, 17, 0))
    op_d = oportunidad("perdida")
    visita(op_d, "confirmada", instante(D_S3D, 12, 15))
    # e) sin ninguna visita.
    op_e = oportunidad()
    CASOS_N18 = [("confirmada futura", op_a, instante(D_S3A, 8, 30), True),
                 ("confirmada pasada", op_b, PASADA, False),
                 ("solo 'solicitada'", op_c, instante(D_S3A, 17, 0), False),
                 ("oportunidad 'perdida' con confirmada futura", op_d, instante(D_S3D, 12, 15), False),
                 ("sin visita", op_e, instante(D_S3A, 8, 30), False)]
    for titulo, op, actual, acepta in CASOS_N18:
        # La ficha, con el mismo cursor (la misma foto).
        f = leer_ficha(cur, op)
        # La ruta: un cambio a las 11:00 de D_S3A, que se deshace.
        respuesta, _, error = ejecutar(datos(op, "cambiar", actual, instante(D_S3A, 11, 0)), deshacer=True)
        # Las dos tienen que coincidir: ficha no null <-> la ruta acepta.
        comprobar(f"{titulo}: ficha {'con' if acepta else 'sin'} visita_modificable y la ruta "
                  f"{'acepta' if acepta else 'rechaza'}",
                  (f.visita_modificable is not None) == acepta and (respuesta is not None) == acepta,
                  f"(ficha {f.visita_modificable}, ruta {motivo_de(error)})")
    # Y la fecha que da la ficha es exactamente la que acepta la ruta.
    f = leer_ficha(cur, op_a)
    comprobar("confirmada futura: visita_modificable.fecha_visita = la visita, acciones [cambiar, cancelar]",
              f.visita_modificable is not None and f.visita_modificable.fecha_visita == instante(D_S3A, 8, 30)
              and [a.value for a in f.visita_modificable.acciones] == ["cambiar", "cancelar"],
              f"({f.visita_modificable})")

    # ==================================================================
    print("\nS4. V1: cambiar")
    # ==================================================================
    op_1 = oportunidad()
    vieja_1 = visita(op_1, "confirmada", instante(D_S4, 8, 30))
    # Con espacios en el informe: se guarda recortado.
    respuesta, diferido, error = ejecutar(datos(op_1, "cambiar", instante(D_S4, 8, 30), instante(D_S4, 11, 0),
                                                informe="  Informe S4.  "))
    # La respuesta: creado, sus ids y su estado.
    comprobar("201: creado, accion cambiar, 'visita_agendada', sin motivo, visita_cancelada_id = la vieja",
              respuesta is not None and respuesta.creado and respuesta.accion.value == "cambiar"
              and respuesta.estado_oportunidad == "visita_agendada" and respuesta.motivo is None
              and respuesta.visita_cancelada_id == vieja_1 and respuesta.visita_id not in (None, vieja_1),
              f"({respuesta}, {motivo_de(error)})")
    # La fecha y su fin (D33), con el mismo desfase.
    comprobar(f"fecha_visita = 11:00 y fecha_fin_visita = + {DURACION} min, mismo desfase",
              respuesta is not None and respuesta.fecha_visita == instante(D_S4, 11, 0)
              and respuesta.fecha_fin_visita == fin_esperado(instante(D_S4, 11, 0))
              and respuesta.fecha_fin_visita.utcoffset() == respuesta.fecha_visita.utcoffset())
    # SQL: las visitas de la oportunidad, en orden.
    cur.execute("SELECT id, estado, texto_cliente FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (op_1,))
    visitas_1 = cur.fetchall()
    comprobar("visitas: la vieja 'cancelada' y la nueva 'confirmada' con el texto fijo (P6)",
              [(e, t) for _, e, t in visitas_1] == [("cancelada", "visita de prueba svc D46"),
                                                     ("confirmada", TEXTO_CAMBIADA)]
              and respuesta is not None and visitas_1[-1][0] == respuesta.visita_id, f"({visitas_1})")
    # SQL: la fila de cambios_visita.
    cur.execute("SELECT id, accion, visita_anterior_id, visita_nueva_id, motivo, informe FROM cambios_visita "
                "WHERE oportunidad_id = %s;", (op_1,))
    filas = cur.fetchall()
    comprobar("una fila en cambios_visita: (cambiar, vieja, nueva, sin motivo, informe recortado) con su id",
              respuesta is not None and filas == [(respuesta.cambio_id, "cambiar", vieja_1, respuesta.visita_id, None,
                                                   "Informe S4.")], f"({filas})")
    # SQL: la oportunidad.
    cur.execute("SELECT estado, fecha_ultimo_contacto IS NOT NULL FROM oportunidades WHERE id = %s;", (op_1,))
    comprobar("oportunidad sigue en 'visita_agendada' y fecha_ultimo_contacto escrita (P8)",
              cur.fetchone() == ("visita_agendada", True))
    # SQL: el log del cambio.
    cur.execute("SELECT detalle, detalle::text FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                "AND accion = 'visita_confirmada_cambiada';", (op_1,))
    logs = cur.fetchall()
    comprobar("un log visita_confirmada_cambiada con estado, ids y fechas, SIN el informe (N12)",
              respuesta is not None and len(logs) == 1 and logs[0][0].get("estado") == "visita_agendada"
              and logs[0][0].get("cambio_id") == respuesta.cambio_id and logs[0][0].get("visita_cancelada_id") == vieja_1
              and logs[0][0].get("visita_id") == respuesta.visita_id and "Informe S4" not in logs[0][1]
              and "informe" not in logs[0][0], f"({logs})")

    # ==================================================================
    print("\nS5. V4 y V2: repetición y segundo cambio")
    # ==================================================================
    antes = foto(op_1)
    # La misma petición con otro informe.
    respuesta_r, _, error = ejecutar(datos(op_1, "cambiar", instante(D_S4, 8, 30), instante(D_S4, 11, 0),
                                           informe="Otro informe."))
    comprobar("repetición: creado False, el mismo cambio_id y la misma visita, nada escrito",
              respuesta_r is not None and not respuesta_r.creado and respuesta is not None
              and respuesta_r.cambio_id == respuesta.cambio_id and respuesta_r.visita_id == respuesta.visita_id
              and foto(op_1) == antes, f"({respuesta_r}, {motivo_de(error)})")
    comprobar("repetición: fecha_fin_visita correcta (lee las reglas, D33)",
              respuesta_r is not None and respuesta_r.fecha_fin_visita == fin_esperado(instante(D_S4, 11, 0)))
    # Un segundo cambio (sin límite, D46.2): de las 11:00 a las 17:00.
    respuesta_2, _, error = ejecutar(datos(op_1, "cambiar", instante(D_S4, 11, 0), instante(D_S4, 17, 0)))
    # SQL: los estados de las visitas y las filas de cambios.
    cur.execute("SELECT estado FROM visitas WHERE oportunidad_id = %s ORDER BY id;", (op_1,))
    estados_1 = [f[0] for f in cur.fetchall()]
    cur.execute("SELECT count(*) FROM cambios_visita WHERE oportunidad_id = %s;", (op_1,))
    comprobar("segundo cambio: 201, dos filas y solo la última confirmada",
              respuesta_2 is not None and respuesta_2.creado and cur.fetchone()[0] == 2
              and estados_1 == ["cancelada", "cancelada", "confirmada"], f"({estados_1}, {motivo_de(error)})")
    # La primera petición otra vez: ya no es repetición (su nueva está cancelada).
    _, _, error = ejecutar(datos(op_1, "cambiar", instante(D_S4, 8, 30), instante(D_S4, 11, 0)))
    comprobar("la primera petición tras el segundo cambio -> 409 visita_cambiada (no 200)",
              motivo_de(error) == "visita_cambiada", f"({motivo_de(error)})")

    # ==================================================================
    print("\nS6. V3 y V4: cancelar")
    # ==================================================================
    op_2 = oportunidad()
    vieja_2 = visita(op_2, "confirmada", instante(D_S6, 9, 45))
    respuesta, _, error = ejecutar(datos(op_2, "cancelar", instante(D_S6, 9, 45), motivo="plazo"))
    comprobar("201: creado, accion cancelar, motivo plazo, 'perdida', sin visita nueva ni fechas",
              respuesta is not None and respuesta.creado and respuesta.accion.value == "cancelar"
              and respuesta.motivo.value == "plazo" and respuesta.estado_oportunidad == "perdida"
              and respuesta.visita_id is None and respuesta.fecha_visita is None and respuesta.fecha_fin_visita is None
              and respuesta.visita_cancelada_id == vieja_2, f"({respuesta}, {motivo_de(error)})")
    # SQL: la visita, la oportunidad y la fila.
    cur.execute("SELECT (SELECT estado FROM visitas WHERE id = %s), o.estado, o.fecha_ultimo_contacto IS NOT NULL, "
                "(SELECT array_agg(ARRAY[accion, visita_anterior_id::text, coalesce(visita_nueva_id::text, '-'), "
                "motivo, informe]) FROM cambios_visita WHERE oportunidad_id = o.id) FROM oportunidades o "
                "WHERE o.id = %s;", (vieja_2, op_2))
    fila = cur.fetchone()
    comprobar("visita 'cancelada', oportunidad 'perdida' con contacto, fila (cancelar, vieja, -, plazo, informe)",
              fila == ("cancelada", "perdida", True, [["cancelar", str(vieja_2), "-", "plazo",
                                                       "Informe de prueba svc D46."]]), f"({fila})")
    # SQL: el log de la cancelación.
    cur.execute("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                "AND accion = 'visita_confirmada_cancelada';", (op_2,))
    logs = [f[0] for f in cur.fetchall()]
    comprobar("un log visita_confirmada_cancelada con estado 'perdida' y motivo, sin informe",
              len(logs) == 1 and logs[0].get("estado") == "perdida" and logs[0].get("motivo") == "plazo"
              and "informe" not in logs[0], f"({logs})")
    antes = foto(op_2)
    # La repetición: 200 sin escribir (va antes que el estado, ya 'perdida').
    respuesta_r, _, error = ejecutar(datos(op_2, "cancelar", instante(D_S6, 9, 45), motivo="plazo",
                                           informe="Otro informe."))
    comprobar("repetición: creado False, el mismo cambio_id, nada escrito (N10: no 409)",
              respuesta_r is not None and not respuesta_r.creado and respuesta is not None
              and respuesta_r.cambio_id == respuesta.cambio_id and foto(op_2) == antes,
              f"({respuesta_r}, {motivo_de(error)})")
    # Otro motivo, y cambiar: ya no se puede.
    _, _, error_m = ejecutar(datos(op_2, "cancelar", instante(D_S6, 9, 45), motivo="precio"))
    _, _, error_c = ejecutar(datos(op_2, "cambiar", instante(D_S6, 9, 45), instante(D_S6, 11, 0)))
    comprobar("tras cancelar: otro motivo y cambiar -> 409 visita_ya_cancelada, nada escrito",
              motivo_de(error_m) == "visita_ya_cancelada" and motivo_de(error_c) == "visita_ya_cancelada"
              and foto(op_2) == antes, f"({motivo_de(error_m)}, {motivo_de(error_c)})")

    # ==================================================================
    print("\nS7. V5 y 404")
    # ==================================================================
    op_3 = oportunidad()
    visita(op_3, "confirmada", instante(D_S7, 8, 30))
    antes = foto(op_3)
    # La pantalla cree que la visita es a las 09:45 (desfasada).
    _, _, error = ejecutar(datos(op_3, "cambiar", instante(D_S7, 9, 45), instante(D_S7, 11, 0)))
    comprobar("fecha_actual que no es la confirmada -> 409 visita_cambiada, nada escrito",
              motivo_de(error) == "visita_cambiada" and foto(op_3) == antes, f"({motivo_de(error)})")
    # Una oportunidad que no existe (el máximo de INTEGER).
    _, _, error = ejecutar(datos(2147483647, "cancelar", instante(D_S7, 8, 30), motivo="otro"))
    comprobar("oportunidad inexistente -> oportunidad_no_encontrada (404)",
              motivo_de(error) == "oportunidad_no_encontrada", f"({motivo_de(error)})")

    # ==================================================================
    print("\nS8. V6: los 422 de la hora nueva")
    # ==================================================================
    # (título, hora nueva, motivo esperado), todos sobre op_3 (08:30 de D_S7).
    for titulo, nueva, esperado in (("la misma hora", instante(D_S7, 8, 30), "misma_hora_actual"),
                                    ("un sábado", instante(SABADO, 9, 45), "fin_de_semana"),
                                    ("fuera de la rejilla", instante(D_S7, 10, 0), "fuera_de_rejilla"),
                                    ("ayer", instante(AYER, 9, 45), "fecha_pasada")):
        # Ninguno escribe.
        _, _, error = ejecutar(datos(op_3, "cambiar", instante(D_S7, 8, 30), nueva))
        comprobar(f"{titulo} -> 422 {esperado}, nada escrito", motivo_de(error) == esperado and foto(op_3) == antes,
                  f"({motivo_de(error)})")
    # Hoy (R5 de D45): la última hora de la rejilla de hoy, 18:15. Si aún no
    # ha llegado, antelacion_insuficiente (hoy es anterior al 1.er laborable
    # de administración); si ya pasó, fecha_pasada. Las dos ramas son
    # deterministas; la regla en sí la prueban S2 y la antelación de D45.
    hoy_tarde = instante(HOY, 18, 15)
    # Qué rama toca, con el now() de Postgres, y se dice.
    esperado = "antelacion_insuficiente" if hoy_tarde > AHORA else "fecha_pasada"
    print(f"  [INFO] hoy a las 18:15 {'aún no ha llegado' if hoy_tarde > AHORA else 'ya pasó'}: se espera {esperado}")
    # El cambio a hoy (no escribe nada).
    _, _, error = ejecutar(datos(op_3, "cambiar", instante(D_S7, 8, 30), hoy_tarde))
    comprobar(f"hoy a las 18:15 -> 422 {esperado}", motivo_de(error) == esperado, f"({motivo_de(error)})")
    # El 1.er laborable de administración, calculado aquí (no con el código):
    # pasa la antelación. Responde 201 si las 08:30 están libres o 409
    # hueco_ocupado si no (el hueco se mira DESPUÉS de la fecha); con la
    # antelación del chat (N8) daría antelacion_insuficiente.
    dia, faltan = HOY, REGLAS_LEIDAS["dias_antelacion_visita_admin"]
    # Los laborables que faltan, uno a uno.
    while faltan > 0:
        # El día siguiente; solo cuenta de lunes a viernes.
        dia += timedelta(days=1)
        # True cuenta como 1: solo resta de lunes (0) a viernes (4).
        faltan -= dia.weekday() < 5
    # El cambio a ese día a las 08:30, deshecho al momento.
    respuesta, _, error = ejecutar(datos(op_3, "cambiar", instante(D_S7, 8, 30), instante(dia, 8, 30)), deshacer=True)
    print(f"  [INFO] 1.er laborable de administración: {dia} -> {'201' if respuesta else motivo_de(error)}")
    comprobar(f"el 1.er laborable de administración ({dia}) pasa la antelación (201 o hueco_ocupado)",
              (respuesta is not None and respuesta.creado) or motivo_de(error) == "hueco_ocupado",
              f"({motivo_de(error)})")

    # ==================================================================
    print("\nS9. V7: el hueco libre")
    # ==================================================================
    # op_4: una confirmada antigua fuera de la rejilla (09:00), que choca con
    # las 09:45; op_5: otra oportunidad propia a las 12:15 del mismo día.
    op_4 = oportunidad()
    visita(op_4, "confirmada", instante(D_S9, 9, 0))
    op_5 = oportunidad()
    visita(op_5, "confirmada", instante(D_S9, 12, 15))
    # A las 09:45: choca con la propia (09:00), que no cuenta (N3).
    respuesta, _, error = ejecutar(datos(op_4, "cambiar", instante(D_S9, 9, 0), instante(D_S9, 9, 45)), deshacer=True)
    comprobar("a una hora que choca con la PROPIA visita vieja -> 201 (no cuenta, se cancela)",
              respuesta is not None and respuesta.creado, f"({motivo_de(error)})")
    # A las 12:15: la ocupa op_5.
    _, _, error = ejecutar(datos(op_4, "cambiar", instante(D_S9, 9, 0), instante(D_S9, 12, 15)))
    libres = getattr(error, "huecos_libres", None)
    comprobar("a la hora de OTRA visita -> 409 hueco_ocupado con huecos_libres (sin 12:15, con 09:45)",
              motivo_de(error) == "hueco_ocupado" and isinstance(libres, list) and "12:15" not in libres
              and "09:45" in libres, f"({motivo_de(error)}, {libres})")

    # ==================================================================
    print("\nS10. V11: 503 al cambiar; cancelar no lee las reglas")
    # ==================================================================
    op_6 = oportunidad()
    visita(op_6, "confirmada", instante(D_S10, 8, 30))
    antes = foto(op_6)
    # Un nombre de regla falso EN MEMORIA (la fila real no se toca).
    reglas_visita.CLAVE_DURACION = "regla_de_visita_que_no_existe"
    reglas_visita.CLAVES_REGLAS = tuple(reglas_visita.CLAVE_DURACION if c == CLAVE_DURACION_REAL else c
                                        for c in CLAVES_REGLAS_REAL)
    respuesta, diferido, error = ejecutar(datos(op_6, "cambiar", instante(D_S10, 8, 30), instante(D_S10, 11, 0)))
    comprobar("cambiar con una regla rota: ninguna respuesta y un ConfiguracionIncompleta diferido",
              respuesta is None and isinstance(diferido, ConfiguracionIncompleta) and error is None,
              f"({respuesta}, {diferido}, {motivo_de(error)})")
    # SQL: los logs de configuración incompleta de op_6, y lo demás sin tocar.
    cur.execute("SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                "AND accion = 'visita_configuracion_incompleta';", (op_6,))
    logs = [f[0] for f in cur.fetchall()]
    despues = foto(op_6)
    comprobar("1 log con origen 'cambios_visita' y nada más escrito (cambios, visitas, estado)",
              len(logs) == 1 and logs[0].get("origen") == "cambios_visita"
              and despues[:2] == antes[:2] and despues[3:] == antes[3:] and despues[2] == antes[2] + 1, f"({logs})")
    # Cancelar con la regla rota: no la necesita.
    respuesta, _, error = ejecutar(datos(op_6, "cancelar", instante(D_S10, 8, 30), motivo="otro"))
    comprobar("cancelar con la regla rota -> 201 (no lee las reglas)", respuesta is not None and respuesta.creado,
              f"({motivo_de(error)})")
    # Se restauran los nombres (también en el finally, por si acaso).
    reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS = CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL

    # ==================================================================
    print("\nS11. V13 en el servicio (P5): la repetición de la llamada tras el cambio")
    # ==================================================================
    # Un Gate propio y su visita acordada por POST /resultados-llamada.
    op_7 = oportunidad("pendiente_aprobacion", gate=True)
    llamada = ResultadoLlamadaCreate(oportunidad_id=op_7, resultado="visita_acordada", fecha=str(D_S11), hora="08:30",
                                     informe="Llamada de prueba svc D46.")
    # try: un commit() dentro sería CommitProhibido -> FALLO contado.
    try:
        # La llamada y, enseguida, su repetición.
        r1, _ = registrar_resultado_con_cursor(cur, llamada)
        r2, _ = registrar_resultado_con_cursor(cur, llamada)
    # Cualquier excepción: sin respuestas (FALLO contado abajo) y se dice cuál.
    except Exception as error:
        r1 = r2 = None
        print(f"  (excepción: {type(error).__name__} {getattr(error, 'motivo', '')})")
    comprobar("control: la llamada se registra (201) y su repetición, ANTES del cambio, da 200",
              r1 is not None and r1.creado and r2 is not None and not r2.creado)
    # El cambio de esa visita, de las 08:30 a las 11:00.
    respuesta, _, error = ejecutar(datos(op_7, "cambiar", instante(D_S11, 8, 30), instante(D_S11, 11, 0)))
    # La repetición de la llamada original, otra vez.
    try:
        registrar_resultado_con_cursor(cur, llamada)
    except RechazoNegocio as error_p5:
        rechazo = error_p5
    # else: no salió ninguna excepción -> la repetición dio 200 (FALLO).
    else:
        rechazo = None
    comprobar("tras el cambio, la repetición -> 409 visita_modificada_despues con su texto (no 200)",
              respuesta is not None and rechazo is not None and rechazo.motivo == "visita_modificada_despues"
              and rechazo.mensaje == MENSAJE_P5, f"({motivo_de(error)}, {motivo_de(rechazo)})")

    # ==================================================================
    print("\nS12. V10 en el servicio: la ficha tras cambiar y tras cancelar")
    # ==================================================================
    # op_1: dos cambios (08:30 -> 11:00 -> 17:00).
    f = leer_ficha(cur, op_1)
    # SQL: las acciones guardadas en la tabla, en orden (para tipo_cambio).
    cur.execute("SELECT accion FROM cambios_visita WHERE oportunidad_id = %s ORDER BY created_at, id;", (op_1,))
    acciones_tabla = [a[0] for a in cur.fetchall()]
    # tipo_cambio de cada fila, igual a la accion guardada (tutor), y sus fechas.
    comprobar("ficha tras dos cambios: tipo_cambio = la accion de la tabla en cada fila, fechas e informe",
              [c.tipo_cambio.value for c in f.cambios_visita] == acciones_tabla == ["cambiar", "cambiar"]
              and [(c.fecha_visita_anterior, c.fecha_visita_nueva) for c in f.cambios_visita]
              == [(instante(D_S4, 8, 30), instante(D_S4, 11, 0)), (instante(D_S4, 11, 0), instante(D_S4, 17, 0))]
              and f.cambios_visita[0].informe == "Informe S4.", f"({f.cambios_visita})")
    comprobar("ficha tras dos cambios: visita_modificable con la de las 17:00",
              f.visita_modificable is not None and f.visita_modificable.fecha_visita == instante(D_S4, 17, 0))
    comprobar("ficha tras dos cambios: historial_cuadra true y ningún evento por el cambio (P9)",
              f.historial_cuadra and all(e.evento.value != "visita_confirmada_cambiada" for e in f.historial))
    # op_2: cancelada.
    f = leer_ficha(cur, op_2)
    comprobar("ficha tras cancelar: visita_modificable null y un cambio (cancelar, plazo, sin fecha nueva)",
              f.visita_modificable is None and [(c.tipo_cambio.value, c.motivo.value if c.motivo else None,
                                                 c.fecha_visita_nueva) for c in f.cambios_visita]
              == [("cancelar", "plazo", None)], f"({f.cambios_visita})")
    comprobar("ficha tras cancelar: último evento visita_confirmada_cancelada en 'perdida' y historial_cuadra (N13)",
              f.historial_cuadra and f.historial[-1].evento.value == "visita_confirmada_cancelada"
              and f.historial[-1].estado.value == "perdida", f"({f.historial})")
finally:
    # Los nombres reales de las reglas, pase lo que pase.
    reglas_visita.CLAVE_DURACION, reglas_visita.CLAVES_REGLAS = CLAVE_DURACION_REAL, CLAVES_REGLAS_REAL
    # Todo lo escrito, deshecho.
    cn.rollback()

# Nada queda: el cliente de prueba no existe fuera de la transacción.
# SQL: los clientes con la marca de esta ejecución.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# Se cierra la lectura y la conexión.
cn.rollback()
cn.close()

# Resumen y código de salida (1 si hubo fallos; lo mira la suite).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con fallos, se listan y el código de salida es 1.
if fallos:
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
