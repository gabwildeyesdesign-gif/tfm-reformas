"""
Verificación SIN servidor de la dirección de la obra (D42; rama
feat/n0-direccion-obra; veredicto del tutor al PASO 0, 2026-10-22).

  S0  autoprueba de ConexionSinCommit (rama else: "se ha tragado").
  S1  el esquema (sin base de datos): los resultados que existen y cuáles
      dejan visita; null = no enviarla; un objeto con un resultado SIN
      visita da error; cada regla de D42.3 (vacío, solo espacios, largo,
      código postal fuera del patrón, clave de más o de menos); el recorte;
      los límites buenos; y el código postal como NÚMERO da error (N15).
  S2  estático (ast y texto), D42.5 y H2: la tabla solo se nombra en el
      servicio que escribe y en la ficha; ningún modelo de respuesta, salvo
      la ficha, tiene un campo de dirección; el error saneado se lanza con
      "from None" y su función no usa pgerror, message_detail ni el texto
      del error.
  S3  con registrar_resultado_con_cursor, en UNA transacción con ROLLBACK
      (ConexionSinCommit): los cinco resultados (fila guardada y
      "direccion_obra_registrada" true/false, o sin la clave); la
      repetición NO mira ni escribe la dirección (H1); el upsert SUSTITUYE
      una fila previa (D42.4); y H2: una dirección que Postgres rechaza (el
      esquema saltado con model_construct) da DireccionObraNoGuardada sin
      el texto, con pgcode y restricción y sin error encadenado.
  S4  con commit REAL, fuera del ROLLBACK (registrar_resultado y el pool):
      todo o nada (N9): un error forzado DESPUÉS del upsert (fin_de_visita,
      cambiada en memoria) no deja ni dirección, ni visita, ni resultado, ni
      log; y H2 por la misma vía: nada escrito y la marca 0 veces en logs.

H4 del veredicto: NUNCA imprime una dirección (tampoco las de prueba): solo
recuentos, verdadero/falso, pgcode y nombres de restricción.

Recuentos: se calculan en la fase 2 y se escriben en el commit (D37.5).

Las 4 preguntas: (1) S0-S3 escriben en tablas reales SOLO dentro de una
transacción que se deshace siempre; S4 SÍ confirma: Gates propios (emails
check-direccion-svc-u-<8>@example.com) creados por los servicios y borrados
por la marca al empezar y al terminar; (2) S3 pasa su cursor al código, por
eso ConexionSinCommit; S4 no pasa ninguna conexión (los servicios usan el
pool); (3) si se corta, Postgres deshace la transacción abierta y S4 borra
sus restos al empezar; al final se comprueba que no queda nada propio; (4)
no toca el 8000 ni ninguna fila real; lo cambiado en memoria (fin_de_visita)
se restaura en finally. Sus visitas van en su bloque de fechas_prueba.py,
con exigir_libres antes de crear nada.
"""

# ast: comprobaciones de estructura. sys: consola y salida. uuid: marcas.
import ast
import sys
import uuid
# Decimal: m2 exacto para LeadCreate (S4).
from decimal import Decimal
# Fechas en Madrid.
from datetime import date, datetime
from pathlib import Path
from typing import get_args
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
# BaseModel y ValidationError: recorrer los modelos y esperar el 422.
from pydantic import BaseModel, ValidationError

# El módulo entero del servicio, para cambiar fin_de_visita en memoria (S4).
import app.services.resultados_llamada_service as servicio
# La cadena de conexión del .env (nunca se imprime) y el pool (S4).
from app.config import DATABASE_URL
from app.db import connection as db
# Lo que se prueba: el esquema y sus límites.
from app.schemas.resultados_llamada import (
    CON_FECHA,
    MAX_CALLE_NUMERO,
    MAX_MUNICIPIO,
    DireccionObra,
    ResultadoLlamada,
    ResultadoLlamadaCreate,
    ResultadoLlamadaResponse,
)
# Los modelos de TODAS las respuestas (rutas y tools), para S2.
from app.schemas.aviso_gate import AvisoGateResponse
from app.schemas.business_rules import BusinessRulesResponse
# D46 (precisión 4 del tutor): POST /cambios-visita, entrada y respuesta.
from app.schemas.cambios_visita import CambioVisitaCreate, CambioVisitaResponse
from app.schemas.estimates import EstimateResponse
# La ficha: la ÚNICA que puede llevarla (control positivo).
from app.schemas.ficha_oportunidad import FichaOportunidadResponse
from app.schemas.followup_tasks import FollowupTaskResponse
from app.schemas.huecos_libres import HuecosDiaResponse, HuecosDiasAgente, HuecosDiasResponse
# Alta y sesión (LeadCreate lo usa también S4).
from app.schemas.leads import LeadCreate, LeadCreateResponse, LeadSessionResponse
from app.schemas.listado_llamadas import ListadoLlamadasResponse
from app.schemas.visits import VisitaResponse
# S4: los servicios de alta y cálculo (para los Gates propios).
from app.services.estimate_service import calculate_estimate
from app.services.leads_service import create_lead

# Las fechas propias de este script (bloque de semanas lejanas).
import fechas_prueba

# La marca de la transacción de S0-S3 (dominio reservado).
EMAIL = "check-direccion-svc@example.com"
# La marca de ESTA ejecución, dentro de los textos de las direcciones: si
# aparece en algún sitio donde no debe, es una fuga (D42.5).
MARCA = f"MARCA-D42-{uuid.uuid4().hex[:10]}"
# Dos direcciones válidas de prueba (inventadas; nunca se imprimen).
DIR_A = {"calle_numero": f"Calle {MARCA} 1", "codigo_postal": "28013", "municipio": "Municipio de Prueba"}
DIR_B = {"calle_numero": f"Avenida {MARCA} 2", "codigo_postal": "08001", "municipio": "Otro Municipio"}
# Las cuatro claves que NO pueden salir en ninguna respuesta salvo la ficha.
CLAVES_DIRECCION = {"direccion_obra", "calle_numero", "codigo_postal", "municipio"}
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
    # Incorrecta: se apunta y se imprime con el detalle (nunca una dirección).
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
DIAS = fechas_prueba.Dias("check_direccion_obra_service", HOY)
# S3: Gate, seguimiento, dos confirmaciones, repetición y sustitución; S4: dos.
D_GATE, D_SEG, D_CONF, D_OTRA, D_REP, D_SUST, D_H2, D_TODO, D_H2B = (DIAS.siguiente() for _ in range(9))
# Ninguna visita activa AJENA en el bloque (antes de crear nada).
fechas_prueba.exigir_libres("check_direccion_obra_service", "check-direccion-svc%@example.com",
                            [fechas_prueba.tramo_del_bloque("check_direccion_obra_service", HOY)])


def instante(dia: date, hh: int, mm: int) -> datetime:
    """Ese día a esa hora, en Madrid."""
    # combine con la zona: el desfase correcto de ese día.
    return datetime.combine(dia, datetime.min.time().replace(hour=hh, minute=mm), tzinfo=MADRID)


def cuerpo(resultado, **campos):
    """El diccionario de una petición, con un informe de prueba."""
    # oportunidad_id 1 si no se dice (S1 no toca la base de datos).
    return {"oportunidad_id": 1, "resultado": resultado, "informe": "prueba D42", **campos}


def valido(datos):
    """(True, None) si el esquema acepta los datos; (False, error) si no."""
    # Se construye el modelo: si una regla falla, ValidationError.
    try:
        modelo = ResultadoLlamadaCreate(**datos)
    except ValidationError as error:
        return False, error
    return True, modelo


# ======================================================================
print("S0. Autoprueba de ConexionSinCommit")
# ======================================================================
# La conexión de S0-S3, con commit() prohibido.
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
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False, "(la excepción se ha tragado)")

# ======================================================================
print("\nS1. El esquema (sin base de datos)")
# ======================================================================
# Los resultados que existen y los que dejan visita (veredicto: enumerarlos).
TODOS = [r.value for r in ResultadoLlamada]
CON_VISITA = [r.value for r in CON_FECHA]
SIN_VISITA = [r for r in TODOS if r not in CON_VISITA]
print(f"  [INFO] resultados: {TODOS}; con visita: {CON_VISITA}; sin visita: {SIN_VISITA}")
comprobar("los resultados sin visita son descartar y cliente_cancela",
          sorted(SIN_VISITA) == ["cliente_cancela", "descartar"], f"({SIN_VISITA})")
# Los campos de cada resultado (fecha y hora, o motivo), para S1.
CAMPOS = {"visita_acordada": {"fecha": "2030-01-07", "hora": "09:45"},
          "confirmar_hora": {"fecha": "2030-01-07", "hora": "09:45"},
          "otra_hora": {"fecha": "2030-01-07", "hora": "09:45"},
          "descartar": {"motivo": "precio"}, "cliente_cancela": {"motivo": "precio"}}
# Cada resultado: sin la clave, con null y con un objeto.
for r in TODOS:
    # Sin la clave y con null: lo mismo, válido en todos.
    sin_clave, _ = valido(cuerpo(r, **CAMPOS[r]))
    con_null, m = valido(cuerpo(r, direccion_obra=None, **CAMPOS[r]))
    comprobar(f"{r}: sin direccion_obra y con null -> válido (null = no enviarla)",
              sin_clave and con_null and m.direccion_obra is None)
    # Con un objeto: válido con visita; 422 sin ella.
    acepta, _ = valido(cuerpo(r, direccion_obra=DIR_A, **CAMPOS[r]))
    comprobar(f"{r}: con direccion_obra -> {'válido' if r in CON_VISITA else '422 (no queda visita)'}",
              acepta == (r in CON_VISITA))


def direccion_valida(**cambios):
    """True si el esquema acepta DIR_A con esos cambios (en visita_acordada)."""
    # La dirección de partida, con los cambios encima.
    acepta, _ = valido(cuerpo("visita_acordada", direccion_obra={**DIR_A, **cambios}, **CAMPOS["visita_acordada"]))
    return acepta


# Cada regla de D42.3: lo que NO pasa.
for titulo, cambios in (("calle vacía", {"calle_numero": ""}), ("calle solo espacios", {"calle_numero": "   "}),
                        (f"calle de {MAX_CALLE_NUMERO + 1}", {"calle_numero": "x" * (MAX_CALLE_NUMERO + 1)}),
                        ("municipio vacío", {"municipio": ""}), ("municipio solo espacios", {"municipio": "\t \n"}),
                        (f"municipio de {MAX_MUNICIPIO + 1}", {"municipio": "x" * (MAX_MUNICIPIO + 1)}),
                        ("código postal 00123", {"codigo_postal": "00123"}),
                        ("código postal 53000", {"codigo_postal": "53000"}),
                        ("código postal de 4 cifras", {"codigo_postal": "2801"}),
                        ("código postal de 6 cifras", {"codigo_postal": "280130"}),
                        ("código postal con letra", {"codigo_postal": "2801a"}),
                        ("código postal con espacio en medio", {"codigo_postal": "28 13"}),
                        ("código postal con cifras árabes orientales", {"codigo_postal": "٢٨٠١٣"}),
                        ("código postal vacío", {"codigo_postal": ""}),
                        ("código postal como NÚMERO 28013 (N15)", {"codigo_postal": 28013}),
                        ("código postal null", {"codigo_postal": None}),
                        # H-D42-1 (C1): el carácter nulo, que psycopg2 rechazaría con un 500.
                        ("calle con el carácter nulo U+0000", {"calle_numero": "Calle\u0000 1"}),
                        ("municipio con el carácter nulo U+0000", {"municipio": "Madr\u0000id"})):
    comprobar(f"{titulo} -> 422", direccion_valida(**cambios) is False)
# Clave de más y de menos, y un texto en vez de un objeto.
acepta, _ = valido(cuerpo("visita_acordada", direccion_obra={**DIR_A, "piso": "3B"}, **CAMPOS["visita_acordada"]))
comprobar("clave de más ('piso') -> 422 (extra='forbid')", acepta is False)
# Sin una de las tres claves.
acepta, _ = valido(cuerpo("visita_acordada", direccion_obra={k: v for k, v in DIR_A.items() if k != "municipio"},
                          **CAMPOS["visita_acordada"]))
comprobar("sin municipio -> 422", acepta is False)
# La dirección en un solo texto, no en un objeto.
acepta, _ = valido(cuerpo("visita_acordada", direccion_obra="Calle 1, 28013 Madrid", **CAMPOS["visita_acordada"]))
comprobar("un texto en vez de un objeto -> 422", acepta is False)
# Los límites buenos y el recorte.
for titulo, cambios in ((f"calle de {MAX_CALLE_NUMERO} 'á'", {"calle_numero": "á" * MAX_CALLE_NUMERO}),
                        (f"municipio de {MAX_MUNICIPIO} 'á'", {"municipio": "á" * MAX_MUNICIPIO}),
                        ("código postal 01001", {"codigo_postal": "01001"}),
                        ("código postal 52999", {"codigo_postal": "52999"})):
    comprobar(f"{titulo} -> válido", direccion_valida(**cambios) is True)
acepta, m = valido(cuerpo("visita_acordada", direccion_obra={"calle_numero": "  Calle 1 ", "codigo_postal": " 28013 ",
                                                             "municipio": "\tMadrid\n"}, **CAMPOS["visita_acordada"]))
comprobar("los tres textos se recortan (como exigen los CHECK)",
          acepta and m.direccion_obra.model_dump() == {"calle_numero": "Calle 1", "codigo_postal": "28013",
                                                       "municipio": "Madrid"})

# ======================================================================
print("\nS2. Estático: D42.5 y H2")
# ======================================================================


def textos_de_codigo(ruta):
    """Los textos entre comillas del código de un archivo (donde vive el SQL),
    sin docstrings ni comentarios (los comentarios no están en el ast)."""
    # El árbol del archivo.
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    # Los docstrings: la primera sentencia de módulo, clase o función, si es texto.
    docstrings = {id(n.body[0].value) for n in ast.walk(arbol)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    # Los demás textos (también los trozos de un f-string).
    return [n.value for n in ast.walk(arbol) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


# Los .py de app/ cuyo CÓDIGO nombra la tabla. (Corregido en la fase 2,
# 2026-10-22: antes se buscaba en el texto entero y contaba un comentario de
# app/schemas/resultados_llamada.py; un JOIN la nombraría en un texto.)
nombran = sorted(str(p.relative_to(RAIZ_REPO)).replace("\\", "/") for p in (RAIZ_REPO / "app").rglob("*.py")
                 if any("direcciones_obra" in t for t in textos_de_codigo(p)))
comprobar("solo el servicio de resultados y la ficha nombran direcciones_obra en su código",
          nombran == ["app/services/ficha_oportunidad_service.py", "app/services/resultados_llamada_service.py"],
          f"({nombran})")


def campos_recursivos(modelo, vistos=None):
    """Los nombres de los campos de un modelo y de sus submodelos (también
    dentro de "X | None", "list[X]" y uniones)."""
    # Cada modelo una sola vez.
    vistos = vistos if vistos is not None else set()
    if modelo in vistos:
        return set()
    vistos.add(modelo)
    # Los de este nivel, y los tipos de cada campo por abrir.
    nombres = set(modelo.model_fields)
    pendientes = [c.annotation for c in modelo.model_fields.values()]
    # Mientras queden tipos: sus tipos internos a la cola; un submodelo suma.
    while pendientes:
        tipo = pendientes.pop()
        pendientes.extend(get_args(tipo))
        if isinstance(tipo, type) and issubclass(tipo, BaseModel):
            nombres |= campos_recursivos(tipo, vistos)
    return nombres


# Todas las respuestas que NO pueden llevarla (rutas y tools MCP, D42.5).
PROHIBIDAS = (AvisoGateResponse, BusinessRulesResponse, EstimateResponse, FollowupTaskResponse, HuecosDiaResponse,
              HuecosDiasAgente, HuecosDiasResponse, LeadCreateResponse, LeadSessionResponse, ListadoLlamadasResponse,
              ResultadoLlamadaResponse, VisitaResponse,
              # D46 (2026-10-25): la respuesta de POST /cambios-visita.
              CambioVisitaResponse)
for modelo in PROHIBIDAS:
    # La intersección con las cuatro claves tiene que estar vacía.
    cruce = campos_recursivos(modelo) & CLAVES_DIRECCION
    comprobar(f"{modelo.__name__}: ningún campo de dirección", cruce == set(), f"({sorted(cruce)})")
# D46: la entrada de POST /cambios-visita no admite la dirección (es de la
# oportunidad, no de la visita): ningún campo de dirección y extra="forbid"
# (una clave direccion_obra da 422).
cruce = campos_recursivos(CambioVisitaCreate) & CLAVES_DIRECCION
comprobar("CambioVisitaCreate: ningún campo de dirección y extra='forbid'",
          cruce == set() and CambioVisitaCreate.model_config.get("extra") == "forbid", f"({sorted(cruce)})")
# Y la ficha SÍ (control positivo), como DireccionObra o null.
comprobar("FichaOportunidadResponse.direccion_obra existe y es obligatoria (objeto o null)",
          "direccion_obra" in FichaOportunidadResponse.model_fields
          and FichaOportunidadResponse.model_fields["direccion_obra"].is_required()
          and DireccionObra in get_args(FichaOportunidadResponse.model_fields["direccion_obra"].annotation))
# H2: la función del upsert, en el árbol del servicio.
arbol = ast.parse((RAIZ_REPO / "app/services/resultados_llamada_service.py").read_text(encoding="utf-8"))
funcion = next((f for f in ast.walk(arbol) if isinstance(f, ast.FunctionDef) and f.name == "_guardar_direccion"), None)
comprobar("existe _guardar_direccion", funcion is not None)
# Los raise de sus except: todos "from None" (cause es la constante None).
lanzados = [n for h in ast.walk(funcion) if isinstance(h, ast.ExceptHandler) for n in ast.walk(h)
            if isinstance(n, ast.Raise)] if funcion else []
comprobar("H2: el except del upsert vuelve a lanzar con 'from None'",
          bool(lanzados) and all(isinstance(n.cause, ast.Constant) and n.cause.value is None for n in lanzados),
          f"({len(lanzados)} raise)")
# Ni pgerror, ni message_detail, ni el error convertido en texto.
texto_funcion = ast.unparse(funcion) if funcion else ""
prohibidos = [p for p in ("pgerror", "message_detail", "str(error)", "{error}", "error.args") if p in texto_funcion]
comprobar("H2: el mensaje saneado no usa pgerror, message_detail ni el texto del error", prohibidos == [],
          f"({prohibidos})")

# ======================================================================
print("\nS3. registrar_resultado_con_cursor (todo se deshace al final)")
# ======================================================================


def oportunidad(estado, gate):
    """Una oportunidad propia en este estado, con presupuesto (con o sin Gate) de hace 50 h."""
    # SQL: la oportunidad del lead de prueba.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', %s) RETURNING id;",
                (LEAD, estado))
    op = cur.fetchone()[0]
    # SQL: su presupuesto, sin importes que importen aquí.
    cur.execute("INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva, "
                "requiere_aprobacion, iva_pct_aplicado, created_at) "
                "VALUES (%s, 1210, 2420, %s, 21, now() - make_interval(hours => 50));", (op, gate))
    return op


# La visita del chat, para las confirmaciones.
def visita_pedida(op, cuando):
    """Una visita 'solicitada' propia (la del chat)."""
    # SQL: la visita, con un texto de prueba.
    cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                "VALUES (%s, %s, 'solicitada', 'visita de prueba D42');", (op, cuando))


# Lo guardado, para compararlo en Python (nunca se imprime, H4).
def direccion_de(op):
    """La dirección guardada de la oportunidad como diccionario, o None (no se imprime, H4)."""
    # SQL: la fila de esa oportunidad (propia).
    cur.execute("SELECT calle_numero, codigo_postal, municipio, updated_at FROM direcciones_obra "
                "WHERE oportunidad_id = %s;", (op,))
    fila = cur.fetchone()
    return None if fila is None else {"calle_numero": fila[0], "codigo_postal": fila[1], "municipio": fila[2],
                                      "updated_at": fila[3]}


# Los logs de resultado, con su texto (para buscar la marca).
def logs_de(op):
    """Los detalles de los logs de resultado de la oportunidad (su texto JSON, para buscar la marca)."""
    # SQL: los logs de las dos acciones de resultado, con detalle como texto.
    cur.execute("SELECT detalle, detalle::text FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s "
                "AND accion IN ('gate_decision_registrada', 'llamada_registrada') ORDER BY id;", (op,))
    return cur.fetchall()


# El registro con el cursor de la prueba, dentro de su SAVEPOINT.
def registrar(op, resultado, direccion=None, **campos):
    """registrar_resultado_con_cursor con el cursor de la prueba; (respuesta, rechazo) o (None, excepción)."""
    # El cuerpo validado por el esquema (como en la ruta). Si el esquema lo
    # rechaza (un 422), se devuelve como rechazo: un FALLO contado, no una
    # traza (corregido en la fase 2: con la N14 el script reventaba aquí).
    try:
        data = ResultadoLlamadaCreate(oportunidad_id=op, resultado=resultado, informe="prueba D42",
                                      direccion_obra=direccion, **campos)
    except ValidationError as error:
        return None, error
    # SQL: un punto al que volver (un rechazo de negocio aborta la transacción).
    cur.execute("SAVEPOINT registro;")
    # Un commit() dentro sería CommitProhibido, y un rechazo de negocio (409,
    # 422) se devuelve también: los dos se cuentan como FALLO, sin traza.
    try:
        par = servicio.registrar_resultado_con_cursor(cur, data)
    # (DireccionObraNoGuardada también: así un upsert roto, N10, es un FALLO
    # contado y no una traza.)
    except (CommitProhibido, servicio.RechazoNegocio, servicio.DireccionObraNoGuardada) as error:
        # SQL: se vuelve al punto de guardado; la transacción sigue viva.
        cur.execute("ROLLBACK TO SAVEPOINT registro;")
        return None, error
    # SQL: bien; el punto de guardado ya no hace falta.
    cur.execute("RELEASE SAVEPOINT registro;")
    return par


# D42.5 en una respuesta del servicio.
def sin_marca(respuesta):
    """True si la respuesta (JSON) no lleva la marca (D42.5)."""
    # model_dump_json: el texto que saldría por HTTP.
    return respuesta is not None and MARCA not in respuesta.model_dump_json()


# Lo que se cambia en memoria en S4 (se guarda ya, por si acaso).
FIN_REAL = servicio.fin_de_visita
# try/finally: pase lo que pase, ROLLBACK.
try:
    # Datos de partida: un cliente y un lead propios.
    # SQL: el cliente, con la marca.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba D42', %s, '600000000') RETURNING id;",
                (EMAIL,))
    CLIENTE = cur.fetchone()[0]
    # SQL: su lead (m2 dentro del CHECK de leads).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_d42', %s) RETURNING id;",
                (CLIENTE, Json({"m2": 10})))
    LEAD = cur.fetchone()[0]

    # Los cinco resultados: (título, oportunidad, resultado, dirección,
    # campos, se espera fila, valor esperado de la clave del log).
    op_gate = oportunidad("pendiente_aprobacion", True)
    op_seg = oportunidad("seguimiento_pendiente", False)
    # Dos confirmaciones, cada una con su visita pedida a las 09:45.
    op_conf = oportunidad("visita_agendada", False)
    visita_pedida(op_conf, instante(D_CONF, 9, 45))
    op_otra = oportunidad("visita_agendada", False)
    visita_pedida(op_otra, instante(D_OTRA, 9, 45))
    # Los dos sin visita: un Gate que se descarta y una confirmación que se cancela.
    op_desc = oportunidad("pendiente_aprobacion", True)
    op_canc = oportunidad("visita_agendada", False)
    visita_pedida(op_canc, instante(D_OTRA, 11, 0))
    # La tabla de los casos.
    CASOS = [
        ("Gate visita_acordada CON dirección", op_gate, "visita_acordada", DIR_A,
         {"fecha": str(D_GATE), "hora": "11:00"}, True, True),
        ("seguimiento visita_acordada SIN dirección", op_seg, "visita_acordada", None,
         {"fecha": str(D_SEG), "hora": "11:00"}, False, False),
        ("confirmar_hora CON dirección", op_conf, "confirmar_hora", DIR_A,
         {"fecha": str(D_CONF), "hora": "09:45"}, True, True),
        ("otra_hora CON dirección", op_otra, "otra_hora", DIR_B,
         {"fecha": str(D_OTRA), "hora": "17:00"}, True, True),
        ("descartar (sin visita)", op_desc, "descartar", None, {"motivo": "precio"}, False, None),
        ("cliente_cancela (sin visita)", op_canc, "cliente_cancela", None, {"motivo": "plazo"}, False, None),
    ]
    for titulo, op, resultado, direccion, campos, con_fila, clave in CASOS:
        respuesta, rechazo = registrar(op, resultado, direccion, **campos)
        # Creado, sin rechazo, y la respuesta sin la marca.
        comprobar(f"{titulo}: creado y la respuesta no lleva la dirección",
                  rechazo is None and respuesta is not None and respuesta.creado and sin_marca(respuesta),
                  f"({type(rechazo).__name__ if rechazo else 'sin rechazo'})")
        # La fila: con dirección, exactamente la enviada; sin ella, ninguna.
        guardada = direccion_de(op)
        esperado = (guardada is not None and {k: guardada[k] for k in DIR_A} == direccion) if con_fila \
            else guardada is None
        comprobar(f"{titulo}: {'fila guardada con los tres textos' if con_fila else 'ninguna fila'}", esperado)
        # El log: la clave con su valor (o ausente sin visita) y sin la marca.
        logs = logs_de(op)
        comprobar(f"{titulo}: 1 log, '{servicio.CLAVE_LOG_DIRECCION}' = {clave}, sin la dirección",
                  len(logs) == 1 and logs[0][0].get(servicio.CLAVE_LOG_DIRECCION, None) == clave
                  and (clave is not None or servicio.CLAVE_LOG_DIRECCION not in logs[0][0])
                  and MARCA not in logs[0][1], f"({len(logs)} logs, clave {logs[0][0].get(servicio.CLAVE_LOG_DIRECCION) if logs else '-'})")

    # H1: la repetición NO mira la dirección ni la escribe.
    op_rep = oportunidad("pendiente_aprobacion", True)
    campos_rep = {"fecha": str(D_REP), "hora": "11:00"}
    primera, _ = registrar(op_rep, "visita_acordada", DIR_A, **campos_rep)
    antes = direccion_de(op_rep)
    logs_antes = len(logs_de(op_rep))
    # La MISMA petición con OTRA dirección: repetición (200), nada escrito.
    repetida, rechazo = registrar(op_rep, "visita_acordada", DIR_B, **campos_rep)
    comprobar("H1: repetición con OTRA dirección -> creado False (200), sin rechazo",
              rechazo is None and repetida is not None and repetida.creado is False and primera is not None
              and repetida.llamada_id == primera.llamada_id)
    comprobar("H1: la dirección guardada NO cambia (ni textos ni updated_at) y ningún log más",
              direccion_de(op_rep) == antes and antes is not None and len(logs_de(op_rep)) == logs_antes)
    # La misma petición SIN dirección: también repetición (no entra en la comparación).
    repetida, rechazo = registrar(op_rep, "visita_acordada", None, **campos_rep)
    comprobar("H1: repetición SIN dirección -> creado False y la guardada sigue",
              rechazo is None and repetida is not None and repetida.creado is False and direccion_de(op_rep) == antes)

    # D42.4: el upsert SUSTITUYE una fila que ya existía (hoy no hay otra
    # vía, H1: se crea a mano dentro de la transacción).
    op_sust = oportunidad("pendiente_aprobacion", True)
    # SQL: una fila previa con los textos de DIR_B.
    cur.execute("INSERT INTO direcciones_obra (oportunidad_id, calle_numero, codigo_postal, municipio) "
                "VALUES (%s, %s, %s, %s);", (op_sust, DIR_B["calle_numero"], DIR_B["codigo_postal"], DIR_B["municipio"]))
    respuesta, rechazo = registrar(op_sust, "visita_acordada", DIR_A, fecha=str(D_SUST), hora="11:00")
    guardada = direccion_de(op_sust)
    # SQL: cuántas filas tiene esa oportunidad.
    cur.execute("SELECT count(*) FROM direcciones_obra WHERE oportunidad_id = %s;", (op_sust,))
    comprobar("D42.4: con una fila previa, el upsert deja UNA fila con los textos nuevos (sin error)",
              rechazo is None and respuesta is not None and respuesta.creado and cur.fetchone()[0] == 1
              and guardada is not None and {k: guardada[k] for k in DIR_A} == DIR_A)

    # H2: una dirección que Postgres rechaza (el esquema saltado a propósito
    # con model_construct: 151 caracteres, con la marca).
    op_h2 = oportunidad("pendiente_aprobacion", True)
    # Con una dirección válida al construir (así vale también con la N14) y
    # la rota puesta después.
    data = ResultadoLlamadaCreate(oportunidad_id=op_h2, resultado="visita_acordada", informe="prueba H2",
                                  fecha=str(D_H2), hora="11:00", direccion_obra=DIR_A)
    data.direccion_obra = DireccionObra.model_construct(calle_numero=MARCA + "x" * MAX_CALLE_NUMERO,
                                                        codigo_postal="28013", municipio="Municipio de Prueba")
    # SQL: un punto al que volver (el error aborta la transacción).
    cur.execute("SAVEPOINT h2;")
    try:
        servicio.registrar_resultado_con_cursor(cur, data)
    # La esperada: sin la marca, con pgcode y restricción, sin encadenar.
    except servicio.DireccionObraNoGuardada as error:
        texto = str(error)
        comprobar("H2: DireccionObraNoGuardada sin el texto de la dirección", MARCA not in texto)
        comprobar("H2: lleva el pgcode 23514 y la restricción chk_direcciones_obra_calle_numero",
                  "23514" in texto and "chk_direcciones_obra_calle_numero" in texto)
        comprobar("H2: sin error encadenado (from None)",
                  error.__cause__ is None and error.__suppress_context__ is True)
    # Otra excepción: FALLO, sin enseñar su texto (podría llevar la fila).
    except Exception as error:
        comprobar("H2: DireccionObraNoGuardada", False, f"(salió {type(error).__name__})")
    # Ninguna: FALLO explícito (regla de CLAUDE.md, caso D16).
    else:
        comprobar("H2: DireccionObraNoGuardada", False, "(la excepción se ha tragado: no salió nada)")
    # SQL: se vuelve al punto de guardado.
    cur.execute("ROLLBACK TO SAVEPOINT h2;")
finally:
    # Siempre: ROLLBACK de todo.
    cn.rollback()

# Fuera de la transacción: no queda el cliente de prueba.
# SQL: clientes con la marca.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
comprobar("S3: no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
cn.rollback()
cn.close()

# ======================================================================
print("\nS4. Con commit REAL: todo o nada (N9) y H2 por registrar_resultado")
# ======================================================================
# Aquí no vale el ROLLBACK de arriba: lo que se prueba es justo el commit
# (y el rollback automático de get_transactional_connection).
PATRON_U = "check-direccion-svc-u-%@example.com"
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
                f"DELETE FROM direcciones_obra WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM decisiones_gate WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM visitas WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM presupuestos WHERE oportunidad_id IN ({ops});",
                f"DELETE FROM oportunidades WHERE id IN ({ops});",
                "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %(p)s);",
                "DELETE FROM clientes WHERE email LIKE %(p)s;"):
        # SQL: el DELETE de esa tabla, solo con la marca.
        cur_u.execute(sql, {"p": PATRON_U})
    # Un solo commit para todos.
    cn_u.commit()


def leer_u(sql, params):
    """Una fila de un SELECT por cn_u, y commit para ver lo último confirmado."""
    # SQL: el que llega (textos fijos de este archivo).
    cur_u.execute(sql, params)
    fila = cur_u.fetchone()
    cn_u.commit()
    return fila


def gate_u():
    """Un Gate propio por los servicios (como POST /leads y /calculate-estimate)."""
    # Token único; sus 8 primeros caracteres van en el email (la marca).
    token = str(uuid.uuid4())
    alta = create_lead(LeadCreate(
        nombre="Prueba D42 U", email=f"check-direccion-svc-u-{token[:8]}@example.com", telefono="600000000",
        tipo_reforma="bano", m2=Decimal("6"), nivel_acabados="medio", incluye_cambios_estructurales=True,
        fotos=[], lead_token=token))
    # Con cambios estructurales queda con Gate, en 'pendiente_aprobacion'.
    calculate_estimate(alta.oportunidad_id)
    return alta.oportunidad_id


# Todo lo que el registro puede escribir en una oportunidad.
SQL_FOTO_U = ("SELECT (SELECT count(*) FROM direcciones_obra WHERE oportunidad_id = %(op)s), "
              "(SELECT count(*) FROM decisiones_gate WHERE oportunidad_id = %(op)s), "
              "(SELECT count(*) FROM visitas WHERE oportunidad_id = %(op)s), "
              "(SELECT count(*) FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %(op)s "
              "AND accion = 'gate_decision_registrada'), o.estado FROM oportunidades o WHERE o.id = %(op)s;")


def lanzar_tras_el_upsert(*args, **kwargs):
    """fin_de_visita cambiada en memoria: se llama DESPUÉS del upsert, del
    resultado, del UPDATE y del log, dentro de la misma transacción."""
    # Un error cualquiera: get_transactional_connection hace rollback.
    raise RuntimeError("error forzado por check_direccion_obra_service (N9)")


# Restos de una ejecución cortada; el id base de logs; el pool.
limpiar_u()
LOGS_BASE = leer_u("SELECT COALESCE(max(id), 0) FROM logs;", ())[0]
db.init_pool()
# try/finally: pase lo que pase, fin_de_visita real, pool cerrado y limpieza.
try:
    # N9: el error llega después de TODAS las escrituras.
    op_todo = gate_u()
    servicio.fin_de_visita = lanzar_tras_el_upsert
    try:
        servicio.registrar_resultado(ResultadoLlamadaCreate(
            oportunidad_id=op_todo, resultado="visita_acordada", fecha=str(D_TODO), hora="11:00",
            informe="prueba N9", direccion_obra=DIR_A))
    # La esperada: el error forzado.
    except RuntimeError as error:
        comprobar("N9: sale el error forzado", "N9" in str(error), f"({type(error).__name__})")
    # Ninguna: FALLO explícito (sin el error, nada que probar).
    else:
        comprobar("N9: sale el error forzado", False, "(la excepción se ha tragado: no salió nada)")
    finally:
        servicio.fin_de_visita = FIN_REAL
    # Lo confirmado, leído por otra conexión: nada de nada.
    foto = leer_u(SQL_FOTO_U, {"op": op_todo})
    comprobar("N9: ni dirección, ni resultado, ni visita, ni log; sigue en 'pendiente_aprobacion'",
              foto == (0, 0, 0, 0, "pendiente_aprobacion"), f"({foto})")

    # H2 por la vía real (con su rollback): nada escrito.
    op_h2b = gate_u()
    # Un cuerpo válido y, después, una dirección que el CHECK rechaza (CP 53000).
    data = ResultadoLlamadaCreate(oportunidad_id=op_h2b, resultado="visita_acordada", informe="prueba H2",
                                  fecha=str(D_H2B), hora="11:00", direccion_obra=DIR_A)
    data.direccion_obra = DireccionObra.model_construct(calle_numero=f"Calle {MARCA} 3", codigo_postal="53000",
                                                        municipio="Municipio de Prueba")
    try:
        servicio.registrar_resultado(data)
    # La esperada, sin la marca.
    except servicio.DireccionObraNoGuardada as error:
        comprobar("H2 (commit real): DireccionObraNoGuardada sin la dirección y con chk_direcciones_obra_codigo_postal",
                  MARCA not in str(error) and "chk_direcciones_obra_codigo_postal" in str(error))
    # Otra: FALLO, sin su texto.
    except Exception as error:
        comprobar("H2 (commit real): DireccionObraNoGuardada", False, f"(salió {type(error).__name__})")
    # Ninguna: FALLO explícito.
    else:
        comprobar("H2 (commit real): DireccionObraNoGuardada", False, "(la excepción se ha tragado)")
    foto = leer_u(SQL_FOTO_U, {"op": op_h2b})
    comprobar("H2 (commit real): nada escrito (ni dirección, ni resultado, ni visita, ni log)",
              foto == (0, 0, 0, 0, "pendiente_aprobacion"), f"({foto})")
    # La marca, en ningún log escrito desde que empezó S4.
    # SQL: logs posteriores a la base con la marca en su detalle.
    con_marca = leer_u("SELECT count(*) FROM logs WHERE id > %s AND detalle::text LIKE %s;",
                       (LOGS_BASE, f"%{MARCA}%"))[0]
    comprobar("S4: la marca aparece 0 veces en logs.detalle", con_marca == 0, f"({con_marca})")
finally:
    # Siempre: la función real, el pool cerrado y lo propio borrado.
    servicio.fin_de_visita = FIN_REAL
    db.close_pool()
    limpiar_u()
    # SQL: los clientes con la marca que queden (tienen que ser 0).
    restos = leer_u("SELECT count(*) FROM clientes WHERE email LIKE %s;", (PATRON_U,))[0]
    comprobar("S4: no queda ningún dato propio (marca check-direccion-svc-u-)", restos == 0, f"({restos})")
    comprobar("fin_de_visita restaurada en memoria", servicio.fin_de_visita is FIN_REAL)
    cn_u.close()

# Resumen y código de salida (1 si hubo fallos; lo mira la suite).
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con fallos, se listan y el código de salida es 1.
if fallos:
    print("FALLOS:")
    # Uno por línea (los títulos nunca llevan una dirección).
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
